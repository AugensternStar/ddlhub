"""共享 fixture：tmp 数据目录 + 容器（注入 FakeNotifier）+ Mock 账号。"""
from __future__ import annotations

import logging
import uuid as uuidlib

import pytest

import tests.mock_adapter  # noqa: F401  在测试进程注册测试桩
from ddlhub.adapters import discover
from ddlhub.config import Settings
from ddlhub.core.db import Database
from ddlhub.core.models import Account
from ddlhub.core.normalize import now_utc
from ddlhub.core.runtime import Container
from ddlhub.notify.base import NotifierBase, SendResult
from tests.fakes import FakeCredentialStore


class FakeNotifier(NotifierBase):
    key = "desktop"
    name = "测试渠道"

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def send(self, title: str, body: str, url=None) -> SendResult:
        self.calls.append((title, body))
        return SendResult(ok=self.ok, error="" if self.ok else "模拟失败")


@pytest.fixture(autouse=True)
def _discover():
    discover()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        min_interval_min=10,
        timezone="Asia/Shanghai",
        port=8787,
    )


@pytest.fixture
def db(settings) -> Database:
    return Database(settings.data_dir)


@pytest.fixture
def fake_notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def credential_store() -> FakeCredentialStore:
    """内存凭据桩：测试不触碰宿主钥匙串（CI 无可用后端会抛 NoKeyringError）。"""
    return FakeCredentialStore()


@pytest.fixture
def container(settings, db, fake_notifier, credential_store) -> Container:
    return Container(
        settings=settings,
        db=db,
        logger=logging.getLogger("test"),
        session_secret="test-secret",
        credential_store=credential_store,
        kek=bytes(32),  # 测试固定 KEK，不触碰用户钥匙串
        channel_override={"desktop": fake_notifier},
    )


@pytest.fixture
def mock_account(db) -> int:
    with db.session() as s:
        acc = Account(
            uuid=str(uuidlib.uuid4()), adapter_key="mock", label="测试Mock",
            username_hint="demo****", status="ok", created_at=now_utc(),
        )
        s.add(acc)
        s.commit()
        return acc.id
