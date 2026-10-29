"""自定义平台适配器：无需写代码即可接入任意平台的登录保活。

用户在设置页填写：平台备注名 + 登录页 URL（必填）+ Cookie 域/门户 URL（可选，
缺省从登录页 URL 推导）。账号级配置存在 account.config_json，经 AdapterContext.config 注入。

能力边界（如实）：登录 + 会话加密保活 + 失效检测；作业抓取需按平台开发适配器
（参照 docs/ADAPTER-GUIDE.md），本适配器不做任何解析、不返回假数据。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .base import (
    BaseAdapter,
    Capability,
    Credentials,
    Health,
    LoginResult,
)
from .registry import register


@register("custom")
class CustomAdapter(BaseAdapter):
    name = "自定义平台"
    capabilities = Capability.INTERACTIVE
    supports_2fa = True
    login_mode = "interactive"

    # —— 实例级配置（来自 account.config_json，类级占位仅供检查） ——

    @property
    def login_entry_url(self) -> str:
        return (self.ctx.config or {}).get("entry_url", "")

    @property
    def cookie_domain(self) -> str:  # type: ignore[override]
        cfg = self.ctx.config or {}
        if cfg.get("cookie_domain"):
            return cfg["cookie_domain"]
        host = urlsplit(self.login_entry_url).netloc.split(":")[0]
        # u.unipus.cn → unipus.cn；jw.school.edu.cn → school.edu.cn（学术二级后缀取三段）
        parts = host.split(".")
        second_level = {"edu.cn", "com.cn", "org.cn", "net.cn", "gov.cn", "ac.cn"}
        if len(parts) > 2 and ".".join(parts[-2:]) in second_level:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:]) if len(parts) > 2 else host

    @property
    def login_cookie_hints(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple(self.ctx.config or {}).get("login_cookie_hints", ())

    @property
    def login_url_bad(self) -> tuple[str, ...]:  # type: ignore[override]
        cfg = self.ctx.config or {}
        return tuple(cfg.get("login_url_bad") or ("login", "signin", "passport"))

    # —— 契约 ——

    def login(self, cred: Credentials) -> LoginResult:
        return LoginResult(
            ok=False, needs_interactive=True,
            message="自定义平台将在弹出的浏览器中打开你配置的登录页，完成登录后会话加密保存",
        )

    def health_check(self) -> Health:
        cfg = self.ctx.config or {}
        portal = cfg.get("portal_url") or self.login_entry_url
        if not portal:
            return Health(ok=False, need_relogin=True, detail="未配置登录入口")
        return self.generic_health(portal)

    def fetch_courses(self):
        self.ctx.log.info("custom platform: login keep-alive only, no course parsing")
        return []

    def fetch_assignments(self, course):
        self.ctx.log.info("custom platform: login keep-alive only, no assignment parsing")
        return []
