"""凭据安全（设计 §6.1/§6.2）：

- 默认：系统钥匙串（Windows Credential Manager 等）。启动时白名单断言，
  白名单外（含 PlaintextKeyring/NullKeyring）一律拒绝——系统里不存在明文路径。
- 兜底：主密码 vault 模式——Argon2id 派生密钥 + AES-256-GCM 加密 blob 存 SQLite。
- 会话缓存：钥匙串中随机 KEK + AES-256-GCM 加密后落库。
"""
from __future__ import annotations

import base64
import json
import secrets
from typing import Protocol

import keyring
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SERVICE = "ddlhub"

ALLOWED_BACKENDS = {
    "keyring.backends.Windows.WinVaultKeyring",
    "keyring.backends.macOS.Keychain",
    "keyring.backends.SecretService.Keyring",
    "keyring.backends.libsecret.Keyring",
}

KEK_ENTRY = f"{SERVICE}/_internal/kek"
KEK_USER = "ddlhub"


def qualified_backend() -> str:
    kr = keyring.get_keyring()
    return f"{type(kr).__module__}.{type(kr).__name__}"


def assert_secure_backend() -> str:
    """返回后端名（非敏感，供启动日志）；不安全则拒绝启动。"""
    q = qualified_backend()
    if q not in ALLOWED_BACKENDS:
        raise SystemExit(
            f"[DDLHub] 检测到不安全或不可用的 keyring 后端：{q}\n"
            "拒绝以明文存储凭据。跨平台/无桌面环境请改用主密码模式：\n"
            "  DDLHUB_VAULT_MODE=1 + DDLHUB_MASTER_PASSWORD=...（见 .env.example）"
        )
    return q


# ---------- AES-256-GCM 原语 ----------

def encrypt_blob(key: bytes, data: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)


def decrypt_blob(key: bytes, blob: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def get_session_kek() -> bytes:
    """会话加密 KEK：首次生成随机 256-bit 存钥匙串。vault 模式下由 vault 密钥代替。"""
    stored = keyring.get_password(KEK_ENTRY, KEK_USER)
    if stored:
        return base64.b64decode(stored)
    raw = secrets.token_bytes(32)
    keyring.set_password(KEK_ENTRY, KEK_USER, base64.b64encode(raw).decode())
    return raw


# ---------- 凭据存取 ----------

class CredentialStore(Protocol):
    def store(self, adapter_key: str, account_uuid: str, username: str, password: str) -> None: ...
    def load(self, adapter_key: str, account_uuid: str) -> tuple[str, str] | None: ...
    def delete(self, adapter_key: str, account_uuid: str) -> None: ...


class KeyringStore:
    """单条目存 JSON({"u":..,"p":..})，避免依赖 get_credential 的跨平台差异。"""

    @staticmethod
    def _entry(adapter_key: str, account_uuid: str) -> str:
        return f"{SERVICE}/{adapter_key}/{account_uuid}"

    def store(self, adapter_key, account_uuid, username, password) -> None:
        secret = json.dumps({"u": username, "p": password}, ensure_ascii=False)
        keyring.set_password(self._entry(adapter_key, account_uuid), KEK_USER, secret)

    def load(self, adapter_key, account_uuid) -> tuple[str, str] | None:
        raw = keyring.get_password(self._entry(adapter_key, account_uuid), KEK_USER)
        if not raw:
            return None
        data = json.loads(raw)
        return data["u"], data["p"]

    def delete(self, adapter_key, account_uuid) -> None:
        try:
            keyring.delete_password(self._entry(adapter_key, account_uuid), KEK_USER)
        except keyring.errors.PasswordDeleteError:
            pass


class VaultStore:
    """主密码模式：Argon2id(m=64MiB,t=3,p=4) 派生密钥，AES-GCM 加密整包存 app_setting。"""

    SALT_LEN = 16

    def __init__(self, master_password: str, blob: bytes | None, salt: bytes | None):
        self._key = self._derive(master_password, salt or b"")
        self._data: dict[str, dict[str, str]] = {}
        if blob:
            self._data = json.loads(decrypt_blob(self._key, blob))

    @staticmethod
    def _derive(master: str, salt: bytes) -> bytes:
        from argon2.low_level import Type, hash_secret_raw

        return hash_secret_raw(
            master.encode("utf-8"), salt,
            time_cost=3, memory_cost=64 * 1024, parallelism=4,
            hash_len=32, type=Type.ID,
        )

    @staticmethod
    def create(master_password: str) -> "VaultStore":
        salt = secrets.token_bytes(VaultStore.SALT_LEN)
        store = VaultStore.__new__(VaultStore)
        store._key = VaultStore._derive(master_password, salt)
        store._data = {}
        store._salt = salt
        return store

    def seal(self) -> bytes:
        """返回要持久化的 blob（salt + 密文），调用方写入 app_setting 'vault'。"""
        payload = encrypt_blob(self._key, json.dumps(self._data, ensure_ascii=False).encode())
        return self._salt + payload

    @staticmethod
    def open(master_password: str, sealed: bytes) -> "VaultStore":
        store = VaultStore.__new__(VaultStore)
        store._salt, payload = sealed[: VaultStore.SALT_LEN], sealed[VaultStore.SALT_LEN:]
        store._key = VaultStore._derive(master_password, store._salt)
        store._data = json.loads(decrypt_blob(store._key, payload))
        return store

    def store(self, adapter_key, account_uuid, username, password) -> None:
        self._data[f"{adapter_key}/{account_uuid}"] = {"u": username, "p": password}

    def load(self, adapter_key, account_uuid) -> tuple[str, str] | None:
        item = self._data.get(f"{adapter_key}/{account_uuid}")
        return (item["u"], item["p"]) if item else None

    def delete(self, adapter_key, account_uuid) -> None:
        self._data.pop(f"{adapter_key}/{account_uuid}", None)

    def kek(self) -> bytes:
        """vault 模式下用主密码派生密钥兼作会话加密 KEK（salt 持久化，密钥稳定）。"""
        return self._key


def mask_username(username: str) -> str:
    if len(username) <= 4:
        return "*" * len(username)
    return f"{username[:2]}****{username[-2:]}"
