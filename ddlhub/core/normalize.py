"""时间归一化与展示。

落库约定：一律 naive UTC（naive 输入按配置时区解释，aware 输入直接转换）。
展示约定：读出后按配置时区转本地。改变 DDLHUB_TIMEZONE 不迁移历史数据（设计 §14.7）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


@lru_cache(maxsize=8)
def _tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def to_utc(dt: datetime | None, tz_name: str = "Asia/Shanghai") -> datetime | None:
    """任意输入 → naive UTC（落库口径）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz(tz_name))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def from_utc(dt: datetime | None, tz_name: str = "Asia/Shanghai") -> datetime | None:
    """naive UTC → 本地时区 aware（展示口径）。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(_tz(tz_name))


def now_utc() -> datetime:
    """当前时刻，naive UTC（与落库口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def remaining_text(due_utc: datetime | None, now: datetime | None = None) -> str:
    if due_utc is None:
        return "无截止时间"
    now = now or now_utc()
    delta = due_utc - now
    past = delta.total_seconds() < 0
    secs = abs(int(delta.total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days >= 1:
        text = f"{days}天{hours}小时"
    elif hours >= 1:
        text = f"{hours}小时{minutes}分钟"
    else:
        text = f"{minutes}分钟"
    return f"已逾期 {text}" if past else f"剩余 {text}"


def is_overdue(due_utc: datetime | None, now: datetime | None = None) -> bool:
    if due_utc is None:
        return False
    return due_utc < (now or now_utc())


def format_local(due_utc: datetime | None, tz_name: str = "Asia/Shanghai") -> str:
    local = from_utc(due_utc, tz_name)
    return local.strftime("%m-%d %H:%M") if local else "—"


def format_date_local(due_utc: datetime | None, tz_name: str = "Asia/Shanghai") -> str:
    local = from_utc(due_utc, tz_name)
    return local.strftime("%Y-%m-%d") if local else "无截止"


def human_offset(minutes_before: int) -> str:
    """10080→T-7d；4470→T-3d2h；90→T-1h30m；45→T-45m。"""
    days, rem = divmod(minutes_before, 1440)
    hours, mins = divmod(rem, 60)
    if days and hours:
        return f"T-{days}d{hours}h"
    if days:
        return f"T-{days}d"
    if hours and mins:
        return f"T-{hours}h{mins}m"
    if hours:
        return f"T-{hours}h"
    return f"T-{mins}m"
