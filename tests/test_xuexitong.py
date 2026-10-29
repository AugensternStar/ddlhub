"""学习通适配器：解析函数（真实样本回放）、登录判定、健康检查、会话加密、有头登录管理器。"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ddlhub.adapters.base import AdapterContext, Credentials
from ddlhub.adapters.mock import MockAdapter
from ddlhub.adapters.xuexitong import (
    XuexitongAdapter,
    parse_backclazzdata,
    parse_work_list_html,
)
from ddlhub.core.db import Database
from ddlhub.core.interactive import InteractiveLoginManager
from ddlhub.core.normalize import now_utc
from ddlhub.core.sessions import SessionStore

FIX = Path(__file__).parent / "fixtures" / "xuexitong"


@pytest.fixture(scope="module")
def local_login_url():
    """本地 HTTP 服务：有头登录测试不依赖外网。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>login-sim</body></html>")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def _ctx(container, account_id) -> AdapterContext:
    return container.adapter_ctx(account_id)


# ---------- 解析纯函数（fixture 回放，不联网） ----------

def test_parse_backclazzdata():
    data = json.loads((FIX / "backclazzdata.json").read_text(encoding="utf-8"))
    drafts = parse_backclazzdata(data)
    assert [d.source_id for d in drafts] == ["900123456", "900123457"]  # 空课程频道被跳过
    assert drafts[0].name == "高等数学（下）"
    assert "classId=900123456" in drafts[0].url and "courseId=201234567" in drafts[0].url


def test_parse_backclazzdata_empty():
    assert parse_backclazzdata({"result": 1, "channelList": []}) == []


def test_parse_work_list_live():
    """真实页面样本（离散数学，0/1 未交）：相对倒计时 → 绝对截止。"""
    html = (FIX / "work_list_live.html").read_text(encoding="utf-8")
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    drafts = parse_work_list_html(html, "153753297", "https://x", now=now)
    assert len(drafts) == 1
    d = drafts[0]
    assert d.source_id == "222000001" and d.course_source_id == "153753297"
    assert d.title == "第一次作业"
    assert d.status == "open"  # 未交
    assert d.due_at == now + timedelta(hours=95, minutes=35)  # 剩余95小时35分钟
    assert d.due_at.tzinfo is not None
    assert "workId=222000001" in d.source_url
    assert d.priority == 0 and d.estimated_minutes == 60


def test_parse_work_list_live_submitted():
    """真实页面样本（高级语言程序设计，1/1 待批阅）→ submitted。"""
    html = (FIX / "work_list_live_submitted.html").read_text(encoding="utf-8")
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    drafts = parse_work_list_html(html, "157668998", "https://x", now=now)
    assert len(drafts) == 1
    d = drafts[0]
    assert d.title == "示例练习题"
    assert d.status == "submitted"  # 待批阅
    assert d.source_id == "222000002"


def test_parse_work_list_empty_course():
    """真实页面样本（0/0 暂无作业）→ 空列表而非报错。"""
    html = (FIX / "work_list_live.html").read_text(encoding="utf-8").replace(
        '<li onclick="goTask(this);"', "<!-- 已移除作业行 <li onclick=\"goTask(this);\""
    ).replace('tabindex="0" aria-label="第一次作业 ; 未交" role="link">', "-->")
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    # 暂无作业的页面没有 .bottomList li → 空列表
    empty_html = html[: html.find('<div class="bottomList"')] + "</div></div></body></html>"
    assert parse_work_list_html(empty_html, "c", "u", now=now) == []


def test_parse_work_list_requires_login():
    with pytest.raises(RuntimeError, match="登录"):
        parse_work_list_html("<html>请登录后操作</html>", "c", "u")


# ---------- 登录判定与策略 ----------

def test_login_requires_interactive(container, mock_account):
    adapter = XuexitongAdapter(_ctx(container, mock_account))
    r = adapter.login(Credentials("u", "p"))
    assert not r.ok and r.needs_interactive  # 红线：不程序化提交账密


def test_login_success_detection(container, mock_account):
    adapter = XuexitongAdapter(_ctx(container, mock_account))

    class FakeBrowserContext:
        def cookies(self):
            return [
                {"name": "_uid", "domain": ".chaoxing.com", "value": "1"},
                {"name": "vc3", "domain": ".chaoxing.com", "value": "ticket"},
            ]

    class FakeBrowserContextNoTicket:
        def cookies(self):
            return [{"name": "_uid", "domain": ".chaoxing.com", "value": "1"}]

    assert adapter.login_success(FakeBrowserContext(), None) is True
    assert adapter.login_success(FakeBrowserContextNoTicket(), None) is False


def test_health_check_no_session(container, mock_account):
    adapter = XuexitongAdapter(_ctx(container, mock_account))
    h = adapter.health_check()
    assert not h.ok and h.need_relogin


def test_health_check_with_session(container, mock_account, monkeypatch):
    ctx = _ctx(container, mock_account)
    ctx.sessions.save({"cookies": [{"name": "vc3", "value": "t", "domain": ".chaoxing.com"}]})
    adapter = XuexitongAdapter(ctx)

    class FakeResp:
        url = "https://i.chaoxing.com/"

    class FakeClient:
        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(adapter, "_http", lambda: FakeClient())
    h = adapter.health_check()
    assert h.ok and not h.need_relogin


