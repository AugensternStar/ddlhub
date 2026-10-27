"""配置：读 .env / 环境变量；红线常量在此钳制（同步间隔 >= 10 分钟）。"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_OFFSETS = (10080, 4320, 1440, 360, 60)  # minutes_before：T-7d / T-3d / T-1d / T-6h / T-1h

SYNC_DIR_HINTS = ("onedrive", "dropbox", "nutstore", "坚果云", "google drive", "icloud")


@dataclass(frozen=True)
class Settings:
    port: int = 8787
    listen: str = "local"  # local | lan
    timezone: str = "Asia/Shanghai"
    data_dir: Path = field(default_factory=lambda: Path("data"))
    sync_times: tuple[str, ...] = ("08:00", "21:00")
    min_interval_min: int = 10
    reminder_offsets: tuple[int, ...] = DEFAULT_OFFSETS
    vault_mode: bool = False
    master_password: str = ""
    token: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""

    @property
    def lan(self) -> bool:
        return self.listen == "lan"

    @classmethod
    def load(cls, base_dir: Path | None = None) -> "Settings":
        base_dir = base_dir or Path.cwd()
        env_path = base_dir / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        port = int(os.getenv("DDLHUB_PORT", "8787"))
        listen = os.getenv("DDLHUB_LISTEN", "local").strip().lower()
        min_interval = max(10, int(os.getenv("DDLHUB_MIN_INTERVAL_MIN", "10")))  # 红线钳制
        raw_offsets = os.getenv("DDLHUB_REMINDER_OFFSETS", ",".join(map(str, DEFAULT_OFFSETS)))
        offsets = tuple(sorted({int(x) for x in raw_offsets.split(",") if x.strip()}, reverse=True))
        sync_times = tuple(
            t.strip() for t in os.getenv("DDLHUB_SYNC_TIMES", "08:00,21:00").split(",") if t.strip()
        )
        data_dir = Path(os.getenv("DDLHUB_DATA_DIR", "data")).expanduser()
        if not data_dir.is_absolute():
            data_dir = (base_dir / data_dir).resolve()

        token = os.getenv("DDLHUB_TOKEN", "")
        if listen == "lan" and not token:
            token = secrets.token_urlsafe(24)
            _append_env(env_path, f"DDLHUB_TOKEN={token}")

        return cls(
            port=port,
            listen=listen,
            timezone=os.getenv("DDLHUB_TIMEZONE", "Asia/Shanghai"),
            data_dir=data_dir,
            sync_times=sync_times,
            min_interval_min=min_interval,
            reminder_offsets=offsets,
            vault_mode=os.getenv("DDLHUB_VAULT_MODE", "0") == "1",
            master_password=os.getenv("DDLHUB_MASTER_PASSWORD", ""),
            token=token,
            smtp_host=os.getenv("DDLHUB_SMTP_HOST", ""),
            smtp_port=int(os.getenv("DDLHUB_SMTP_PORT", "465")),
            smtp_user=os.getenv("DDLHUB_SMTP_USER", ""),
            smtp_pass=os.getenv("DDLHUB_SMTP_PASS", ""),
        )


def warn_unsafe_data_dir(data_dir: Path, log=print) -> None:
    """数据目录若在网盘同步目录下，WAL 会被同步器破坏——启动时告警。"""
    lowered = str(data_dir).lower()
    for hint in SYNC_DIR_HINTS:
        if hint in lowered:
            log(f"[警告] 数据目录位于同步网盘路径下（含 '{hint}'）：{data_dir}")
            log("        SQLite WAL 在同步目录下可能损坏，建议用 DDLHUB_DATA_DIR 指向本地磁盘目录。")
            return


def _append_env(env_path: Path, line: str) -> None:
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with env_path.open("a", encoding="utf-8") as f:
            f.write(f"\n{line}\n")
    except OSError:
        pass
