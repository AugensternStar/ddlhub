"""同步编排：每账号互斥锁（P0-7）、自动触发限频（红线 ≥10min，手动豁免）、
指数退避重试、单账号失败隔离（设计 §7）。

事务策略：全程短事务、不嵌套——
  阶段1 建 sync_run → 阶段2 适配器抓取+upsert（各自独立事务，不持库锁）
  → 变更通知统一 claim → 阶段3 落 run 结果。任何一步失败不影响其他账号。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlmodel import select
from sqlalchemy import func
from tenacity import Retrying, stop_after_attempt, wait_exponential

from ..adapters.registry import REGISTRY
from .db import Database
from .models import Account, Assignment, SyncRun
from .normalize import now_utc
from .redact import redact
from .reminders import send_pending
from .upsert import upsert_assignments, upsert_courses

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _account_lock(account_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(account_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[account_id] = lock
        return lock


@dataclass
class SyncOutcome:
    ok: bool
    message: str
    run_id: int | None = None


def sync_account(container, account_id: int, trigger: str) -> SyncOutcome:
    """每账号一把进程内锁：手动同步撞上 cron 立即返回，不排队不并行。"""
    lock = _account_lock(account_id)
    if not lock.acquire(blocking=False):
        return SyncOutcome(False, "该账号正在同步中，请稍候")
    try:
        return _sync_locked(container, account_id, trigger)
    finally:
        lock.release()


def sync_all(container, trigger: str) -> list[SyncOutcome]:
    with container.db.session() as s:
        accounts = s.exec(select(Account).where(Account.status != "disabled")).all()
        targets = [(a.id, a.label) for a in accounts]
    outcomes: list[SyncOutcome] = []
    for aid, label in targets:
        try:
            outcomes.append(sync_account(container, aid, trigger))
        except Exception as exc:
            container.logger.exception("sync_all: account %s(%s) crashed", aid, label)
            outcomes.append(SyncOutcome(False, f"{label}: {type(exc).__name__}（已隔离，不影响其他平台）"))
    return outcomes


def _retry_call(fn, *args, **kwargs):
    """指数退避：1s→2s→4s，共 3 次后放弃（失败向上抛，由调用方记录）。"""
    for attempt in Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    ):
        with attempt:
            return fn(*args, **kwargs)


def _finish_run(
    db: Database,
    run_id: int,
    account_id: int,
    *,
    status: str,
    error: str = "",
    ok_count: int = 0,
    fail_count: int = 0,
) -> None:
    with db.session() as s:
        run = s.get(SyncRun, run_id)
        if run is not None:
            run.status = status
            run.error_summary = error[:2000]
            run.ok_count = ok_count
            run.fail_count = fail_count
            run.finished_at = now_utc()
        account = s.get(Account, account_id)
        if account is not None:
            if status == "need_relogin":
                account.status = "need_relogin"
            elif status in ("success", "partial"):
                account.status = "ok"
                account.last_ok_at = now_utc()
                # R-P2-9：全部课程抓取失败（partial 且 0 成功）→ 界面可见，不再一路绿灯
                if status == "partial" and fail_count > 0 and ok_count == 0:
                    account.last_error = f"全部课程抓取失败：{error[:200]}"
                else:
                    account.last_error = ""
            elif status == "failed":
                account.last_error = error[:2000]
        s.commit()


def _sync_locked(container, account_id: int, trigger: str) -> SyncOutcome:
    db = container.db
    now = now_utc()

    # ---- 阶段1：加载账号 + 限频检查 + 建 run（短事务）----
    with db.session() as s:
        account = s.get(Account, account_id)
        if account is None or account.status == "disabled":
            return SyncOutcome(False, "账号不存在或已停用")
        adapter_key = account.adapter_key
        label = account.label

        # 自动触发限频：距上次【实际】同步不足下限则跳过（红线；手动同步豁免）。
        # R-P2-2：基准排除 skipped 行——被跳过的班次不推长限频窗口
        if trigger in ("cron", "startup"):
            last = s.exec(
                select(SyncRun)
                .where(SyncRun.account_id == account_id,
                       SyncRun.status != "skipped")
                .order_by(SyncRun.started_at.desc())
            ).first()
            if (
                last is not None
                and last.started_at is not None
                and (now - last.started_at).total_seconds()
                < container.settings.min_interval_min * 60
            ):
                run = SyncRun(
                    account_id=account_id, trigger=trigger, status="skipped",
                    started_at=now, finished_at=now_utc(),
                    error_summary="距上次同步不足下限，跳过（红线 ≥10min）",
                )
                s.add(run)
                s.commit()
                return SyncOutcome(True, "距上次同步不足下限，已跳过", run.id)

        # R-P2-1：先落 running——进程中断时不会留下"假 success"
        run = SyncRun(account_id=account_id, trigger=trigger, status="running", started_at=now)
        s.add(run)
        s.commit()
        run_id = run.id

    with db.session() as s:
        prior_success = (
            s.exec(
                select(SyncRun).where(
                    SyncRun.account_id == account_id,
                    SyncRun.status.in_(["success", "partial"]),
                    SyncRun.id != run_id,
                )
            ).first()
            is not None
        )

    # ---- 阶段2：适配器工作（不持有任何库事务）----
    adapter_cls = REGISTRY.get(adapter_key)
    if adapter_cls is None:
        _finish_run(db, run_id, account_id, status="failed", error=f"未知适配器: {adapter_key}")
        return SyncOutcome(False, f"未知适配器: {adapter_key}", run_id)

    ctx = container.adapter_ctx(account_id)  # 含加密会话存取，勿手工构造
    adapter = adapter_cls(ctx)
    ok = 0
    failed = 0
    run_status = "success"
    error_summary = ""
    try:
        health = _retry_call(adapter.health_check)
        if health.need_relogin:
            _finish_run(db, run_id, account_id, status="need_relogin", error=health.detail)
            container.logger.info("account %s 需要重新登录", label)
            return SyncOutcome(False, "需要重新登录（状态栏可一键恢复）", run_id)
        if not health.ok:
            raise RuntimeError(f"健康检查失败: {health.detail}")

        course_drafts = _retry_call(adapter.fetch_courses)
        course_map = upsert_courses(db, account_id, course_drafts)

        draft_by_source = {d.source_id: d for d in course_drafts}
        errors: list[str] = []
        for csrc, ref in course_map.items():
            if not ref.enabled:
                continue  # 课程选择：用户关闭的课程不抓取（设置页开关）
            course_draft = draft_by_source.get(csrc)
            if course_draft is None:
                continue
            try:
                # R-P1-4：解析到 0 条而库里此前有数据 → 疑似页面改版，显式告警而非静默成功
                with db.session() as s:
                    existing = s.exec(
                        select(func.count()).select_from(Assignment).where(
                            Assignment.course_id == ref.id)
                    ).one()
                drafts = _retry_call(adapter.fetch_assignments, course_draft)
                stats = upsert_assignments(
                    db, account_id, ref.id, csrc, drafts,
                    container.settings.timezone,
                    notify_new=prior_success,
                    channel_names=list(container.active_channels().keys()),
                )
                ok += stats.total
                if stats.total == 0 and existing > 0:
                    failed += 1
                    msg = f"{csrc}: 解析到 0 条（此前 {existing} 条），疑似页面改版或会话异常"
                    errors.append(msg)
                    container.logger.warning(msg)
            except Exception as exc:
                failed += 1
                errors.append(f"{csrc}: {type(exc).__name__}")
                container.logger.warning(
                    "fetch_assignments failed for %s: %s", csrc, redact(str(exc))
                )
        run_status = "success" if failed == 0 else "partial"
        error_summary = "; ".join(errors)
    except Exception as exc:
        error_summary = redact(f"{type(exc).__name__}: {exc}")
        _finish_run(db, run_id, account_id, status="failed", error=error_summary)
        container.logger.exception("sync account %s failed", label)
        return SyncOutcome(False, f"同步失败：{error_summary}", run_id)
    finally:
        adapter.close()

    # ---- 变更通知：outbox 已随 upsert 事务落 pending，这里统一发送 ----
    send_pending(container)

    # ---- 阶段3：落 run 结果与账号状态 ----
    _finish_run(
        db, run_id, account_id,
        status=run_status, error=error_summary, ok_count=ok, fail_count=failed,
    )
    msg = f"同步完成：{ok} 项作业" + (f"，{failed} 门课程失败" if failed else "")
    container.logger.info("account %s: %s", label, msg)
    return SyncOutcome(True, msg, run_id)
