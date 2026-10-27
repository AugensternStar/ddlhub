"""SQLite：engine/session、WAL、schema_version + migrations 顺序执行（不使用 Alembic）。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, create_engine

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _split_sql(script: str) -> list[str]:
    """把多语句 SQL 脚本按分号切成单语句（引号感知；纯注释块丢弃）。"""
    parts: list[str] = []
    buf: list[str] = []
    in_str = False
    for ch in script:
        if ch == "'":
            in_str = not in_str
        if ch == ";" and not in_str:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))

    stmts: list[str] = []
    for p in parts:
        code = "\n".join(
            line for line in p.splitlines() if not line.strip().startswith("--")
        ).strip()
        if code:
            stmts.append(p.strip())
    return stmts


class Database:
    def __init__(self, data_dir: Path, migrations_dir: Path | None = None):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "ddlhub.db"
        self.migrations_dir = migrations_dir or _MIGRATIONS_DIR
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        event.listen(self.engine, "connect", Database._on_connect)
        self.migrate()

    @staticmethod
    def _on_connect(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    def migrate(self) -> list[int]:
        """按文件名前缀顺序执行未应用的 NNN_*.sql，返回本次应用的版本号。"""
        applied_now: list[int] = []
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, "
                "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            done = {r[0] for r in conn.exec_driver_sql("SELECT version FROM schema_version").fetchall()}
            for path in sorted(self.migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name.split("_", 1)[0])
                if version in done:
                    continue
                for stmt in _split_sql(path.read_text(encoding="utf-8")):
                    conn.exec_driver_sql(stmt)
                conn.exec_driver_sql(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
                applied_now.append(version)
        return applied_now

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)
