"""本地 Web 加固：Host 白名单 / Origin / Sec-Fetch-Site / 会话 cookie（P0-5）。"""
import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ddlhub.core.runtime import Container
from ddlhub.core.security import KeyringStore
from ddlhub.web.main import create_app


@pytest.fixture
def client(container):
    app = create_app(container)
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_get_sets_session_cookie(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "ddlhub_session" in r.cookies


def test_bad_host_blocked(client):
    r = client.get("/", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_post_without_session_blocked(container):
    app = create_app(container)
    fresh = TestClient(app, base_url="http://127.0.0.1:8787")  # 未先 GET，无 cookie
    r = fresh.post("/api/assignments/1/action", data={"action": "done"})
    assert r.status_code == 401


def test_cross_origin_post_blocked(client):
    client.get("/")  # 取得会话 cookie
    r = client.post("/api/assignments/999999/action",
                    data={"action": "done"},
                    headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 403


def test_cross_site_fetch_blocked(client):
    client.get("/")
    r = client.post("/api/assignments/999999/action",
                    data={"action": "done"},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_post_passes_auth_layer(client):
    client.get("/")
    r = client.post("/api/assignments/999999/action",
                    data={"action": "done"},
                    headers={"Origin": "http://127.0.0.1:8787"})
    # 404 = 通过了加固层（作业不存在），而非 401/403
    assert r.status_code == 404


def test_non_browser_client_without_origin_allowed(client):
    """curl 等本机脚本无 Origin 头：带 cookie 即放行（CSRF 是浏览器向量）。"""
    client.get("/")
    r = client.post("/api/assignments/999999/action", data={"action": "done"})
    assert r.status_code == 404


# ---------- LAN 模式：/login 令牌交换（体检发现的路由缺失回归） ----------

@pytest.fixture
def lan_container(settings, db, fake_notifier):
    from dataclasses import replace

    from ddlhub.core.runtime import Container

    lan_settings = replace(settings, listen="lan", token="test-token-123")
    return Container(
        settings=lan_settings, db=db, logger=logging.getLogger("test"),
        session_secret="test-secret", credential_store=KeyringStore(),
        kek=bytes(32), channel_override={"desktop": fake_notifier},
    )


def test_lan_unauthenticated_redirects_to_login(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_lan_login_page_without_token_renders(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/login")
    assert r.status_code == 200
    assert "token" in r.text


def test_lan_login_token_exchange_sets_cookie_and_clears_url(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/login?token=test-token-123", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert "ddlhub_session" in r.cookies
    # 带 cookie 后正常访问
    assert client.get("/").status_code == 200


def test_lan_login_wrong_token_no_cookie(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    r = client.get("/login?token=wrong-token", follow_redirects=False)
    assert r.status_code == 200  # 渲染提示页
    assert "ddlhub_session" not in r.cookies
    # 仍未认证 → 访问业务页继续重定向
    assert client.get("/", follow_redirects=False).status_code == 302


def test_lan_post_without_auth_still_401(lan_container):
    app = create_app(lan_container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    assert client.post("/api/assignments/1/action", data={"action": "done"}).status_code == 401
