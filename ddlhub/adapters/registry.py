"""适配器注册表：REGISTRY 字典 + @register 装饰器。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from .base import BaseAdapter

REGISTRY: dict[str, Type["BaseAdapter"]] = {}


def register(key: str):
    def deco(cls: Type["BaseAdapter"]) -> Type["BaseAdapter"]:
        if key in REGISTRY:
            raise ValueError(f"adapter key 重复注册: {key}")
        REGISTRY[key] = cls
        cls.key = key
        return cls

    return deco
