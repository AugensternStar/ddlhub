"""幂等 upsert（组合自然键，设计 §5.6）+ DDL 变更检测（设计 §7.2）。

硬规则：
- 自然键 = f"{course_source_id}::{draft.source_id}"，由 core 统一生成（适配器无从拼错）。
- local_done / local_ignored / pinned 本地字段，同步永不覆盖。
- 变更（DDL/标题/→closed）产生 change 通知，change_hash 防同一变更重发。
- 函数自身不发送通知：收集到 stats.notifications，由调用方在提交后统一 claim
  （避免嵌套写事务撞 SQLite 锁）。
"""
from __future__ import annotations

import hashlib
import uuid as uuidlib
from dataclasses import dataclass, field

from sqlmodel import select

from ..adapters.base import AssignmentDraft, CourseDraft
from .db import Database
from .models import Assignment, Course, NotificationLog
from .normalize import format_local, now_utc, to_utc

_SAFE_URL_SCHEMES = ("http", "https")


def _safe_external_url(u: str | None) -> str | None:
    """SEC-01：平台数据里的链接只允许 http/https（防 javascript: 同源 XSS）。"""
    if not u:
        return None
    from urllib.parse import urlsplit

    parts = urlsplit(u)
    return u if parts.scheme in _SAFE_URL_SCHEMES and parts.netloc else None


def natural_source_id(draft: AssignmentDraft) -> str:
    return f"{draft.course_source_id}::{draft.source_id}"


@dataclass
class UpsertStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    # 待发变更通知 (summary, assignment_id, dedupe_key)
    notifications: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.unchanged


@dataclass
class CourseRef:
    id: int
    enabled: bool = True


def _claim_pending(
    s, channel_names: list[str], *, assignment_id: int | None, kind: str,
    dedupe_key: str, summary: str, minutes_before: int | None = None, now=None,
) -> None:
    """outbox：在业务事务内写 pending 通知（R-P2-3），由 send_pending 统一发送。"""
    now = now or now_utc()
    for name in channel_names:
        dup = s.exec(
            select(NotificationLog).where(
                NotificationLog.dedupe_key == dedupe_key,
                NotificationLog.channel == name,
            )
        ).first()
        if dup:
            continue  # R-P2-6：存在即跳过；竞态余量由 begin_nested 兜底
        try:
            with s.begin_nested():
                s.add(NotificationLog(
                    assignment_id=assignment_id, channel=name, kind=kind,
                    minutes_before=minutes_before, dedupe_key=dedupe_key,
                    status="pending", summary=summary[:1000], created_at=now,
                ))
        except Exception:
            continue  # 并发撞 UNIQUE：已被他方 claim


def upsert_courses(db: Database, account_id: int, drafts: list[CourseDraft]) -> dict[str, CourseRef]:
    """返回 {course_source_id: CourseRef}。课程行全量 upsert（含已关闭的，供设置页展示）。"""
    mapping: dict[str, CourseRef] = {}
    with db.session() as s:
        for d in drafts:
            row = s.exec(
                select(Course).where(
                    Course.account_id == account_id, Course.source_id == d.source_id
                )
            ).first()
            if row is None:
                row = Course(
                    account_id=account_id, source_id=d.source_id,
                    name=d.name, term=d.term, url=d.url, enabled=True,
                )
                s.add(row)
                s.flush()
            else:
                row.name = d.name
                row.term = d.term
                if d.url:
                    row.url = d.url
            mapping[d.source_id] = CourseRef(id=row.id, enabled=bool(row.enabled))
        s.commit()
    return mapping


def upsert_assignments(
    db: Database,
    account_id: int,
    course_id: int,
    course_source_id: str,
    drafts: list[AssignmentDraft],
    tz_name: str,
    *,
    notify_new: bool,
    channel_names: list[str] | None = None,   # outbox：变更/新增通知在同事务写 pending
) -> UpsertStats:
    stats = UpsertStats()
    now = now_utc()
    with db.session() as s:
        for d in drafts:
            d.source_url = _safe_external_url(d.source_url)  # SEC-01
            source_id = natural_source_id(d)
            due = to_utc(d.due_at, tz_name)
            published = to_utc(d.published_at, tz_name)

            row = s.exec(
                select(Assignment).where(
                    Assignment.account_id == account_id,
                    Assignment.source_id == source_id,
                )
            ).first()

            if row is None:
                row = Assignment(
                    course_id=course_id,
                    account_id=account_id,
                    source_id=source_id,
                    title=d.title,
                    description=d.description,
                    due_at=due,
                    published_at=published,
                    status=d.status,
                    source_url=d.source_url,
                    estimated_minutes=d.estimated_minutes,
                    priority=d.priority,
                    change_hash="",
                    first_seen_at=now,
                    updated_at=now,
                )
                s.add(row)
                s.flush()
                stats.created += 1
                if notify_new and channel_names:
                    key = f"a{row.id}:change:new:{uuidlib.uuid4().hex[:8]}"
                    _claim_pending(
                        s, channel_names, assignment_id=row.id, kind="change",
                        dedupe_key=key, summary=f"新增作业：{d.title}", now=now,
                    )
                continue

            changes: list[tuple[str, str, str]] = []
            # 相对倒计时还原的绝对截止时间有分钟级捕获误差（学习通只给"剩余X小时"），
            # 30 分钟内视为同一截止，避免每次同步误报 DDL 变更
            due_changed = (
                due is not None
                and row.due_at is not None
                and abs((due - row.due_at).total_seconds()) > 1800
            ) or (due is None) != (row.due_at is None)
            # R-P1-2：平台对已截止作业只给"已截止"不给绝对时间——保留既有 due_at，
            # 绝不写成 NULL（否则逾期提醒永久失效）
            keep_due = due is None and row.due_at is not None
            if keep_due:
                due_changed = False
            if due_changed:
                old = format_local(row.due_at, tz_name) if row.due_at else "（无）"
                new = format_local(due, tz_name) if due else "（无）"
                changes.append(("DDL", old, new))
            title_changed = row.title != d.title
            if title_changed:
                changes.append(("标题", row.title, d.title))
            status_changed = row.status != d.status
            if status_changed and d.status == "closed":
                changes.append(("状态", row.status, "closed"))

            # 平台字段始终与平台对齐；本地字段（local_* / pinned）从不触碰
            row.title = d.title
            row.status = d.status
            row.priority = d.priority
            if d.description is not None:
                row.description = d.description
            if d.source_url:
                row.source_url = d.source_url
            if d.estimated_minutes is not None:
                row.estimated_minutes = d.estimated_minutes
            if due_changed:
                row.due_at_prev = row.due_at
                row.due_at = due
            row.updated_at = now

            if changes:
                digest = hashlib.sha1(
                    "|".join(f"{k}:{o}->{n}" for k, o, n in changes).encode("utf-8")
                ).hexdigest()[:20]
                row.change_hash = digest
                s.flush()
                summary = f"「{d.title}」" + "；".join(
                    f"{k}从 {o} 改为 {n}" for k, o, n in changes
                )
                if channel_names:
                    # R-P2-4：键含时间戳——同一变更再次发生（A→B→A→B）每次都应通知
                    _claim_pending(
                        s, channel_names, assignment_id=row.id, kind="change",
                        dedupe_key=f"a{row.id}:change:{digest}:{uuidlib.uuid4().hex[:8]}",
                        summary=summary, now=now,
                    )

            if due_changed or title_changed or status_changed:
                stats.updated += 1
            else:
                stats.unchanged += 1
        s.commit()
    return stats
