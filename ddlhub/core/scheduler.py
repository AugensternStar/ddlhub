"""APScheduler：cron 同步 + 启动补漏 + 通知重试 + 30 天保留清理。

所有作业 max_instances=1 / coalesce=True / misfire_grace_time=3600
（P0-7：Windows 休眠唤醒后错过的 cron 只补跑一次，不集中爆发）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from apscheduler.schedulers.background import BackgroundScheduler

from .models import NotificationLog, SyncRun
from .normalize import now_utc
from .sync import sync_all

logger = logging.getLogger("ddlhub.scheduler")


def start_scheduler(container) -> BackgroundScheduler:
    settings = container.settings
    sched = BackgroundScheduler(timezone=settings.timezone)

    for i, t in enumerate(settings.sync_times):
        try:
            hh, mm = (int(x) for x in t.split(":"))
        except ValueError:
            logger.warning("非法同步时刻 %r，忽略", t)
            continue
        sched.add_job(
            _sync_job, "cron", hour=hh, minute=mm, args=[container, "cron"],
            id=f"sync-cron-{i}", max_instances=1, coalesce=True, misfire_grace_time=3600,
        )

    sched.add_job(
        _sync_job, "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=3),
        args=[container, "startup"], id="sync-startup",
    )
    # 提醒引擎：每分钟 tick（无状态计算 + notification_log 去重 → 重启后计划自然恢复）
    sched.add_job(
        _reminder_job, "interval", minutes=1, args=[container],
        id="reminder-tick", max_instances=1, coalesce=True,
    )
    sched.add_job(
        _requeue_job, "interval", minutes=5, args=[container],
        id="notif-requeue", max_instances=1, coalesce=True,
    )
    sched.add_job(
        _cleanup_job, "cron", hour=3, minute=30, args=[container],
        id="retention", max_instances=1, coalesce=True, misfire_grace_time=3600,
    )

    sweep_stale_runs(container)
    sched.start()
    logger.info(
        "调度器已启动：每日同步 %s，启动补漏 3s 后执行，通知重试每 5 分钟",
        ",".join(settings.sync_times),
    )
    return sched


def sweep_stale_runs(container) -> None:
    """R-P2-1 收尾：上次进程中断留下的 running → failed（避免假 success 与群发误判）。"""
    from sqlalchemy import update as _sa_update

    try:
        with container.db.session() as s:
            s.execute(
                _sa_update(SyncRun)
                .where(SyncRun.status == "running")
                .values(status="failed", finished_at=now_utc(),
                        error_summary="进程中断（启动清扫）")
            )
            s.commit()
    except Exception:
        logger.exception("stale running sweep failed")


def _sync_job(container, trigger: str) -> None:
    try:
        sync_all(container, trigger)
    except Exception:
        logger.exception("scheduled sync (%s) crashed", trigger)


def _reminder_job(container) -> None:
    try:
        from .reminders import run_reminder_tick

        run_reminder_tick(container)
    except Exception:
        logger.exception("reminder tick crashed")


def _requeue_job(container) -> None:
    try:
        from .reminders import requeue_stale

        requeue_stale(container)
    except Exception:
        logger.exception("notification requeue crashed")


def _cleanup_job(container) -> None:
    try:
        cutoff = now_utc() - timedelta(days=30)
        with container.db.session() as s:
            s.execute(delete(SyncRun).where(SyncRun.started_at < cutoff))  # type: ignore[arg-type]
            s.execute(delete(NotificationLog).where(NotificationLog.created_at < cutoff))  # type: ignore[arg-type]
            s.commit()
    except Exception:
        logger.exception("retention cleanup crashed")
