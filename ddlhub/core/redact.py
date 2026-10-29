"""脱敏：日志 Formatter 层正则清洗（覆盖 msg、参数与异常堆栈），redact() 供入库前清洗。"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

SECRET_RE = re.compile(
    r"(password|passwd|pwd|token|secret|cookie|authorization|smtp_pass|sendkey|api_?key)"
    r"(\s*[=:]\s*)([^\s&;'\"]+)",
    re.IGNORECASE,
)
# S-P2-1：JSON 形态（"password": "x"）与单字母短键（u=/p=）
JSON_SECRET_RE = re.compile(
    r'("(?i:password|passwd|pwd|token|secret|cookie|authorization|smtp_pass|sendkey|api_?key)"'
    r'\s*:\s*)("[^"]*"|\{[^}]*\}|[^,}\s]+)'
)
SHORT_KEY_RE = re.compile(r'(\?|^|&|\s)([up])=([^\s&]+)', re.IGNORECASE)
MASK = "******"


def redact(text: str) -> str:
    out = SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    out = JSON_SECRET_RE.sub(lambda m: f'{m.group(1)}"{MASK}"', out)
    out = SHORT_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}={MASK}", out)
    return out


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = data_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "ddlhub.log"

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台中文
        except (AttributeError, OSError):
            pass

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    root.addHandler(file_handler)
    root.addHandler(console)

    # 第三方库降噪
    for noisy in ("httpx", "httpcore", "apscheduler", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
