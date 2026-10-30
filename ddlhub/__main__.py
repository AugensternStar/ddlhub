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
        import getpass

        sealed = get_setting(db, "vault", None)
        try:
            if sealed:
                mp = getpass.getpass("DDLHub vault 主密码: ")
                store = VaultStore.open(mp, base64.b64decode(sealed))
                store.set_persist_callback(
                    lambda blob: set_setting(db, "vault", base64.b64encode(blob).decode())
                )
                logger.info("已用主密码解锁 vault")
            else:
                print("[DDLHub] 首次使用 vault 模式：主密码仅用于本机加解密、不落盘；")
                print("         丢失主密码将无法恢复已存凭据，请牢记。")
                mp = getpass.getpass("设置 vault 主密码: ")
                mp2 = getpass.getpass("确认 vault 主密码: ")
                if not mp or mp != mp2:
                    raise SystemExit("两次输入为空或不一致，未创建 vault")
                store = VaultStore.create(mp)
                store.set_persist_callback(
                    lambda blob: set_setting(db, "vault", base64.b64encode(blob).decode())
                )
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


def _acquire_instance_lock(data_dir: Path):
    """R-P2-8：单实例锁——双开进程时第二实例退出（避免迁移/端口/WAL 竞争）。

    CR-01：Windows 用 msvcrt，POSIX 用 fcntl（start.sh 是受支持的交付路径）。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / "ddlhub.lock"
    fh = open(lock_path, "a+b")
    try:
        try:
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise SystemExit("[DDLHub] 检测到另一个实例正在运行（锁被占用），本次启动退出。")
    return fh  # 进程生命周期持有，退出自动释放


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    os.chdir(base_dir)

    settings = Settings.load(base_dir)
    log_file = setup_logging(settings.data_dir)
    warn_unsafe_data_dir(settings.data_dir, log=logger.warning)
    logger.info("DDLHub v%s 启动：数据目录 %s，日志 %s", __version__, settings.data_dir, log_file)

    _lock_fh = _acquire_instance_lock(settings.data_dir)  # 进程退出自动释放
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
        # 审计附带发现：token 经 logger 会被自家 redact 抹成 ******——横幅必须走 print
        print(f"[DDLHub] LAN 模式：手机请访问 http://<本机IP>:{settings.port}/?token={settings.token}"
              "（打开后自动换成 Cookie）")
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
