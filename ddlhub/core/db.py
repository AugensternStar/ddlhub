"""SQLite：engine/session、WAL、schema_version + migrations 顺序执行（不使用 Alembic）。

迁移执行（审计 08）：独立裸连接 + 显式 BEGIN IMMEDIATE 事务——pysqlite 缺省模式会在
DDL 前隐式提交，SQLAlchemy begin() 保护不了 DDL；此处手动管理事务保证「失败整体回滚」。
任一待应用迁移执行前，自动生成 data/backups/pre-migration-*.db 备份（SQLite backup API）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, create_engine

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _split_sql(script: str) -> list[str]:
    """把多语句 SQL 脚本按分号切成单语句（引号感知 + 跳过 -- 注释，R-P2-7）。

    注释里出现撇号（如 don't）不会再让 in_str 常开。
    """
    parts: list[str] = []
    buf: list[str] = []
    in_str = False
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if ch == "'":
            in_str = not in_str
            buf.append(ch)
            i += 1
            continue
        if not in_str and ch == "-" and i + 1 < n and script[i + 1] == "-":
            while i < n and script[i] != "\n":  # 行注释：跳到行尾
                i += 1
            continue
        if ch == ";" and not in_str:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
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
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    def _backup(self, raw: sqlite3.Connection) -> Path:
        """审计 08：迁移前用 SQLite backup API 生成一致快照（含 WAL 内容）。"""
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_path = backup_dir / f"pre-migration-{stamp}.db"
        with sqlite3.connect(dest_path) as dst:
            raw.backup(dst)
        return dest_path

    def migrate(self) -> list[int]:
        """按文件名前缀顺序执行未应用的 NNN_*.sql（显式事务，失败整体回滚）。"""
        applied_now: list[int] = []
        raw = sqlite3.connect(self.path, timeout=30)
        try:
            raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, "
                "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            done = {r[0] for r in raw.execute("SELECT version FROM schema_version").fetchall()}
            pending: list[tuple[Path, int, str]] = []
            if self.migrations_dir.exists():
                for path in sorted(self.migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
                    version = int(path.name.split("_", 1)[0])
                    if version in done:
                        continue
                    pending.append((path, version, path.read_text(encoding="utf-8")))

            if pending:
                backup_path = self._backup(raw)
                raw.execute("BEGIN IMMEDIATE")
                try:
                    for path, version, sql in pending:
                        for stmt in _split_sql(sql):
                            raw.execute(stmt)
                        raw.execute(
                            "INSERT INTO schema_version (version) VALUES (?)", (version,)
                        )
                        applied_now.append(version)
                    raw.execute("COMMIT")
                except Exception as exc:
                    raw.execute("ROLLBACK")
                    raise SystemExit(
                        f"[DDLHub] 迁移失败已整体回滚（数据库未改动）；"
                        f"升级前备份：{backup_path}。错误：{exc}"
                    ) from exc
        finally:
            raw.close()
        return applied_now

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)
