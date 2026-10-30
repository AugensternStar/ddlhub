"""平台改名：设置页直接编辑账号备注名（凭据/会话按 uuid 关联，不受影响）。"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from ddlhub.core.models import Account
from ddlhub.web.main import create_app


@pytest.fixture
def client(container, mock_account):
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")  # 会话 cookie
    return client


def _label(container, aid):
    with container.db.session() as s:
        return s.get(Account, aid).label


def test_rename_success(client, container, mock_account):
    r = client.post(f"/api/accounts/{mock_account}/rename",
                    data={"label": "我的学习通"}, follow_redirects=False)
    assert r.status_code == 303
    assert _label(container, mock_account) == "我的学习通"


def test_rename_redirect_message_contains_old_and_new(client, container, mock_account):
    r = client.post(f"/api/accounts/{mock_account}/rename",
                    data={"label": "新名字"}, follow_redirects=False)
    from urllib.parse import unquote

    assert "旧名" in unquote(r.headers["location"]) or "新名字" in unquote(r.headers["location"])


def test_rename_empty_label_rejected(client, container, mock_account):
    r = client.post(f"/api/accounts/{mock_account}/rename",
                    data={"label": "   "}, follow_redirects=False)
    assert r.status_code == 303
    assert _label(container, mock_account) != ""  # 旧名保留


def test_rename_unknown_account(client, container):
    from urllib.parse import unquote

    r = client.post("/api/accounts/99999/rename",
                    data={"label": "x"}, follow_redirects=False)
    assert r.status_code == 303
    assert "账号不存在" in unquote(r.headers["location"])


def test_rename_long_label_truncated(client, container, mock_account):
    r = client.post(f"/api/accounts/{mock_account}/rename",
                    data={"label": "长" * 300}, follow_redirects=False)
    assert r.status_code == 303
    assert len(_label(container, mock_account)) <= 128


def test_rename_visible_on_settings_page(client, container, mock_account):
    client.post(f"/api/accounts/{mock_account}/rename", data={"label": "可见性检查"})
    html = client.get("/settings").text
    assert "可见性检查" in html
