"""课程选择：设置页关闭的课程不再抓取、视图不再显示（数据保留）。"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from tests.mock_adapter import MockAdapter
from ddlhub.core.models import Course
from ddlhub.core.sync import sync_account
from ddlhub.web.main import create_app


def _set_course_enabled(db, source_id: str, enabled: bool) -> None:
    with db.session() as s:
        co = s.exec(select(Course).where(Course.source_id == source_id)).one()
        co.enabled = enabled
        s.commit()


def test_disabled_course_skipped_on_sync(container, mock_account, monkeypatch):
    sync_account(container, mock_account, "manual")  # 首次同步建课程

    _set_course_enabled(container.db, "CO-03", False)  # 关闭「大学英语（四）」

    fetched: list[str] = []
    orig = MockAdapter.fetch_assignments

    def rec(self, course):
        fetched.append(course.source_id)
        return orig(self, course)

    monkeypatch.setattr(MockAdapter, "fetch_assignments", rec)
    outcome = sync_account(container, mock_account, "manual")

    assert outcome.ok
    assert "CO-03" not in fetched          # 已关闭：不抓取
    assert "CO-01" in fetched              # 未关闭：正常抓取


def test_disabled_course_hidden_in_ui(container, mock_account):
    sync_account(container, mock_account, "manual")
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")  # 获取会话 cookie

    html = client.get("/?view=list&status=all").text
    assert "高等数学" in html and "大学英语" in html

    _set_course_enabled(container.db, "CO-01", False)
    html = client.get("/?view=list&status=all").text
    assert "高等数学" not in html           # 已关闭：视图隐藏
    assert "大学英语" in html               # 其他课程不受影响

    _set_course_enabled(container.db, "CO-01", True)
    html = client.get("/?view=list&status=all").text
    assert "高等数学" in html               # 数据保留，恢复即显示


def test_course_toggle_endpoint(container, mock_account):
    sync_account(container, mock_account, "manual")
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")

    with container.db.session() as s:
        co = s.exec(select(Course).where(Course.source_id == "CO-02")).one()

    r = client.post(f"/api/courses/{co.id}/toggle", data={})  # 未带 enabled → 关
    assert r.status_code == 200 and "关闭" in r.text
    r = client.post(f"/api/courses/{co.id}/toggle", data={"enabled": "true"})  # → 开
    assert r.status_code == 200 and "开启" in r.text


def test_filter_with_empty_params_renders(container, mock_account):
    """回归：筛选表单提交 account=&course=（空串）不应 422。"""
    sync_account(container, mock_account, "manual")
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")  # 会话 cookie

    for qs in ("/?view=list&status=done&account=&course=",
               "/?view=timeline&status=open&account=&course=&days7=true",
               "/?view=list&account=abc&course="):
        r = client.get(qs)
        assert r.status_code == 200, f"{qs} -> {r.status_code}"


def test_settings_page_renders(container, mock_account):
    sync_account(container, mock_account, "manual")
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    html = client.get("/settings").text
    assert "平台管理" in html and "课程选择" in html
    assert "添加平台" in html
    assert "高等数学（下）" in html  # 课程出现在选择列表


def test_onboarding_when_no_accounts(container):
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    html = client.get("/").text
    assert "还没有接入任何平台" in html
    assert "/settings" in html


def test_migration_002_adds_enabled_column(tmp_path):
    from sqlalchemy import inspect

    from ddlhub.core.db import Database

    db = Database(tmp_path)  # 启动即应用 001 + 002
    cols = {c["name"] for c in inspect(db.engine).get_columns("course")}
    assert "enabled" in cols
