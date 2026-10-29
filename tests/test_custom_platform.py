"""自定义平台：注册、配置注入、创建链路；演示平台从生产下架。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from ddlhub.adapters.custom import CustomAdapter
from ddlhub.adapters.registry import REGISTRY
from ddlhub.core.models import Account
from ddlhub.core.sync import sync_account
from ddlhub.web.main import create_app

APP_DIR = Path(__file__).resolve().parents[1] / "ddlhub"


def test_mock_removed_from_production():
    """演示平台已从生产代码下架（仅测试桩存在于 tests/）。"""
    assert not (APP_DIR / "adapters" / "mock.py").exists()
    assert "custom" in REGISTRY


def _custom_ctx(container, account_id, config):
    ctx = container.adapter_ctx(account_id)
    ctx.config = config
    return ctx


def test_custom_adapter_config_derivation(container, mock_account):
    cfg = {"entry_url": "https://jw.example.edu.cn/login", "cookie_domain": "example.edu.cn"}
    adapter = CustomAdapter(_custom_ctx(container, mock_account, cfg))
    assert adapter.login_entry_url == "https://jw.example.edu.cn/login"
    assert adapter.cookie_domain == "example.edu.cn"
    # cookie 域留空 → 从登录页 host 推导最后两段
    adapter2 = CustomAdapter(_custom_ctx(container, mock_account,
                                         {"entry_url": "https://u.school.edu.cn/"}))
    assert adapter2.cookie_domain == "school.edu.cn"
    r = adapter.login(None)
    assert r.needs_interactive and not r.ok
    assert adapter.fetch_courses() == []      # 仅登录保活，不返回假数据
    assert adapter.health_check().ok is False  # 无会话 → need_relogin（generic_health）


def test_create_account_custom_saves_config(container, mock_account):
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")  # cookie

    r = client.post("/api/accounts", data={
        "adapter_key": "custom",
        "label": "我的教务系统",
        "entry_url": "https://jw.example.edu.cn/login",
    }, follow_redirects=False)
    assert r.status_code == 303

    with container.db.session() as s:
        acc = s.exec(select(Account).where(Account.adapter_key == "custom")).one()
        cfg = json.loads(acc.config_json)
        assert cfg["entry_url"] == "https://jw.example.edu.cn/login"
        assert cfg["cookie_domain"] == ".example.edu.cn"
        assert cfg["portal_url"] == ""  # 可留空


def test_create_account_custom_requires_url(container):
    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.get("/")
    r = client.post("/api/accounts", data={"adapter_key": "custom", "label": "x"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "URL" in r.headers["location"]  # 错误信息重定向
    with container.db.session() as s:
        assert s.exec(select(Account).where(Account.adapter_key == "custom")).first() is None


def test_custom_account_sync_is_quiet(container, mock_account, monkeypatch):
    """自定义账号同步 = 登录保活：0 课程、状态正常，不报错（网络打桩）。"""
    container.sessions_for(mock_account).save(
        {"cookies": [{"name": "sid", "value": "v", "domain": ".example.edu.cn"}]}
    )
    with container.db.session() as s:
        acc = s.get(Account, mock_account)
        acc.adapter_key = "custom"
        import json as _json
        acc.config_json = _json.dumps({"entry_url": "https://jw.example.edu.cn/login",
                                       "cookie_domain": "example.edu.cn",
                                       "portal_url": "https://jw.example.edu.cn/"})
        s.commit()

    class FakeResp:
        url = "https://jw.example.edu.cn/"  # 未跳回登录页 → 会话有效

    class FakeClient:
        def get(self, url):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(CustomAdapter, "_httpx_with_session", lambda self: FakeClient())
    outcome = sync_account(container, mock_account, "manual")
    assert outcome.ok and "0 项作业" in outcome.message
