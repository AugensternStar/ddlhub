"""SQLModel 表定义，与 migrations/001_init.sql 严格对应。

时间约定：所有 datetime 一律为 naive UTC（见 core/normalize.py）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, ForeignKeyConstraint, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel
from sqlmodel.main import NaiveDatetime  # sqlmodel 0.0.48 未在顶层导出


class Account(SQLModel, table=True):
    __tablename__ = "account"

    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(max_length=36, unique=True, index=True)  # 不可变，keyring 条目名用它
    adapter_key: str = Field(max_length=64)
    label: str = Field(max_length=128)
    username_hint: str = Field(default="", max_length=64)  # 脱敏展示，如 13****01
    status: str = Field(default="ok", max_length=16)  # ok | need_relogin | disabled
    last_ok_at: Optional[NaiveDatetime] = None
    last_error: str = Field(default="", max_length=2000)
    created_at: NaiveDatetime


class SessionCache(SQLModel, table=True):
    __tablename__ = "session_cache"
    __table_args__ = (
        ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
    )

    account_id: int = Field(primary_key=True)
    enc_blob: bytes
    key_ref: str = Field(max_length=128)
    updated_at: NaiveDatetime
    expires_at: Optional[NaiveDatetime] = None


class Course(SQLModel, table=True):
    __tablename__ = "course"
    __table_args__ = (
        UniqueConstraint("account_id", "source_id", name="uq_course_account_source"),
        UniqueConstraint("id", "account_id", name="uq_course_id_account"),
        ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        Index("ix_course_account", "account_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int
    source_id: str = Field(max_length=255)
    name: str = Field(max_length=255)
    term: Optional[str] = Field(default=None, max_length=64)
    url: Optional[str] = Field(default=None, max_length=512)
    enabled: bool = Field(default=True)  # 课程选择开关：关=不抓取+不展示（数据保留）


class Assignment(SQLModel, table=True):
    __tablename__ = "assignment"
    __table_args__ = (
        UniqueConstraint("account_id", "source_id", name="uq_assignment_account_source"),
        ForeignKeyConstraint(
            ["course_id", "account_id"],
            ["course.id", "course.account_id"],
            ondelete="CASCADE",
            name="fk_assignment_course",
        ),
        Index("ix_assignment_due_at", "due_at"),
        Index("ix_assignment_done_status", "local_done", "status"),
        Index("ix_assignment_course", "course_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int
    account_id: int  # 由复合外键保证与 course.account_id 一致
    source_id: str = Field(max_length=512)  # core 组合键 course_source_id::local_id
    title: str = Field(max_length=512)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    due_at: Optional[NaiveDatetime] = None  # naive UTC；NULL=无截止（展示但不提醒）
    due_at_prev: Optional[NaiveDatetime] = None
    published_at: Optional[NaiveDatetime] = None
    status: str = Field(default="open", max_length=16)  # open | submitted | closed
    source_url: Optional[str] = Field(default=None, max_length=512)
    estimated_minutes: Optional[int] = None
    priority: int = Field(default=0)
    reminder_offsets_json: Optional[str] = Field(default=None, max_length=255)
    local_done: bool = Field(default=False)
    local_ignored: bool = Field(default=False)
    pinned: bool = Field(default=False)
    change_hash: str = Field(default="", max_length=64)
    first_seen_at: NaiveDatetime
    updated_at: NaiveDatetime


class SyncRun(SQLModel, table=True):
    __tablename__ = "sync_run"
    __table_args__ = (
        ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        Index("ix_sync_run_account", "account_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int
    trigger: str = Field(max_length=16)  # manual | cron | startup
    status: str = Field(max_length=16)  # success | partial | failed | need_relogin | skipped
    started_at: NaiveDatetime
    finished_at: Optional[NaiveDatetime] = None
    ok_count: int = Field(default=0)
    fail_count: int = Field(default=0)
    error_summary: str = Field(default="", max_length=2000)


class NotificationLog(SQLModel, table=True):
    __tablename__ = "notification_log"
    __table_args__ = (
        UniqueConstraint("dedupe_key", "channel", name="uq_notif_dedupe_channel"),
        ForeignKeyConstraint(["assignment_id"], ["assignment.id"], ondelete="SET NULL"),
        Index("ix_notif_status", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: Optional[int] = None
    channel: str = Field(max_length=32)
    kind: str = Field(max_length=16)  # reminder | change | overdue | test
    minutes_before: Optional[int] = None
    dedupe_key: str = Field(max_length=600)
    status: str = Field(default="pending", max_length=16)  # pending|sent|failed|skipped
    attempts: int = Field(default=0)
    summary: str = Field(default="", max_length=1000)
    error: str = Field(default="", max_length=1000)
    sent_at: Optional[NaiveDatetime] = None
    created_at: NaiveDatetime


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_setting"

    key: str = Field(primary_key=True, max_length=64)
    value_json: str = Field(default="{}", sa_column=Column(Text))
