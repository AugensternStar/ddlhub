"""有头浏览器人工登录（设计 §5.4，一等公民路径）。

线程内启动 Playwright 有头浏览器（优先系统 Edge，回退 Chromium；内存态 context，
禁用 user_data_dir——P1-10：Chromium 不落地明文 cookie），用户人工完成账密+验证码/扫码；
轮询适配器 login_success 判定，成功即 storage_state() 加密落库并自动同步一次。
不绕过任何验证。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .redact import redact

logger = logging.getLogger("ddlhub.interactive")

_LAUNCH_ATTEMPTS = (
    {"channel": "msedge"},   # 系统自带 Edge，与学习通环境最接近
    {},                      # Playwright 内置 Chromium（playwright install chromium）
)


@dataclass
class LoginState:
    state: str = "idle"  # idle | waiting | success | failed | timeout
    message: str = ""


class InteractiveLoginManager:
    TIMEOUT_S = 180

    def __init__(self, container, headless: bool | None = None):
        self.container = container
        # 测试可传 headless=True；生产恒为有头（None→False）
        self._headless = headless if headless is not None else False
        self._states: dict[int, LoginState] = {}
        self._lock = threading.Lock()

    def status(self, account_id: int) -> LoginState:
        return self._states.get(account_id, LoginState())

    def _set(self, account_id: int, state: str, message: str) -> None:
        with self._lock:
            self._states[account_id] = LoginState(state, message)

    def start(self, account_id: int) -> LoginState:
        with self._lock:
            cur = self.status(account_id)
            if cur.state == "waiting":
                return cur
            self._states[account_id] = LoginState("waiting", "正在打开浏览器…")
        threading.Thread(target=self._run, args=(account_id,), daemon=True).start()
        return self.status(account_id)

    def _run(self, account_id: int, adapter_cls=None) -> None:
        c = self.container
        from ..adapters.registry import REGISTRY
        from .models import Account
        from .sync import sync_account

        try:
            with c.db.session() as s:
                account = s.get(Account, account_id)
            if account is None:
                self._set(account_id, "failed", "账号不存在")
                return
            cls = adapter_cls or REGISTRY.get(account.adapter_key)
            if cls is None or not getattr(cls, "login_entry_url", ""):
                self._set(account_id, "failed", "该平台不支持浏览器登录")
                return
            label = account.label
        except Exception as exc:
            logger.exception("interactive login setup failed")
            self._set(account_id, "failed", redact(f"{type(exc).__name__}: {exc}"))
            return

        ctx = c.adapter_ctx(account_id)
        adapter = cls(ctx)
        try:
            from playwright.sync_api import sync_playwright

            logger.info("interactive login: opening headed browser for %s", label)
            with sync_playwright() as p:
                browser = self._launch(p)
                try:
                    context = browser.new_context()  # 内存态：不写任何 profile 落盘
                    page = context.new_page()
                    page.goto(adapter.login_entry_url, timeout=30000)
                    snapshot = adapter.login_snapshot(context)  # 登录前平台域 cookie 快照
                    deadline = time.time() + self.TIMEOUT_S
                    ok = False
                    while time.time() < deadline:
                        try:
                            if adapter.login_success(context, page, snapshot):
                                ok = True
                                break
                        except Exception:
                            pass  # 页面跳转瞬间的判定异常忽略，继续轮询
                        time.sleep(1.5)
                    if not ok:
                        self._set(account_id, "timeout", "登录超时：请在设置页重试")
                        return
                    ctx.sessions.save(context.storage_state())
                    self._set(account_id, "success", "登录成功，会话已加密保存，正在同步…")
                finally:
                    browser.close()  # 内存态 context 随之消亡，磁盘无明文
            logger.info("interactive login: %s success, syncing", label)
            sync_account(c, account_id, "manual")
            self._set(account_id, "success", "登录成功并已完成同步")
        except Exception as exc:
            logger.exception("interactive login failed for %s", label)
            self._set(account_id, "failed", redact(f"{type(exc).__name__}: {exc}"))
        finally:
            adapter.close()

    def _launch(self, p):
        last_err: Exception | None = None
        for attempt in _LAUNCH_ATTEMPTS:
            try:
                return p.chromium.launch(headless=self._headless, **attempt)
            except Exception as exc:  # 未装 Edge / Chromium 等
                last_err = exc
                logger.warning("browser launch attempt %s failed: %s", attempt, exc)
        raise last_err  # type: ignore[misc]
