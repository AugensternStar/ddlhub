"""python -m ddlhub：init → migrate → 种子账号 → 调度器 → uvicorn（仅本机）。"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .adapters import discover
from .config import Settings, warn_unsafe_data_dir
from .core.db import Database
from .core.interactive import InteractiveLoginManager
from .core.kv import get_setting, set_setting
from .core.redact import setup_logging
from .core.runtime import Container
from .core.scheduler import start_scheduler
from .core.security import (
    KeyringStore,
    VaultStore,
    assert_secure_backend,
    mask_username,
)
from .web.main import create_app

logger = logging.getLogger("ddlhub")


def _credential_store(settings: Settings, db: Database):
    if settings.vault_mode:
        sealed = get_setting(db, "vault")
        try:
            if sealed:
                store = VaultStore.open(settings.master_password, base64.b64decode(sealed))
                logger.info("已用主密码解锁 vault")
            else:
                if not settings.master_password:
                    raise SystemExit("vault 模式首次使用需先设置 DDLHUB_MASTER_PASSWORD")
                store = VaultStore.create(settings.master_password)
                set_setting(db, "vault", base64.b64encode(store.seal()).decode())
                logger.info("已创建主密码 vault")
            return store
        except SystemExit:
            raise
        except Exception:
            raise SystemExit("主密码错误或 vault 数据损坏，拒绝启动")
    backend = assert_secure_backend()
    logger.info("keyring 后端：%s", backend)
    return KeyringStore()


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    os.chdir(base_dir)

    settings = Settings.load(base_dir)
    log_file = setup_logging(settings.data_dir)
    warn_unsafe_data_dir(settings.data_dir, log=logger.warning)
    logger.info("DDLHub v%s 启动：数据目录 %s，日志 %s", __version__, settings.data_dir, log_file)

    db = Database(settings.data_dir)
    discover()

    container = Container(
        settings=settings,
        db=db,
        logger=logger,
        session_secret=secrets.token_urlsafe(32),
        credential_store=_credential_store(settings, db),
    )
    container.interactive = InteractiveLoginManager(container)
    container.scheduler = start_scheduler(container)
    app = create_app(container)

    host = "0.0.0.0" if settings.lan else "127.0.0.1"
    shown_host = "本机局域网IP" if settings.lan else "127.0.0.1"
    url = f"http://{shown_host}:{settings.port}/"
    if settings.lan:
        logger.info("LAN 模式：手机请访问 http://<本机IP>:%s/?token=%s（打开后自动换成 Cookie）",
                    settings.port, settings.token)
    logger.info("Web UI：%s （Ctrl+C 退出）", url)

    if not settings.lan:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{settings.port}/")).start()
    try:
        uvicorn.run(app, host=host, port=settings.port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        pass
    finally:
        if container.scheduler:
            container.scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
