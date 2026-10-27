"""提醒纯函数 + 通知 claim-then-send 状态机（设计 §8）。

状态机：UNIQUE(dedupe_key, channel) 先 claim（pending）再发送 → sent/failed；
补发护栏：同作业错过多节点只发最近 1 个；overdue 双护栏；pending>5min 重claim；attempts>=3 停。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlmodel import select

from .models import Assignment, NotificationLog
from .normalize import format_local, now_utc, remaining_text
from .redact import redact

if TYPE_CHECKING:
    from .runtime import Container

T12H = 720  # priority=1 时前插的档位（minutes_before）


def offsets_for(a: Assignment, defaults) -> list[int]:
    """生效档位：单作业覆盖 > 默认（priority=1 前插 T-12h）。minutes_before 正数，大→小。

    reminder_offsets_json 语义：None=未覆盖（用默认）；"[]"=明确不提醒；"[...]"]=覆盖档位。
    """
    if a.reminder_offsets_json is not None:
        try:
            vals = [int(x) for x in json.loads(a.reminder_offsets_json)]
        except (ValueError, TypeError):
            vals = None
        if vals is not None:
            return sorted(set(vals), reverse=True)
    offs = set(defaults)
    if a.priority and T12H not in offs:
        offs.add(T12H)
    return sorted(offs, reverse=True)


def effective_done(a: Assignment) -> bool:
    """平台已提交 或 本地标记完成，都算完成（设计 §8.1）。"""
    return bool(a.local_done or a.status == "submitted")


def in_scope(a: Assignment) -> bool:
    return (
        a.due_at is not None
        and not a.local_ignored
        and not effective_done(a)
    )


def due_nodes(
    a: Assignment, defaults, now: datetime | None = None
) -> list[tuple[int, datetime]]:
    """已到期未发送的提醒节点 [(minutes_before, fire_time)]，离截止最近在前。"""
    if not in_scope(a):
        return []
    now = now or now_utc()
    nodes: list[tuple[int, datetime]] = []
    for mb in offsets_for(a, defaults):
        fire = a.due_at - timedelta(minutes=mb)
        if now >= fire:
            nodes.append((mb, fire))
    nodes.sort()  # minutes_before 升序 = 离截止最近在前
    return nodes


def overdue_node(a: Assignment, now: datetime, max_past_days: int = 7) -> int | None:
    """overdue 双护栏：提前见过（first_seen_at < due_at）且逾期 <= 7 天才提醒一次。"""
    if a.due_at is None or a.due_at >= now:
        return None
    if a.local_ignored or effective_done(a):
        return None
    if a.first_seen_at >= a.due_at:
        return None  # 首次同步导入的历史作业，不发逾期
    if (now - a.due_at).total_seconds() > max_past_days * 86400:
        return None
    return -1  # 特殊 minutes_before 标记


_KIND_LABEL = {"reminder": "作业提醒", "change": "DDL 变更", "overdue": "作业逾期", "test": "测试通知"}


def dispatch_notification(
    container: "Container",
    *,
    kind: str,
    dedupe_key: str,
    assignment_id: int | None = None,
    minutes_before: int | None = None,
    summary: str = "",
    url: str | None = None,
) -> int:
    """claim-then-send。返回本次成功发送的渠道数。并发/重复调用由 UNIQUE 约束挡住。"""
    now = now_utc()
    channels = container.active_channels()
    claimed: list[tuple[int, str, object]] = []
    with container.db.session() as s:
        for name, notifier in channels.items():
            dup = s.exec(
                select(NotificationLog).where(
                    NotificationLog.dedupe_key == dedupe_key,
                    NotificationLog.channel == name,
                )
            ).first()
            if dup:
                continue
            row = NotificationLog(
                assignment_id=assignment_id,
                channel=name,
                kind=kind,
                minutes_before=minutes_before,
                dedupe_key=dedupe_key,
                status="pending",
                summary=summary[:1000],
                created_at=now,
            )
            s.add(row)
            s.flush()
            claimed.append((row.id, name, notifier))
        s.commit()

    sent = 0
    if claimed:
        title = f"DDLHub：{_KIND_LABEL.get(kind, kind)}"
        with container.db.session() as s:
            for rid, name, notifier in claimed:
                row = s.get(NotificationLog, rid)
                result = notifier.send(title, summary, url)
                row.attempts += 1
                row.status = "sent" if result.ok else "failed"
                row.error = result.error
                row.sent_at = now_utc() if result.ok else None
                if result.ok:
                    sent += 1
                else:
                    container.logger.warning(
                        "notification %s via %s failed: %s", rid, name, result.error
                    )
            s.commit()
    return sent


def requeue_stale(container: "Container", older_than_minutes: int = 5, max_attempts: int = 3) -> int:
    """崩溃残留 pending（>5min）与 failed（attempts<3）重试发送。"""
    now = now_utc()
    cutoff = now - timedelta(minutes=older_than_minutes)
    channels = container.active_channels()
    if not channels:
        return 0
    sent = 0
    with container.db.session() as s:
        rows = s.exec(
            select(NotificationLog).where(
                NotificationLog.status.in_(["pending", "failed"]),
                NotificationLog.attempts < max_attempts,
                NotificationLog.created_at < cutoff,
            )
        ).all()
        for row in rows:
            notifier = channels.get(row.channel)
            if notifier is None:
                continue  # 渠道当前不可用，留给下轮
            result = notifier.send(f"DDLHub：{_KIND_LABEL.get(row.kind, row.kind)}", row.summary)
            row.attempts += 1
            row.status = "sent" if result.ok else "failed"
            row.error = result.error
            row.sent_at = now_utc() if result.ok else None
            if result.ok:
                sent += 1
        s.commit()
    return sent


def _mark_skipped(container: "Container", a: Assignment, nodes: list[tuple[int, object]]) -> None:
    """补发护栏：错过的较早节点静默标记 skipped（UNIQUE 冲突=已处理过，跳过）。"""
    channels = container.active_channels()
    now = now_utc()
    with container.db.session() as s:
        for mb, _fire in nodes:
            for name in channels:
                row = NotificationLog(
                    assignment_id=a.id, channel=name, kind="reminder",
                    minutes_before=mb,
                    dedupe_key=f"a{a.id}:reminder:b{mb}",
                    status="skipped", summary=f"错过未补发（仅补最近节点）：{a.title}",
                    created_at=now,
                )
                s.add(row)
        try:
            s.commit()
        except Exception:
            s.rollback()


def run_reminder_tick(container: "Container") -> int:
    """每分钟 tick（调度器调用）：到期提醒 + 逾期提醒。返回本次发送数。

    - 补发护栏（P0-4）：同作业多个到期节点只发最近一个，更早的置 skipped
    - overdue 双护栏：first_seen_at < due_at 且逾期 <=7 天，只发一次
    - 完成即停：in_scope() 已排除 local_done / submitted / ignored / 无截止
    - 状态机：UNIQUE(dedupe_key, channel) claim → 重启后计划自然恢复（验收项）
    """
    now = now_utc()
    defaults = container.settings.reminder_offsets
    channels = container.active_channels()
    if not channels:
        return 0
    with container.db.session() as s:
        candidates = s.exec(
            select(Assignment).where(
                Assignment.due_at.is_not(None),
                Assignment.local_ignored == False,  # noqa: E712
                Assignment.local_done == False,  # noqa: E712
                Assignment.status != "submitted",
            )
        ).all()
    sent = 0
    for a in candidates:
        # 已逾期：只走 overdue（双护栏），不再补发任何 T-x 节点
        if a.due_at <= now:
            if overdue_node(a, now) is not None:
                summary = (f"「{a.title}」已逾期（截止 {format_local(a.due_at, container.settings.timezone)}），"
                           f"如已完成请到时间线标记")
                sent += dispatch_notification(
                    container, kind="overdue", dedupe_key=f"a{a.id}:overdue",
                    assignment_id=a.id, summary=summary, url=a.source_url,
                )
            continue

        nodes = due_nodes(a, defaults, now)
        if not nodes:
            continue
        latest_mb = nodes[0][0]  # minutes_before 最小 = 离截止最近
        _mark_skipped(container, a, nodes[1:])
        remaining = a.due_at - now
        summary = (f"「{a.title}」"
                   f"{remaining_text(a.due_at, now)}，"
                   f"截止 {format_local(a.due_at, container.settings.timezone)}")
        sent += dispatch_notification(
            container, kind="reminder", dedupe_key=f"a{a.id}:reminder:b{latest_mb}",
            assignment_id=a.id, minutes_before=latest_mb,
            summary=summary, url=a.source_url,
        )
    if sent:
        container.logger.info("reminder tick: sent %d notification(s)", sent)
    return sent
