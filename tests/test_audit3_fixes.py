"""第三轮审计修复回归：vault 持久化、custom hints、token 链路、同日改期、
迁移完整性、原子领取、pin 幂等、未来7天下界、配置校验、删除顺序。
"""
import base64
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from ddlhub.adapters.base import AssignmentDraft, CourseDraft
from ddlhub.adapters.custom import CustomAdapter
from ddlhub.core.db import Database
from ddlhub.core.kv import get_setting, set_setting
from ddlhub.core.models import Account, Assignment, Course, NotificationLog
from ddlhub.core.normalize import now_utc
from ddlhub.core.reminders import run_reminder_tick, send_pending
from ddlhub.core.security import VaultStore
from ddlhub.core.sync import sync_account
from ddlhub.core.upsert import upsert_assignments, upsert_courses
from ddlhub.web.main import create_app
from tests.test_harden import lan_container  # noqa: F401


# ---------- 01：vault 增删即时持久化 ----------


def test_vault_store_persists_across_reopen(tmp_path, monkeypatch):
    db = Database(tmp_path)
    prompts = iter(["master-pw", "master-pw"])

    class _S:
        vault_mode = True

    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))
    from ddlhub.__main__ import _credential_store

    store = _credential_store(_S(), db)
    store.store("xuexitong", "uuid-A", "18800000000", "cred-A")  # 创建 vault 之后新增

    reopened = VaultStore.open("master-pw", base64.b64decode(get_setting(db, "vault", None)))
    assert reopened.load("xuexitong", "uuid-A") == ("18800000000", "cred-A")


def test_vault_delete_persists_across_reopen(tmp_path, monkeypatch):
    db = Database(tmp_path)
    prompts = iter(["master-pw", "master-pw"])

    class _S:
        vault_mode = True

    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))
    from ddlhub.__main__ import _credential_store

    store = _credential_store(_S(), db)
    store.store("xuexitong", "uuid-B", "u1", "p1")
    store.delete("xuexitong", "uuid-B")

    reopened = VaultStore.open("master-pw", base64.b64decode(get_setting(db, "vault", None)))
    assert reopened.load("xuexitong", "uuid-B") is None


def test_vault_persist_failure_propagates(tmp_path):
    db = Database(tmp_path)
    store = VaultStore.create("master-pw")

    def boom(blob):
        raise RuntimeError("disk full")

    store.set_persist_callback(boom)
    with pytest.raises(RuntimeError, match="disk full"):
        store.store("x", "u", "user", "pw")  # 写失败不得报告成功


# ---------- 03：custom hints 从配置生效 ----------


class FakeBrowserContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class FakePage:
    def __init__(self, url):
        self.url = url


def test_custom_hints_from_config(container, mock_account):
    adapter = CustomAdapter(container.adapter_ctx(mock_account))
    adapter.ctx.config = {
        "entry_url": "https://portal.example.edu/",
        "cookie_domain": "example.edu",
        "login_cookie_hints": ["mysess"],
    }
    ok_ctx = FakeBrowserContext([
        {"name": "anon", "domain": ".example.edu", "value": "1"},
        {"name": "mysess", "domain": ".example.edu", "value": "t"},
    ])
    # 提示 cookie 命中 → 成功（修复前 AttributeError → 恒 False）
    assert adapter.login_success(ok_ctx, FakePage("https://portal.example.edu/home"), {"anon"}) is True
    # 无任何会话 cookie → False
    assert adapter.login_success(
        FakeBrowserContext([{"name": "anon", "domain": ".example.edu", "value": "1"}]),
        FakePage("https://portal.example.edu/home"), {"anon"},
    ) is False


# ---------- 04：/?token= 完整链路（重定向保留 query） ----------


