"""配置解析容错：非法环境变量回退默认并告警，不让启动崩溃。"""
from ddlhub.config import Settings


def test_bad_env_values_fall_back(monkeypatch):
    monkeypatch.setenv("DDLHUB_PORT", "not-a-number")
    monkeypatch.setenv("DDLHUB_MIN_INTERVAL_MIN", "abc")
    monkeypatch.setenv("DDLHUB_REMINDER_OFFSETS", "7天,3天")   # 非法格式
    monkeypatch.setenv("DDLHUB_SMTP_PORT", "465x")
    s = Settings.load(base_dir=None)
    assert s.port == 8787
    assert s.min_interval_min == 10
    assert s.reminder_offsets == (10080, 4320, 1440, 360, 60)
    assert s.smtp_port == 465


def test_valid_env_values_respected(monkeypatch):
    monkeypatch.setenv("DDLHUB_PORT", "9000")
    monkeypatch.setenv("DDLHUB_REMINDER_OFFSETS", "60,30")
    monkeypatch.setenv("DDLHUB_MIN_INTERVAL_MIN", "5")   # 低于红线会被钳到 10
    s = Settings.load(base_dir=None)
    assert s.port == 9000
    assert s.reminder_offsets == (60, 30)
    assert s.min_interval_min == 10


def test_sync_times_empty_falls_back(monkeypatch):
    monkeypatch.setenv("DDLHUB_SYNC_TIMES", ",,")
    s = Settings.load(base_dir=None)
    assert s.sync_times == ("08:00", "21:00")
