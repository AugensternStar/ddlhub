"""DDL 变更检测：改期→恰好一条 change 通知；change_hash 防重发；首次同步静默导入。

通知流程按生产路径：upsert 收集 stats.notifications → 提交后逐条 dispatch（claim-then-send）。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import select

from ddlhub.adapters.base import AssignmentDraft, CourseDraft
from ddlhub.core.models import Account, Assignment, NotificationLog
from ddlhub.core.reminders import dispatch_notification
from ddlhub.core.upsert import upsert_assignments, upsert_courses

SH = ZoneInfo("Asia/Shanghai")


def _draft(lid="HW1", title="作业一", due=None):
    return AssignmentDraft(source_id=lid, course_source_id="C1", title=title, due_at=due)


def _upsert(db, acc, cid, drafts, container, notify_new=True):
    """outbox 流程：upsert 在业务事务内写 pending claim，随后 send_pending 统一发送。"""
    stats = upsert_assignments(
        db, acc.id, cid, "C1", drafts, "Asia/Shanghai", notify_new=notify_new,
        channel_names=["desktop"],
    )
    from ddlhub.core.reminders import send_pending

    send_pending(container)
    return stats


def _course(db, acc) -> int:
    return upsert_courses(db, acc.id, [CourseDraft(source_id="C1", name="课程一")])["C1"].id


def _notifs(db) -> list[NotificationLog]:
    with db.session() as s:
        return s.exec(select(NotificationLog).order_by(NotificationLog.id)).all()


def test_first_sync_imports_silently(container, db, mock_account):
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = _course(db, acc)
    due = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    _upsert(db, acc, cid, [_draft(due=due)], container, notify_new=False)
    assert _notifs(db) == []  # 首次同步不轰炸


def test_due_change_emits_exactly_one_notification(container, db, mock_account):
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = _course(db, acc)
    base = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    _upsert(db, acc, cid, [_draft(due=base)], container, notify_new=False)
    assert _notifs(db) == []

    moved = base + timedelta(days=2)
    _upsert(db, acc, cid, [_draft(due=moved)], container)
    rows = _notifs(db)
    assert len(rows) == 1
    assert rows[0].kind == "change"
    assert rows[0].status == "sent"
    assert rows[0].dedupe_key.startswith(f"a{rows[0].assignment_id}:change:")
    with db.session() as s:
        row = s.exec(select(Assignment)).one()
        assert row.due_at == datetime(2026, 4, 3, 0, 0)       # moved 的 UTC（上海 08:00+2d）
        assert row.due_at_prev == datetime(2026, 4, 1, 0, 0)  # 原 DDL 的 UTC


def test_same_change_not_renotified(container, db, mock_account):
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = _course(db, acc)
    base = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    moved = base + timedelta(days=2)
    for i, due in enumerate((base, moved, moved)):
        _upsert(db, acc, cid, [_draft(due=due)], container, notify_new=i > 0)
    assert len(_notifs(db)) == 1  # 同一变更重放不重发


def test_a_b_a_notifies_each_flip(container, db, mock_account):
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = _course(db, acc)
    base = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    moved = base + timedelta(days=2)
    for i, due in enumerate((base, moved, base, moved)):
        _upsert(db, acc, cid, [_draft(due=due)], container, notify_new=i > 0)
    # 修复 R-P2-4（时间戳键）：A→B→A→B 三次翻转全部通知
    # （旧实现第二次 B 与第一次 B 同 digest 同键，会被静默吞掉——只剩 2 条）
    assert len(_notifs(db)) == 3


def test_title_and_due_merged_into_one(container, db, mock_account):
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = _course(db, acc)
    _upsert(db, acc, cid, [_draft(due=datetime(2026, 4, 1, 8, 0, tzinfo=SH))], container, notify_new=False)
    _upsert(db, acc, cid,
            [_draft(title="作业一（改名）", due=datetime(2026, 4, 2, 8, 0, tzinfo=SH))], container)
    rows = _notifs(db)
    assert len(rows) == 1  # 标题+DDL 同步都变 → 合并为一条（设计 §14.2）
    assert "改名" in rows[0].summary and "DDL" in rows[0].summary


def test_new_assignment_after_first_sync_notifies(container, db, mock_account):
    with db.session() as s:
        acc = s.get(Account, mock_account)
    cid = _course(db, acc)
    base = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    _upsert(db, acc, cid, [_draft(due=base)], container, notify_new=False)
    _upsert(db, acc, cid, [_draft(due=base), _draft(lid="HW2", title="新作业")], container)
    rows = _notifs(db)
    assert len(rows) == 1 and "新增作业" in rows[0].summary
