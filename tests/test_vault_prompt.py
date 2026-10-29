"""S-P1-2 复现测试：vault 主密码必须交互输入，不再从 env/.env 读取。

修前红色（Settings 携带 master_password 字段且读 env；vault 首用报 SystemExit 要求配置 env），
修后全绿。不联网；getpass 打桩。
"""
import base64
import dataclasses

import pytest

from ddlhub.config import Settings
from ddlhub.core.db import Database
from ddlhub.core.kv import get_setting
from ddlhub.core.security import VaultStore


@pytest.fixture
def vault_settings(tmp_path, monkeypatch):
    # 即使有人恶意/误配了 env 主密码，也不再被读取
    monkeypatch.setenv("DDLHUB_MASTER_PASSWORD", "leaked-via-env")
    return Settings(data_dir=tmp_path / "data", vault_mode=True)


@pytest.fixture
def vault_db(vault_settings):
    from ddlhub.core.db import Database

    return Database(vault_settings.data_dir)


def test_master_password_removed_from_config(vault_settings):
    s = Settings.load(base_dir=None)
    assert all(f.name != "master_password" for f in dataclasses.fields(s))


def test_vault_first_use_prompts_not_env(vault_settings, vault_db, monkeypatch):
    from ddlhub.__main__ import _credential_store

    prompts = iter(["master-pw", "master-pw"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))
    store = _credential_store(vault_settings, vault_db)
    assert isinstance(store, VaultStore)
    assert get_setting(vault_db, "vault", None) is not None  # 密文已落库


def test_vault_prompt_mismatch_exits(vault_settings, vault_db, monkeypatch):
    from ddlhub.__main__ import _credential_store

    prompts = iter(["pw1", "pw2"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))
    with pytest.raises(SystemExit):
        _credential_store(vault_settings, vault_db)


def test_vault_open_with_prompt_and_roundtrip(vault_settings, vault_db, monkeypatch):
    from ddlhub.__main__ import _credential_store

    seed = VaultStore.create("master-pw")
    seed.store("xuexitong", "uuid-1", "18800000000", "secret-cred")
    vault_db  # 确保 schema 存在
    from ddlhub.core.kv import set_setting

    set_setting(vault_db, "vault", base64.b64encode(seed.seal()).decode())

    prompts = iter(["master-pw"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))
    store = _credential_store(vault_settings, vault_db)
    assert store.load("xuexitong", "uuid-1") == ("18800000000", "secret-cred")


def test_vault_wrong_password_exits(vault_settings, vault_db, monkeypatch):
    from ddlhub.__main__ import _credential_store

    seed = VaultStore.create("master-pw")
    from ddlhub.core.kv import set_setting

    set_setting(vault_db, "vault", base64.b64encode(seed.seal()).decode())
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "wrong-pw")
    with pytest.raises(SystemExit):
        _credential_store(vault_settings, vault_db)
