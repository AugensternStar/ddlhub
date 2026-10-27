"""时间归一化：naive→UTC、aware 保留、剩余时间、档位文案。"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ddlhub.core.normalize import (
    format_local,
    human_offset,
    is_overdue,
    remaining_text,
    to_utc,
)

SH = ZoneInfo("Asia/Shanghai")


def test_naive_shanghai_to_utc():
    # 上海 08:00 = UTC 00:00（+8h）
    naive = datetime(2026, 3, 1, 8, 0)
    assert to_utc(naive, "Asia/Shanghai") == datetime(2026, 3, 1, 0, 0)


def test_aware_converted_and_stripped():
    aware = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    assert to_utc(aware) == datetime(2026, 3, 1, 0, 0)
    other = datetime(2026, 3, 1, 12, 0, tzinfo=SH)
    assert to_utc(other) == datetime(2026, 3, 1, 4, 0)


def test_none_passthrough():
    assert to_utc(None) is None


def test_remaining_text():
    now = datetime(2026, 3, 1, 0, 0)
    due = now + timedelta(days=2, hours=3)
    assert remaining_text(due, now) == "剩余 2天3小时"
    assert remaining_text(now + timedelta(hours=5), now) == "剩余 5小时0分钟"
    assert remaining_text(now - timedelta(days=1), now).startswith("已逾期")
    assert remaining_text(None) == "无截止时间"


def test_is_overdue():
    now = datetime(2026, 3, 1, 0, 0)
    assert is_overdue(now - timedelta(minutes=1), now)
    assert not is_overdue(now + timedelta(minutes=1), now)
    assert not is_overdue(None, now)


def test_format_local():
    due = datetime(2026, 3, 1, 0, 30)  # UTC → 上海 08:30
    assert format_local(due, "Asia/Shanghai") == "03-01 08:30"
    assert format_local(None) == "—"


def test_human_offset():
    assert human_offset(10080) == "T-7d"
    assert human_offset(1440) == "T-1d"
    assert human_offset(4470) == "T-3d2h"
    assert human_offset(90) == "T-1h30m"
    assert human_offset(45) == "T-45m"