def test_lan_root_token_full_chain(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    # 从实际对外公布的链接开始（README/横幅给出的 /?token= 形式）
    r = client.get("/?token=test-token-123", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login")
    r2 = client.get(r.headers["location"], follow_redirects=False)
    assert r2.status_code == 302 and r2.headers["location"] == "/"
    assert "ddlhub_session" in client.cookies
    assert client.get("/", follow_redirects=False).status_code == 200
    # 浏览器地址栏最终不含 token
    assert "token" not in str(client.get("/").request.url)


# ---------- 05：同日改期产生新提醒 ----------


def test_same_day_postpone_fires_new_reminder(container, mock_account, monkeypatch):
    db = container.db
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = upsert_courses(db, acc.id, [CourseDraft(source_id="C1", name="课程一")])["C1"].id
    from zoneinfo import ZoneInfo

    SH = ZoneInfo("Asia/Shanghai")
    due = datetime.now(SH) + timedelta(hours=2)  # aware——适配器产出即此形态
    upsert_assignments(db, acc.id, cid, "C1", [AssignmentDraft(
        source_id="A1", course_source_id="C1", title="作业A", due_at=due,
    )], "Asia/Shanghai", notify_new=False)

    assert run_reminder_tick(container) == 1  # 首次 T-6h
    assert run_reminder_tick(container) == 0  # 无变化重跑不重复

    # 老师把截止延后 1 小时（同一天）
    with db.session() as s:
        row = s.exec(select(Assignment).where(Assignment.source_id == "C1::A1")).one()
        row.due_at = row.due_at + timedelta(hours=1)
        s.commit()

    assert run_reminder_tick(container) == 1  # 修复前：0（同键被吞）
    assert run_reminder_tick(container) == 0  # 新键已记录


# ---------- 06：迁移 004 外键与孤儿清理（升级路径） ----------


def _copy_migrations(tmp_path, names):
    import shutil

    real = Database.__init__  # noqa: F401  仅借路径常量
    from ddlhub.core.db import _MIGRATIONS_DIR

    dst = tmp_path / "migrations"
    dst.mkdir(exist_ok=True)
    for n in names:
        shutil.copy(_MIGRATIONS_DIR / n, dst / n)
    return dst


def test_migration_004_adds_fk_and_cleans_orphans(tmp_path):
    partial = _copy_migrations(tmp_path, ["001_init.sql", "002_course_enabled.sql",
                                          "003_account_config.sql"])
    db = Database(tmp_path / "data", migrations_dir=partial)
    with db.session() as s:
        acc = Account(uuid="u", adapter_key="mock", label="t", created_at=now_utc())
        s.add(acc)
        s.flush()
        co = Course(account_id=acc.id, source_id="C1", name="课程")
        s.add(co)
        s.flush()
        a = Assignment(account_id=acc.id, course_id=co.id, source_id="C1::A", title="t",
                       first_seen_at=now_utc(), updated_at=now_utc())
        s.add(a)
        s.commit()
        aid = a.id
        # 孤儿：pending（应删）、sent（应置 NULL）——用 raw SQL（旧表无 claimed_at）
        import sqlite3 as _sq

        raw = _sq.connect(tmp_path / "data" / "ddlhub.db")
        for st in ("pending", "sent"):
            raw.execute(
                "INSERT INTO notification_log (assignment_id, channel, kind, dedupe_key,"
                " status, created_at) VALUES (?, 'desktop', 'reminder', ?, ?, datetime('now'))",
                (aid + 100, f"orphan-{st}", st))
        raw.commit()
        raw.close()

    # 用完整迁移目录重开 → 004 自动应用
    db2 = Database(tmp_path / "data")
    from sqlalchemy import inspect as _insp

    fks = _insp(db2.engine).get_foreign_keys("notification_log")
    assert any(f.get("referred_table") == "assignment" for f in fks)
    with db2.session() as s:
        orphan = s.exec(select(NotificationLog).where(
            NotificationLog.dedupe_key == "orphan-sent")).one()
        assert orphan.assignment_id is None          # sent 孤儿置 NULL
        assert s.exec(select(NotificationLog).where(
            NotificationLog.dedupe_key == "orphan-pending")).first() is None  # 待发孤儿删除
        assert s.get(Assignment, aid) is not None    # 正常数据不受影响


def test_migration_failure_rolls_back_and_backs_up(tmp_path):
    partial = _copy_migrations(tmp_path, ["001_init.sql", "002_course_enabled.sql",
                                          "003_account_config.sql",
                                          "004_notification_integrity.sql"])
    (partial / "005_bad.sql").write_text(
        "CREATE TABLE ok_table(id INTEGER);\nINSERT INTO no_such_table VALUES (1);\n",
        encoding="utf-8")
    with pytest.raises(SystemExit, match="回滚"):
        Database(tmp_path / "data", migrations_dir=partial)
    # 半成品不存在 + 版本未记录 + 备份已生成
    import sqlite3

    raw = sqlite3.connect(tmp_path / "data" / "ddlhub.db")
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables
    versions = {r[0] for r in raw.execute("SELECT version FROM schema_version")}
    assert 5 not in versions
    raw.close()
    assert list((tmp_path / "data" / "backups").glob("pre-migration-*.db"))


# ---------- 07：并发消费不重复发送 ----------


def test_concurrent_send_single_delivery(container, mock_account, fake_notifier, monkeypatch):
    from ddlhub.core.reminders import claim_notification

    ids = claim_notification(container, kind="reminder", dedupe_key="conc:1",
                             summary="并发测试")
    assert len(ids) == 1

    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        results.append(send_pending(container, ids))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1                      # 只有一个消费者投递
    assert len(fake_notifier.calls) == 1          # 渠道只被调用一次


# ---------- 09/11：删除顺序与 pin 幂等 ----------


class _BrokenKeyringStore:
    def store(self, *a):
        pass

    def load(self, *a):
        return None

    def delete(self, *a):
        raise RuntimeError("credential vault busy")


def test_delete_account_keyring_failure_aborts(container, mock_account):
    container.credential_store = _BrokenKeyringStore()
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")
    r = client.post("/api/accounts/1/delete", follow_redirects=False)
    from urllib.parse import unquote

    assert "失败" in unquote(r.headers["location"])
    with container.db.session() as s:
        assert s.get(Account, 1) is not None  # 账号保留，可重试


def test_delete_account_removes_pending_notifications(container, mock_account):
    from ddlhub.core.reminders import claim_notification

    sync_account(container, mock_account, "manual")
    with container.db.session() as s:
        a = s.exec(select(Assignment)).first()
        aid = a.id
    claim_notification(container, kind="reminder",
                       dedupe_key=f"a{aid}:reminder:b60:test", assignment_id=aid,
                       summary="待发")
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")
    client.post(f"/api/accounts/{mock_account}/delete", follow_redirects=False)
    with container.db.session() as s:
        assert s.exec(select(NotificationLog).where(
            NotificationLog.assignment_id == aid)).first() is None  # 待发通知随账号清除


def test_pin_idempotent(container, mock_account):
    sync_account(container, mock_account, "manual")
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")
    with container.db.session() as s:
        aid = s.exec(select(Assignment)).first().id
    client.post(f"/api/assignments/{aid}/action", data={"action": "pin"})
    client.post(f"/api/assignments/{aid}/action", data={"action": "pin"})  # 重复
    with container.db.session() as s:
        assert s.get(Assignment, aid).pinned is True  # 幂等：仍为置顶


# ---------- 10：未来 7 天排除逾期 ----------


def test_days7_excludes_overdue(container, mock_account):
    db = container.db
    sync_account(container, mock_account, "manual")
    with db.session() as s:
        overdue = s.exec(select(Assignment).where(
            Assignment.source_id == "CO-03::EN-ORAL")).one()
        overdue.due_at = now_utc() - timedelta(days=1)
        s.commit()
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")
    html = client.get("/?view=list&status=all&days7=true").text
    assert "口语录音作业" not in html  # 逾期被下界排除


# ---------- 12：配置校验 ----------


def test_config_validation(monkeypatch):
    monkeypatch.setenv("DDLHUB_REMINDER_OFFSETS", "-1,0,60,100000")
    monkeypatch.setenv("DDLHUB_SYNC_TIMES", "08:00,25:00")
    monkeypatch.setenv("DDLHUB_PORT", "99999")
    monkeypatch.setenv("DDLHUB_TIMEZONE", "Not/AZone")
    from ddlhub.config import Settings

    s = Settings.load(base_dir=None)
    assert s.reminder_offsets == (60,)          # 非法档位剔除，合法保留
    assert s.sync_times == ("08:00",)           # 非法时刻忽略
    assert s.port == 8787                       # 端口越界回退
    assert s.timezone == "Asia/Shanghai"        # 时区不可识别回退
