"""登录态加密缓存（设计 §6.2）：storage_state → AES-256-GCM → session_cache 表。

keyring 中存随机 KEK；vault 模式下由主密码派生密钥代替（见 runtime.Container.sessions_for）。
磁盘上永远只有密文；进程重启后 load() 解密恢复，达成"重启后登录态有效"验收。
"""
from __future__ import annotations

import json
from datetime import datetime

from .db import Database
from .models import SessionCache
from .normalize import now_utc
from .security import decrypt_blob, encrypt_blob


class SessionStore:
    """按账号隔离的加密会话存取（AdapterContext.sessions）。"""

    def __init__(self, db: Database, kek: bytes, account_id: int):
        self.db = db
        self.kek = kek
        self.account_id = account_id

    def load(self) -> dict | None:
        with self.db.session() as s:
            row = s.get(SessionCache, self.account_id)
        if row is None:
            return None
        try:
            return json.loads(decrypt_blob(self.kek, row.enc_blob))
        except Exception:
            return None  # KEK 轮换/数据损坏 → 视为无会话，走重新登录

    def save(self, state: dict, expires_at: datetime | None = None) -> None:
        blob = encrypt_blob(self.kek, json.dumps(state, ensure_ascii=False).encode("utf-8"))
        now = now_utc()
        with self.db.session() as s:
            row = s.get(SessionCache, self.account_id)
            if row is None:
                s.add(SessionCache(
                    account_id=self.account_id, enc_blob=blob, key_ref="kek",
                    updated_at=now, expires_at=expires_at,
                ))
            else:
                row.enc_blob = blob
                row.key_ref = "kek"
                row.updated_at = now
                row.expires_at = expires_at
            s.commit()

    def clear(self) -> None:
        with self.db.session() as s:
            row = s.get(SessionCache, self.account_id)
            if row is not None:
                s.delete(row)
                s.commit()