def test_health_check_expired_redirects_to_passport(container, mock_account, monkeypatch):
    ctx = _ctx(container, mock_account)
    ctx.sessions.save({"cookies": [{"name": "vc3", "value": "t", "domain": ".chaoxing.com"}]})
    adapter = XuexitongAdapter(ctx)

    class FakeResp:
        url = "https://passport2.chaoxing.com/login"

    class FakeClient:
        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(adapter, "_http", lambda: FakeClient())
    h = adapter.health_check()
    assert not h.ok and h.need_relogin  # 跳回登录页 → need_relogin → UI 弹有头浏览器


# ---------- 会话加密存储 ----------

def test_session_store_roundtrip_and_ciphertext(tmp_path):
    from ddlhub.core.models import Account

    db = Database(tmp_path)
    with db.session() as s:
        s.add(Account(id=1, uuid="u1", adapter_key="xuexitong", label="测试",
                      created_at=now_utc()))
        s.commit()

    kek = bytes(range(32))
    store = SessionStore(db, kek, 1)
    store.save({"cookies": [{"name": "vc3", "value": "super-secret-ticket"}]})

    assert store.load()["cookies"][0]["value"] == "super-secret-ticket"

    raw = sqlite3.connect(db.path).execute("SELECT enc_blob FROM session_cache").fetchone()[0]
    assert b"super-secret" not in raw  # 落库是密文

    assert SessionStore(db, bytes(32), 1).load() is None  # 错 KEK 解不开 → 视为无会话

    store.clear()
    assert store.load() is None


# ---------- 有头登录管理器（headless 测试形态） ----------

def test_sync_passes_encrypted_sessions(container, db, mock_account, monkeypatch):
    """回归：同步路径必须给适配器传加密会话。

    阶段 3 曾漏传（sync.py 手工构造 AdapterContext），导致学习通 health_check
    调 ctx.sessions.load() 时炸 NoneType——本用例保证不再回归。
    """
    from sqlmodel import select

    from ddlhub.adapters.base import BaseAdapter, Capability
    from ddlhub.adapters.registry import REGISTRY
    from ddlhub.core.models import Account
    from ddlhub.core.sync import sync_account

    seen = {}

    class SessCheckAdapter(BaseAdapter):
        name = "SessCheck"
        capabilities = Capability.EMBEDDED_JSON

        def __init__(self, ctx):
            super().__init__(ctx)
            seen["sessions"] = ctx.sessions

        def login(self, cred): ...
        def fetch_courses(self): return []
        def fetch_assignments(self, course): return []

    monkeypatch.setitem(REGISTRY, "sesscheck", SessCheckAdapter)
    with db.session() as s:
        acc = s.get(Account, mock_account)
        acc.adapter_key = "sesscheck"
        s.commit()

    outcome = sync_account(container, mock_account, "manual")

    assert outcome.ok
    assert seen["sessions"] is not None  # ← 曾为 None 的回归点
    seen["sessions"].save({"cookies": [{"name": "t", "value": "v"}]})
    assert seen["sessions"].load()["cookies"][0]["value"] == "v"


def test_interactive_manager_success(container, mock_account, local_login_url):
    from ddlhub.adapters.base import BaseAdapter, Capability

    class FakeBrowserAdapter(BaseAdapter):
        name = "FakeBrowser"
        capabilities = Capability.INTERACTIVE
        login_mode = "interactive"
        login_entry_url = ""  # 由测试注入本地服务地址

        def login(self, cred): ...
        def fetch_courses(self): return []
        def fetch_assignments(self, course): return []
        def login_success(self, context, page, snapshot=None): return True  # 打开即视为登录完成

    FakeBrowserAdapter.login_entry_url = local_login_url
    mgr = InteractiveLoginManager(container, headless=True)
    mgr._run(mock_account, adapter_cls=FakeBrowserAdapter)

    assert mgr.status(mock_account).state == "success"
    # 会话已加密落库（真实 storage_state 路径）
    state = container.sessions_for(mock_account).load()
    assert state is not None and "cookies" in state
    # 成功后自动同步已执行（mock 数据已入库）
    with container.db.session() as s:
        from sqlmodel import select

        from ddlhub.core.models import Course

        assert s.exec(select(Course)).first() is not None


def test_interactive_manager_timeout(container, mock_account, local_login_url):
    from ddlhub.adapters.base import BaseAdapter, Capability

    class NeverLoginAdapter(BaseAdapter):
        name = "NeverLogin"
        capabilities = Capability.INTERACTIVE
        login_mode = "interactive"
        login_entry_url = ""  # 由测试注入本地服务地址

        def login(self, cred): ...
        def fetch_courses(self): return []
        def fetch_assignments(self, course): return []
        def login_success(self, context, page, snapshot=None): return False

    NeverLoginAdapter.login_entry_url = local_login_url
    mgr = InteractiveLoginManager(container, headless=True)
    mgr.TIMEOUT_S = 2  # 缩短等待
    mgr._run(mock_account, adapter_cls=NeverLoginAdapter)
    assert mgr.status(mock_account).state == "timeout"
