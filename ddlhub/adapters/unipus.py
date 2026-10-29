"""U校园（外研社，unipus.cn）适配器。

阶段交付：登录（有头浏览器人工登录 + 会话加密保存 + 通用判定）。
作业抓取待校准：需要一次真实登录后的课程/作业页面样本（参照 docs/ADAPTER-GUIDE.md）。
"""
from __future__ import annotations

from .base import (
    BaseAdapter,
    Capability,
    CourseDraft,
    Credentials,
    Health,
    LoginResult,
)
from .registry import register

_NOT_CALIBRATED = ("「U校园」作业抓取尚未校准：请登录后提供课程列表页与作业/单元测试列表页的 "
                   "URL 和页面样本（参照 docs/ADAPTER-GUIDE.md），即可按学习通同款流程接入")


@register("unipus")
class UnipusAdapter(BaseAdapter):
    name = "U校园"
    capabilities = Capability.INTERACTIVE
    supports_2fa = True
    login_mode = "interactive"
    login_entry_url = "https://u.unipus.cn/"
    cookie_domain = "unipus.cn"
    login_cookie_hints = ("uid",)
    login_url_bad = ("login", "signin", "passport")

    def login(self, cred: Credentials) -> LoginResult:
        return LoginResult(
            ok=False, needs_interactive=True,
            message="U校园需在弹出的浏览器中完成一次登录（账密/扫码均可），会话将加密保存并长期复用",
        )

    def health_check(self) -> Health:
        return self.generic_health("https://u.unipus.cn/")

    def fetch_courses(self) -> list[CourseDraft]:
        raise RuntimeError(_NOT_CALIBRATED)

    def fetch_assignments(self, course: CourseDraft):
        raise RuntimeError(_NOT_CALIBRATED)
