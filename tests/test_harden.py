"""本地 Web 加固：Host 白名单 / Origin / Sec-Fetch-Site / 会话 cookie（P0-5）。"""
import pytest
from fastapi.testclient import TestClient

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
