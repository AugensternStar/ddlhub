"""渠道构建：enabled 开关存 app_setting("channels")；SMTP 授权码缺失时邮件渠道自动不可用。"""
from __future__ import annotations

import os

from ..config import Settings
from ..core.db import Database
from ..core.kv import get_setting
from .base import NotifierBase
from .desktop import DesktopNotifier
from .email_smtp import EmailNotifier, SMTP_CRED_ENTRY

DEFAULT_CHANNELS = {"desktop": True, "email": False}


def get_channels_enabled(db: Database) -> dict[str, bool]:
    return {**DEFAULT_CHANNELS, **(get_setting(db, "channels", {}) or {})}


def set_channel_enabled(db: Database, key: str, enabled: bool) -> None:
    from ..core.kv import set_setting

    current = get_channels_enabled(db)
    current[key] = bool(enabled)
    set_setting(db, "channels", current)


def build_notifiers(settings: Settings) -> dict[str, NotifierBase]:
    notifiers: dict[str, NotifierBase] = {"desktop": DesktopNotifier()}
    smtp_password = settings.smtp_pass
    if not smtp_password:
        try:
            import keyring

            smtp_password = keyring.get_password(SMTP_CRED_ENTRY, "smtp") or ""
        except Exception:
            smtp_password = ""
    notifiers["email"] = EmailNotifier(
        settings.smtp_host, settings.smtp_port, settings.smtp_user, smtp_password
    )
    return notifiers
