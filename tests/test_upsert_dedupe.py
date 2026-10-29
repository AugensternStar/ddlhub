"""幂等 upsert：重复同步不重复入库、组合自然键、本地字段不被覆盖。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import select

from ddlhub.adapters.base import AssignmentDraft, CourseDraft
from ddlhub.core.models import Account, Assignment
from ddlhub.core.normalize import now_utc
from ddlhub.core.upsert import natural_source_id, upsert_assignments, upsert_courses

SH = ZoneInfo("Asia/Shanghai")


def _account(db) -> Account:
    with db.session() as s:
        acc = Account(uuid="u-test", adapter_key="mock", label="测试",
                      created_at=now_utc())
        s.add(acc)
        s.commit()
        return acc


def _course(db, acc, source_id="C1") -> int:
    return upsert_courses(db, acc.id, [CourseDraft(source_id=source_id, name="课程一")])[source_id].id


def _draft(lid="HW1", title="作业一", due=None, status="open"):
    return AssignmentDraft(
        source_id=lid, course_source_id="C1", title=title,
        due_at=due, status=status, estimated_minutes=60,
    )


def _upsert(db, acc, cid, drafts):
    return upsert_assignments(db, acc.id, cid, "C1", drafts, "Asia/Shanghai", notify_new=False)


def test_natural_key_composition():
    d = _draft()
    assert natural_source_id(d) == "C1::HW1"
    d2 = AssignmentDraft(source_id="HW1", course_source_id="C2", title="x")
    assert natural_source_id(d2) == "C2::HW1"


def test_repeat_sync_idempotent(db):
    acc = _account(db)
    cid = _course(db, acc)
    due = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    drafts = [_draft(due=due)]

    s1 = _upsert(db, acc, cid, drafts)
    assert (s1.created, s1.updated, s1.unchanged) == (1, 0, 0)

    s2 = _upsert(db, acc, cid, drafts)
    assert (s2.created, s2.updated, s2.unchanged) == (0, 0, 1)

    with db.session() as s:
        rows = s.exec(select(Assignment)).all()
        assert len(rows) == 1
        assert rows[0].source_id == "C1::HW1"
        assert rows[0].due_at == datetime(2026, 4, 1, 0, 0)  # naive UTC


def test_local_fields_never_overwritten(db):
    acc = _account(db)
    cid = _course(db, acc)
    due = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    _upsert(db, acc, cid, [_draft(due=due)])
    with db.session() as s:
        row = s.exec(select(Assignment)).one()
        row.local_done = True
        row.pinned = True
        s.commit()

    # 平台侧字段完全一致 + 本地已改 → 再次同步仍 unchanged，本地字段保留
    s2 = _upsert(db, acc, cid, [_draft(due=due)])
    assert s2.unchanged == 1
    with db.session() as s:
        row = s.exec(select(Assignment)).one()
        assert row.local_done and row.pinned


def test_due_change_tolerance_for_relative_countdown(db):
    """学习通给出的是相对倒计时还原的绝对时间，分钟级误差不应算 DDL 变更。"""
    from sqlmodel import select

    from ddlhub.core.models import NotificationLog

    acc = _account(db)
    cid = _course(db, acc)
    due = datetime(2026, 4, 1, 8, 0, tzinfo=SH)
    upsert_assignments(db, acc.id, cid, "C1", [_draft(due=due)], "Asia/Shanghai",
                       notify_new=False, channel_names=[])

    # ±10 分钟：同一截止，不产生变更
    s1 = upsert_assignments(db, acc.id, cid, "C1",
                            [_draft(due=due + timedelta(minutes=10))], "Asia/Shanghai",
                            notify_new=False, channel_names=[])
    assert s1.unchanged == 1

    # ±2 小时：真变更 → updated + outbox 落 1 条 pending claim
    s2 = upsert_assignments(db, acc.id, cid, "C1",
                            [_draft(due=due + timedelta(hours=2))], "Asia/Shanghai",
                            notify_new=False, channel_names=["desktop"])
    assert s2.updated == 1
    claims = db.session().exec(
        select(NotificationLog).where(NotificationLog.status == "pending")
    ).all()
    assert len(claims) == 1 and claims[0].kind == "change"


def test_status_aligns_with_platform_without_noise(db):
    acc = _account(db)
    cid = _course(db, acc)
    _upsert(db, acc, cid, [_draft()])
    # 平台侧变为 submitted：状态跟随平台并计为 updated，但不发变更通知（仅 →closed 通知）
    s2 = _upsert(db, acc, cid, [_draft(status="submitted")])
    assert s2.updated == 1
    assert s2.notifications == []
    with db.session() as s:
        assert s.exec(select(Assignment)).one().status == "submitted"
