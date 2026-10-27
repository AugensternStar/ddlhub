"""适配器自动发现（P0-1 修复：discover 必须在包的 __init__.py，用包级 __path__）。

新增平台 = adapters/ 下新文件（内含 @register("key") 一行），核心代码零改动。
单个坏文件只跳过自身，不拖垮启动与其他平台。
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

_DISCOVER_EXCLUDE = {"base", "registry"}


def discover() -> list[str]:
    loaded: list[str] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name in _DISCOVER_EXCLUDE:
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
            loaded.append(info.name)
        except Exception:
            log.exception("adapter import failed: %s（跳过，不影响启动与其他平台）", info.name)
    return loaded
