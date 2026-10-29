"""运行时容器：settings/db/logger/凭据存储/渠道/调度器的单一持有者（测试可注入替换）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from ..config import Settings
from .db import Database
from .kv import SettingKV
from .security import CredentialStore

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler
    from fastapi.templating import Jinja2Templates

    from ..notify.base import NotifierBase
    from .interactive import InteractiveLoginManager


@dataclass
class Container:
    settings: Settings
    db: Database
    logger: logging.Logger
    session_secret: str
    credential_store: CredentialStore
    kek: Optional[bytes] = None  # 测试注入；None=按凭据模式自动派生
    scheduler: Optional["BackgroundScheduler"] = None
    templates: Optional["Jinja2Templates"] = None
    channel_override: Optional[dict[str, "NotifierBase"]] = None  # 测试注入用
    interactive: Optional["InteractiveLoginManager"] = None

    def session_kek(self) -> bytes:
        """会话加密 KEK：keyring 随机密钥；vault 模式复用主密码派生密钥。"""
        if self.kek is not None:
            return self.kek
        from .security import KeyringStore, VaultStore, get_session_kek

        if isinstance(self.credential_store, VaultStore):
            return self.credential_store.kek()
        if isinstance(self.credential_store, KeyringStore):
            return get_session_kek()
        return get_session_kek()

    def sessions_for(self, account_id: int):
        from .sessions import SessionStore

        return SessionStore(self.db, self.session_kek(), account_id)

    def kv_for(self, account_id: int) -> SettingKV:
        return SettingKV(self.db, f"a{account_id}")

    def adapter_ctx(self, account_id: int):
        import json

        from ..adapters.base import AdapterContext
        from .models import Account

        config: dict = {}
        with self.db.session() as s:
            acc = s.get(Account, account_id)
            if acc is not None and acc.config_json:
                try:
                    config = json.loads(acc.config_json)
                except json.JSONDecodeError:
                    config = {}
        return AdapterContext(
            log=self.logger, kv=self.kv_for(account_id),
            sessions=self.sessions_for(account_id), config=config,
        )

    def active_channels(self) -> dict[str, "NotifierBase"]:
        """按 app_setting('channels') 开关过滤，且渠道自身 available()。"""
        if self.channel_override is not None:
            return dict(self.channel_override)
        from ..notify import build_notifiers, get_channels_enabled

        enabled = get_channels_enabled(self.db)
        return {
            key: notifier
            for key, notifier in build_notifiers(self.settings).items()
            if enabled.get(key) and notifier.available()
        }
