"""共享 fixture：tmp 数据目录 + 容器（注入 FakeNotifier）+ Mock 账号。"""
from __future__ import annotations

import logging
import uuid as uuidlib

import pytest

from ddlhub.adapters import discover
from ddlhub.config import Settings
from ddlhub.core.db import Database
from ddlhub.core.models import Account
from ddlhub.core.normalize import now_utc
from ddlhub.core.runtime import Container
from ddlhub.core.security import KeyringStore
from ddlhub.notify.base import NotifierBase, SendResult


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
def container(settings, db, fake_notifier) -> Container:
    return Container(
        settings=settings,
        db=db,
        logger=logging.getLogger("test"),
        session_secret="test-secret",
        credential_store=KeyringStore(),
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
