"""提醒引擎集成：补发护栏、完成即停、overdue 双护栏、渠道去重（验收项）。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import select

from ddlhub.adapters.base import AssignmentDraft, CourseDraft
from ddlhub.core.models import Account, Assignment, NotificationLog
from ddlhub.core.normalize import now_utc
from ddlhub.core.reminders import run_reminder_tick
from ddlhub.core.upsert import upsert_assignments, upsert_courses

SH = ZoneInfo("Asia/Shanghai")


def _seed_assignment(container, db, account_id, *, due_delta, title="作业A",
                     first_seen_delta=None, status="open", source_id="C1::A1",
                     local_done=False):
    with db.session() as s:
        acc = s.get(Account, account_id)
    cid = upsert_courses(db, acc.id, [CourseDraft(source_id="C1", name="课程一")])["C1"].id
    draft = AssignmentDraft(
        source_id=source_id.split("::")[-1], course_source_id="C1", title=title,
        due_at=datetime.now(SH) + due_delta, status=status,
    )
    upsert_assignments(db, acc.id, cid, "C1", [draft], "Asia/Shanghai", notify_new=False)
    with db.session() as s:
        row = s.exec(select(Assignment).where(Assignment.source_id == source_id)).one()
        if first_seen_delta is not None:
            row.first_seen_at = now_utc() + first_seen_delta
        if local_done:
            row.local_done = True
        s.commit()
        return row.id


def _notif_rows(db):
    with db.session() as s:
        return s.exec(select(NotificationLog).order_by(NotificationLog.id)).all()


def test_tick_sends_latest_missed_node_only(container, mock_account):
    """截止剩 2h：T-6h/3d/7d 已错过 → 只发 T-6h，更早的 skipped（补发护栏）。"""
    _seed_assignment(container, container.db, mock_account, due_delta=timedelta(hours=2))

    assert run_reminder_tick(container) == 1
    rows = _notif_rows(container.db)
    statuses = {(r.channel, r.kind, r.status, r.minutes_before) for r in rows}
    assert ("desktop", "reminder", "sent", 360) in statuses      # 最近节点已发送
    assert {r.minutes_before for r in rows if r.status == "skipped"} == {1440, 4320, 10080}

    # 第二次 tick：全部去重命中，不再发送（不重复轰炸）
    before = len(_notif_rows(container.db))
    assert run_reminder_tick(container) == 0
    assert len(_notif_rows(container.db)) == before


def test_completed_and_submitted_stop_reminders(container, mock_account):
    db = container.db
    aid_open = _seed_assignment(container, db, mock_account, due_delta=timedelta(hours=2),
                                source_id="C1::OPEN")
    done_id = _seed_assignment(container, db, mock_account, due_delta=timedelta(hours=2),
                               source_id="C1::DONE", title="作业DONE", local_done=True)
    _seed_assignment(container, db, mock_account, due_delta=timedelta(hours=2),
                     source_id="C1::SUBMITTED", title="作业SUBMITTED", status="submitted")

    run_reminder_tick(container)
    sent_aids = {r.assignment_id for r in _notif_rows(db) if r.status == "sent"}
    assert aid_open in sent_aids
    assert done_id not in sent_aids          # 本地完成 → 停
    sub_id = {r.assignment_id for r in _notif_rows(db) if r.kind == "reminder"}
    assert all(x in (aid_open,) for x in sent_aids)  # submitted 的作业根本没有节点


def test_overdue_double_guard(container, mock_account):
    """逾期且提前见过 → 提醒一次；重复 tick 不再发；首次导入的历史逾期不发。"""
    db = container.db
    _seed_assignment(container, db, mock_account, due_delta=timedelta(days=-1),
                     first_seen_delta=timedelta(days=-5), source_id="C1::LATE")

    assert run_reminder_tick(container) == 1
    assert "overdue" in {r.kind for r in _notif_rows(db) if r.status == "sent"}
    assert run_reminder_tick(container) == 0  # dedupe：只提醒一次

    _seed_assignment(container, db, mock_account, due_delta=timedelta(days=-2),
                     first_seen_delta=timedelta(days=-1), source_id="C1::IMPORTED")
    assert run_reminder_tick(container) == 0  # 首次导入的历史逾期 → 不发


def test_per_assignment_offset_override(container, mock_account):
    """单作业覆盖档位：off=不提醒；urgent 前插 T-12h。"""
    db = container.db
    _seed_assignment(container, db, mock_account, due_delta=timedelta(hours=2),
                     source_id="C1::OFF", title="作业OFF")
    with db.session() as s:
        row = s.exec(select(Assignment).where(Assignment.source_id == "C1::OFF")).one()
        row.reminder_offsets_json = "[]"  # 不提醒
        s.commit()
    assert run_reminder_tick(container) == 0

    with db.session() as s:
        row = s.exec(select(Assignment).where(Assignment.source_id == "C1::OFF")).one()
        row.reminder_offsets_json = "[720,180,60]"  # 冲刺档
        s.commit()
    # 剩 2h：冲刺档里 T-3h(180min) 已错过且为最近 → 发 180
    assert run_reminder_tick(container) == 1
    assert 180 in {r.minutes_before for r in _notif_rows(db) if r.status == "sent"}


def test_no_channels_no_sends(container, mock_account, fake_notifier):
    _seed_assignment(container, container.db, mock_account, due_delta=timedelta(hours=2),
                     source_id="C1::NOCH")
    container.channel_override = {}          # 所有渠道关闭
    assert run_reminder_tick(container) == 0
    assert _notif_rows(container.db) == []   # 无渠道时不产生任何 claim
    container.channel_override = {"desktop": fake_notifier}
