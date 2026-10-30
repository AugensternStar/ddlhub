"""审计修复回归：安全响应头、local_token、私网拒绝、SQL 切分器、同步生命周期、scheme 白名单。"""
import logging
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from ddlhub.adapters.base import AssignmentDraft
from ddlhub.adapters.custom import CustomAdapter
from tests.mock_adapter import MockAdapter
from ddlhub.core.db import Database
from ddlhub.core.models import Account, Assignment, SyncRun
from ddlhub.core.normalize import now_utc
from ddlhub.core.runtime import Container
from ddlhub.core.sync import sync_account
from tests.test_harden import lan_container  # noqa: F401  复用 LAN fixture
from ddlhub.web.main import create_app

# ---------- SEC-06：安全响应头 ----------


def test_security_headers_present(container):
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_docs_hidden_in_lan(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/docs", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"  # S-P3-2：未认证不可达


# ---------- SEC-02：local_token 模式 ----------


@pytest.fixture
def local_token_container(settings, db, fake_notifier, credential_store):
    lt_settings = replace(settings, local_token=True, token="local-token-9")
    return Container(
        settings=lt_settings, db=db, logger=logging.getLogger("test"),
        session_secret="test-secret", credential_store=credential_store,
        kek=bytes(32), channel_override={"desktop": fake_notifier},
    )


def test_local_token_gates_and_exchanges(local_token_container):
    app = create_app(local_token_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    r = client.get("/login?token=local-token-9", follow_redirects=False)
    assert r.status_code == 302 and "ddlhub_session" in r.cookies
    assert client.get("/").status_code == 200


def test_local_mode_without_flag_keeps_auto_cookie(container):
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200 and "ddlhub_session" in r.cookies  # 默认行为不变


# ---------- SEC-03：自定义平台私网地址拒绝 ----------


def test_custom_blocks_private_entry_url(container, mock_account, monkeypatch):
    adapter = CustomAdapter(container.adapter_ctx(mock_account))
    adapter.ctx.config = {"entry_url": "http://127.0.0.1:9000/login"}
    with pytest.raises(RuntimeError, match="安全策略"):
        adapter.login(None)
    adapter.ctx.config = {"entry_url": "http://localhost/login"}
    with pytest.raises(RuntimeError, match="安全策略"):
        adapter.login(None)
    adapter.ctx.config = {"entry_url": "http://169.254.169.254/meta"}
    with pytest.raises(RuntimeError, match="安全策略"):
        adapter.login(None)


def test_custom_private_url_allow_switch(container, mock_account, monkeypatch):
    adapter = CustomAdapter(container.adapter_ctx(mock_account))
    adapter.ctx.config = {"entry_url": "http://127.0.0.1:9000/login"}
    monkeypatch.setenv("DDLHUB_ALLOW_PRIVATE_URL", "1")
    adapter.login(None)  # 显式放行：不再抛
    monkeypatch.setenv("DDLHUB_ALLOW_PRIVATE_URL", "0")
    with pytest.raises(RuntimeError):
        adapter.login(None)


# ---------- R-P2-7：SQL 切分器注释感知 ----------


def test_splitter_survives_apostrophe_in_comment(tmp_path):
    from ddlhub.core.db import Database, _split_sql

    script = "-- 用户 don't 偷懒; 注意撇号\nCREATE TABLE t(a INTEGER);\n-- 再来一个 it's fine\nINSERT INTO t VALUES (1);"
    stmts = _split_sql(script)
    assert len(stmts) == 2

    # 端到端：含撇号注释的迁移文件能正常执行
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_x.sql").write_text(
        "-- it's a test; with apostrophe\nCREATE TABLE demo(id INTEGER);\n", encoding="utf-8"
    )
    db = Database(tmp_path / "d2", migrations_dir=mig)
    with db.session() as s:
        from sqlalchemy import text as _text

        s.exec(_text("SELECT 1"))
        assert True


# ---------- 同步生命周期（R-P2-1 / R-P1-4 / R-P2-2 / R-P2-9） ----------


def _run_row(db, account_id, status, minutes_ago):
    with db.session() as s:
        s.add(SyncRun(account_id=account_id, trigger="cron", status=status,
                      started_at=now_utc() - timedelta(minutes=minutes_ago),
                      finished_at=now_utc() - timedelta(minutes=minutes_ago)))
        s.commit()


def test_stale_running_swept_to_failed(container, mock_account):
    with container.db.session() as s:
        s.add(SyncRun(account_id=mock_account, trigger="manual", status="running",
                      started_at=now_utc() - timedelta(hours=1)))
        s.commit()
    from ddlhub.core.scheduler import sweep_stale_runs

    sweep_stale_runs(container)
    with container.db.session() as s:
        row = s.exec(select(SyncRun).where(SyncRun.status == "failed")).one()
        assert "进程中断" in row.error_summary


def test_zero_assignments_marked_partial_and_visible(container, mock_account, monkeypatch):
    sync_account(container, mock_account, "manual")  # 先正常同步入库

    monkeypatch.setattr(MockAdapter, "fetch_assignments", lambda self, c: [])  # 模拟改版
    outcome = sync_account(container, mock_account, "manual")

    assert "4 门课程失败" in outcome.message
    with container.db.session() as s:
        acc = s.get(Account, mock_account)
        assert "解析到 0 条" in acc.last_error  # R-P2-9：不再一路绿灯


def test_throttle_baseline_ignores_skipped(container, mock_account):
    """R-P2-2：限频基准取非 skipped——被跳过的班次不推长窗口。"""
    _run_row(container.db, mock_account, "success", 11)   # 上次真实同步：11 分钟前
    _run_row(container.db, mock_account, "skipped", 1)    # 1 分钟前刚跳过过一次

    outcome = sync_account(container, mock_account, "cron")
    assert outcome.ok and "跳过" not in outcome.message   # 旧实现会在此误跳过


# ---------- SEC-01：source_url scheme 白名单 ----------


def test_source_url_javascript_neutralized(db, mock_account):
    from ddlhub.core.upsert import upsert_assignments, upsert_courses

    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = upsert_courses(db, acc.id, [__import__(
        "ddlhub.adapters.base", fromlist=["CourseDraft"]).CourseDraft(
        source_id="C1", name="课程一")])["C1"].id
    draft = AssignmentDraft(
        source_id="XSS1", course_source_id="C1", title="恶意链接",
        source_url="javascript:fetch('/api/sync',{method:'POST'})",
    )
    upsert_assignments(db, acc.id, cid, "C1", [draft], "Asia/Shanghai", notify_new=False)
    with db.session() as s:
        row = s.exec(select(Assignment).where(Assignment.source_id == "C1::XSS1")).one()
        assert row.source_url is None  # javascript: 被置空，模板不再输出 href
