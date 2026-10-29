"""四个新增平台的登录适配器：注册、登录引导、通用判定逻辑、待校准边界。"""
import pytest

from ddlhub.adapters.base import BaseAdapter
from ddlhub.adapters.daxuemooc import DaxuemoocAdapter
from ddlhub.adapters.iwrite import IwriteAdapter
from ddlhub.adapters.registry import REGISTRY
from ddlhub.adapters.unipus import UnipusAdapter
from ddlhub.adapters.zhihuishu import ZhihuishuAdapter
from ddlhub.adapters.xuexitong import XuexitongAdapter

NEW_PLATFORMS = [DaxuemoocAdapter, ZhihuishuAdapter, UnipusAdapter, IwriteAdapter]


class FakeBrowserContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class FakePage:
    def __init__(self, url):
        self.url = url


def test_registry_contains_all_platforms():
    for key, cls in [("daxuemooc", DaxuemoocAdapter), ("zhihuishu", ZhihuishuAdapter),
                     ("unipus", UnipusAdapter), ("iwrite", IwriteAdapter),
                     ("xuexitong", XuexitongAdapter), ("mock", None)]:
        assert key in REGISTRY
        if cls is not None:
            assert REGISTRY[key] is cls


@pytest.mark.parametrize("cls", NEW_PLATFORMS)
def test_login_requires_interactive(container, mock_account, cls):
    adapter = cls(container.adapter_ctx(mock_account))
    r = adapter.login(None)
    assert not r.ok and r.needs_interactive


@pytest.mark.parametrize("cls", NEW_PLATFORMS)
def test_fetch_not_calibrated(container, mock_account, cls):
    """作业抓取未校准：必须显式报错而非返回假数据。"""
    adapter = cls(container.adapter_ctx(mock_account))
    with pytest.raises(RuntimeError, match="尚未校准"):
        adapter.fetch_courses()
    with pytest.raises(RuntimeError, match="尚未校准"):
        adapter.fetch_assignments(None)


@pytest.mark.parametrize("cls", NEW_PLATFORMS)
def test_health_check_without_session(container, mock_account, cls):
    adapter = cls(container.adapter_ctx(mock_account))
    h = adapter.health_check()
    assert not h.ok and h.need_relogin


def test_generic_login_success_detection(container, mock_account):
    """通用判定：新 cookie / 特征 cookie → 成功；仍在登录页 → False。"""
    adapter = UnipusAdapter(container.adapter_ctx(mock_account))

    domain = "unipus.cn"
    # 1) 登录页上，无新 cookie → False
    assert adapter.generic_login_success(
        FakeBrowserContext([{"name": "anon", "domain": f".{domain}", "value": "1"}]),
        FakePage("https://u.unipus.cn/u/login"),
        snapshot={"anon"},
    ) is False

    # 2) 登录后出现快照里没有的新会话 cookie，且已离开登录页 → True
    assert adapter.generic_login_success(
        FakeBrowserContext([
            {"name": "anon", "domain": f".{domain}", "value": "1"},
            {"name": "uid", "domain": f".{domain}", "value": "9527"},
        ]),
        FakePage("https://u.unipus.cn/home"),
        snapshot={"anon"},
    ) is True

    # 3) 特征 cookie 命中（即使不在快照 diff 里也判成功）
    assert adapter.generic_login_success(
        FakeBrowserContext([{"name": "uid", "domain": f".{domain}", "value": "9527"}]),
        FakePage("https://u.unipus.cn/home"),
        snapshot={"uid"},  # 名字相同但已存在 → diff 为空；命中 hints → True
    ) is True

    # 4) 学习通覆盖版判定：vc3 出现才成功（不受通用判定影响）
    xt = XuexitongAdapter(container.adapter_ctx(mock_account))
    assert xt.login_success(
        FakeBrowserContext([{"name": "vc3", "domain": ".chaoxing.com", "value": "t"}]),
        FakePage("https://i.chaoxing.com/"), {"anon"},
    ) is True
    assert xt.login_success(
        FakeBrowserContext([{"name": "_uid", "domain": ".chaoxing.com", "value": "1"}]),
        FakePage("https://i.chaoxing.com/"), {"_uid"},
    ) is False


def test_login_snapshot_collects_platform_cookies(container, mock_account):
    adapter = UnipusAdapter(container.adapter_ctx(mock_account))
    ctx = FakeBrowserContext([
        {"name": "anon", "domain": ".unipus.cn", "value": "1"},
        {"name": "other", "domain": ".example.com", "value": "x"},  # 非平台域，忽略
        {"name": "", "domain": ".unipus.cn", "value": "2"},          # 无名，忽略
    ])
    assert adapter.login_snapshot(ctx) == {"anon"}


def test_generic_health_redirect_to_login(container, mock_account, monkeypatch):
    adapter = UnipusAdapter(container.adapter_ctx(mock_account))
    # 先存一个会话
    container.sessions_for(mock_account).save(
        {"cookies": [{"name": "uid", "value": "1", "domain": ".unipus.cn"}]}
    )
    client = adapter._httpx_with_session()
    assert client is not None
    client.close()

    class FakeResp:
        url = "https://u.unipus.cn/u/login"

    class FakeClient:
        def get(self, url):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(adapter, "_httpx_with_session", lambda: FakeClient())
    h = adapter.health_check()
    assert not h.ok and h.need_relogin
