"""app_setting 读写：全局设置 + 适配器命名空间 KV（如 mock 演示场景开关）。"""
from __future__ import annotations

import json

from sqlmodel import select

from .db import Database
from .models import AppSetting


def get_setting(db: Database, key: str, default):
    with db.session() as s:
        row = s.get(AppSetting, key)
        if row is None:
            return default
        try:
            return json.loads(row.value_json)
        except json.JSONDecodeError:
            return default


def set_setting(db: Database, key: str, value) -> None:
    with db.session() as s:
        row = s.get(AppSetting, key)
        encoded = json.dumps(value, ensure_ascii=False)
        if row is None:
            s.add(AppSetting(key=key, value_json=encoded))
        else:
            row.value_json = encoded
        s.commit()


class SettingKV:
    """适配器命名空间 KV：key 实际存储为 f"{namespace}:{key}"。"""

    def __init__(self, db: Database, namespace: str):
        self.db = db
        self.namespace = namespace

    def get(self, key: str, default=None):
        return get_setting(self.db, f"{self.namespace}:{key}", default)

    def set(self, key: str, value) -> None:
        set_setting(self.db, f"{self.namespace}:{key}", value)
