"""适配器契约（设计 §5.1）。适配器只产出 Draft，落库归 core 统一做。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Flag, auto
from logging import Logger
from typing import ClassVar, Optional, Protocol


class Capability(Flag):
    OFFICIAL_API = auto()    # 官方 API（最高优先）
    EMBEDDED_JSON = auto()   # 页面内嵌 JSON / 私有接口
    PLAYWRIGHT = auto()      # 渲染兜底
    INTERACTIVE = auto()     # 支持有头人工登录


class Credentials:
    """SecretStr 语义：repr 恒为掩码，任何序列化/日志都不会泄漏。"""

    __slots__ = ("username", "password")

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    def __repr__(self) -> str:
        return "Credentials(username=****, password=******)"


@dataclass
class LoginResult:
    ok: bool = False
    needs_interactive: bool = False  # True → core 弹有头浏览器（阶段3）
    message: str = ""                # 已脱敏


@dataclass
class Health:
    ok: bool = True
    need_relogin: bool = False
    detail: str = ""


@dataclass
class CourseDraft:
    source_id: str            # 平台侧课程 id（账号内唯一）
    name: str
    term: Optional[str] = None
    url: Optional[str] = None


@dataclass
class AssignmentDraft:
    source_id: str            # ★ 平台侧作业 id，可能仅“课程内唯一”（组合规则见 core/upsert.py §5.6）
    course_source_id: str
    title: str
    description: Optional[str] = None
    due_at: Optional[datetime] = None  # 必须带时区；naive 按本地时区解释；None=无截止
    published_at: Optional[datetime] = None
    status: str = "open"      # open | submitted | closed
    source_url: Optional[str] = None
    estimated_minutes: Optional[int] = None
    priority: int = 0         # 平台侧线索，如“限时测验”→1


class KeyValueStore(Protocol):
    def get(self, key: str, default=None): ...
    def set(self, key: str, value) -> None: ...


@dataclass
class AdapterContext:
    """core 提供给适配器的运行环境；适配器不直接接触明文存储。"""

    log: Logger
    kv: KeyValueStore
    sessions: Optional[object] = None        # 阶段3：加密会话读写
    http: Optional[object] = None            # 阶段3：复用会话 cookie 的 httpx 工厂
    headed_browser: Optional[object] = None  # 阶段3：有头浏览器工厂（内存态 context）
    limiter: Optional[object] = None         # 请求间随机 1-3s 限速器


class BaseAdapter(ABC):
    key: ClassVar[str] = ""
    name: ClassVar[str] = "未命名平台"
    capabilities: ClassVar[Capability] = Capability.INTERACTIVE
    supports_2fa: ClassVar[bool] = False
    fetch_interval: ClassVar[timedelta] = timedelta(minutes=10)  # 红线：自动抓取 >=10min
    scenario_buttons: ClassVar[bool] = False  # Mock 专用演示开关，真实平台恒为 False
    # 登录模式：credentials=账密可程序化登录；interactive=弹有头浏览器人工登录一次
    login_mode: ClassVar[str] = "credentials"
    login_entry_url: ClassVar[str] = ""  # interactive 模式的登录页
    # —— interactive 模式的通用登录判定参数 ——
    cookie_domain: ClassVar[str] = ""            # 平台会话 cookie 所属域，如 "unipus.cn"
    login_cookie_hints: ClassVar[tuple[str, ...]] = ()  # 平台特征 cookie 名（可选辅助）
    login_url_bad: ClassVar[tuple[str, ...]] = ("login", "signin", "passport")
    login_url_ok: ClassVar[str] = ""             # 登录后 URL 必含的子串（空=不校验）

    UA_DEFAULT: ClassVar[str] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
    )

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    @abstractmethod
    def login(self, cred: Credentials) -> LoginResult: ...

    @abstractmethod
    def fetch_courses(self) -> list[CourseDraft]: ...

    @abstractmethod
    def fetch_assignments(self, course: CourseDraft) -> list[AssignmentDraft]: ...

    def health_check(self) -> Health:
        """用已存会话发轻量请求验证登录态；默认视为健康（有会话的平台建议自实现）。"""
        return Health(ok=True)

    def login_snapshot(self, context) -> set[str]:
        """浏览器打开登录页后，快照平台域已有的 cookie 名（用于登录后 diff）。"""
        names: set[str] = set()
        if not self.cookie_domain:
            return names
        try:
            for c in context.cookies():
                if self.cookie_domain in (c.get("domain") or "") and c.get("name"):
                    names.add(c["name"])
        except Exception:
            pass
        return names

    def generic_login_success(self, context, page, snapshot: set[str] | None = None) -> bool:
        """通用登录完成判定（core 传入打开时的快照）：
        平台域出现「新 cookie 名」或「特征 cookie」，且页面已离开登录页。"""
        url = ""
        try:
            url = page.url or ""
        except Exception:
            pass
        if any(bad in url for bad in self.login_url_bad):
            return False
        if self.login_url_ok and self.login_url_ok not in url:
            return False
        if not self.cookie_domain:
            return False
        try:
            current: set[str] = set()
            hinted = False
            for c in context.cookies():
                if self.cookie_domain not in (c.get("domain") or "") or not c.get("value"):
                    continue
                current.add(c["name"])
                if c["name"] in self.login_cookie_hints:
                    hinted = True
            new_cookies = current - (snapshot or set())
            return bool(new_cookies) or hinted
        except Exception:
            return False

    def login_success(self, context, page, snapshot: set[str] | None = None) -> bool:
        """有头浏览器人工登录完成判定（core 传入打开登录页时的 cookie 快照）。
        默认用通用判定（快照 diff + 特征 cookie + 离开登录页）；平台可覆盖。"""
        return self.generic_login_success(context, page, snapshot)

    def _httpx_with_session(self, cookie_domain_filter: str | None = None):
        """用已存（加密）会话构造带 cookie 的 httpx 客户端。"""
        import httpx

        state = self.ctx.sessions.load()
        if not state or not state.get("cookies"):
            return None
        cookies = httpx.Cookies()
        for c in state.get("cookies") or []:
            dom = c.get("domain") or ""
            if cookie_domain_filter and cookie_domain_filter not in dom:
                continue
            cookies.set(c["name"], c.get("value", ""), domain=dom or None,
                        path=c.get("path") or "/")
        return httpx.Client(
            cookies=cookies,
            headers={"User-Agent": self.UA_DEFAULT},
            follow_redirects=True,
            timeout=20,
        )

    def generic_health(self, portal_url: str) -> Health:
        """通用健康检查：无会话 → need_relogin；门户 GET 跳回登录页 → need_relogin。"""
        client = self._httpx_with_session()
        if client is None:
            return Health(ok=False, need_relogin=True,
                          detail="无已保存会话，请先完成一次浏览器登录")
        try:
            r = client.get(portal_url)
            final = str(r.url)
            if any(bad in final for bad in self.login_url_bad):
                return Health(ok=False, need_relogin=True, detail="会话已失效（跳回登录页）")
            return Health(ok=True)
        except Exception as exc:
            return Health(ok=False, need_relogin=False, detail=f"网络异常：{exc}")
        finally:
            client.close()

    def relogin(self) -> LoginResult:
        """会话失效后的恢复。interactive 平台 → 打开浏览器人工登录；Mock 可即时恢复。"""
        return LoginResult(ok=False, needs_interactive=True,
                           message="需要重新登录（请到设置页点击「打开浏览器登录」）")

    def close(self) -> None: ...
