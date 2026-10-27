"""桌面通知：Windows Toast（winotify）。聚焦助手开启时会静默失败——
UI 的“发送测试通知”按钮 + notification_log 落库用于暴露此问题（设计 §8.4）。
"""
from __future__ import annotations

import importlib.util
from typing import ClassVar, Optional

from ..core.redact import redact
from .base import NotifierBase, SendResult


class DesktopNotifier(NotifierBase):
    key: ClassVar[str] = "desktop"
    name: ClassVar[str] = "桌面通知"

    def available(self) -> bool:
        return importlib.util.find_spec("winotify") is not None

    def send(self, title: str, body: str, url: Optional[str] = None) -> SendResult:
        try:
            from winotify import Notification

            msg = f"{body}\n{url}" if url else body
            Notification(app_id="DDLHub", title=title[:64], msg=msg[:400]).show()
            return SendResult(ok=True)
        except Exception as exc:  # 系统策略/聚焦助手/非 Windows 等
            return SendResult(ok=False, error=redact(f"{type(exc).__name__}: {exc}"))
