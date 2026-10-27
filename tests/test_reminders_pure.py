"""提醒纯函数：档位计算、scope 过滤、到期节点（引擎调度任务在阶段 4 接入）。"""
from datetime import datetime, timedelta

from ddlhub.core.models import Assignment
from ddlhub.core.normalize import now_utc
from ddlhub.core.reminders import due_nodes, effective_done, in_scope, offsets_for

DEFAULTS = (10080, 4320, 1440, 360, 60)


def _assignment(**kw) -> Assignment:
    base = dict(
        id=1, course_id=0, account_id=0, source_id="C::A", title="t",
        first_seen_at=now_utc() - timedelta(days=10), updated_at=now_utc(),
    )
    base.update(kw)
    return Assignment(**base)


def test_offsets_priority_inserts_t12h():
    a = _assignment(priority=1)
    offs = offsets_for(a, DEFAULTS)
    assert 720 in offs
    assert offs == sorted(offs, reverse=True)

    a2 = _assignment(priority=0)
    assert 720 not in offsets_for(a2, DEFAULTS)


def test_offsets_per_assignment_override():
    a = _assignment(reminder_offsets_json="[60, 30]")
    assert offsets_for(a, DEFAULTS) == [60, 30]


def test_scope_excludes_submitted_done_ignored_nodue():
    due = now_utc() + timedelta(days=1)
    assert in_scope(_assignment(due_at=due))
    assert not in_scope(_assignment(due_at=due, status="submitted"))       # 平台已提交即停
    assert not in_scope(_assignment(due_at=due, local_done=True))
    assert not in_scope(_assignment(due_at=due, local_ignored=True))
    assert not in_scope(_assignment(due_at=None))                          # 无截止永不提醒
    assert effective_done(_assignment(status="submitted"))


def test_due_nodes_boundaries():
    now = now_utc()
    # 3 天后截止：T-7d(10080) 与 T-3d(4320) 已到期（7d 节点属错过，由补发策略取舍）
    due = now + timedelta(days=3)
    a = _assignment(due_at=due, priority=0)
    assert [mb for mb, _ in due_nodes(a, DEFAULTS, now)] == [4320, 10080]

    # 10 小时后截止：T-7d/T-3d/T-1d 已到期，T-6h/T-1h 未到
    near = _assignment(due_at=now + timedelta(hours=10))
    assert [mb for mb, _ in due_nodes(near, DEFAULTS, now)] == [1440, 4320, 10080]

    # 30 天后截止：全部未到期
    future = _assignment(due_at=now + timedelta(days=30))
    assert due_nodes(future, DEFAULTS, now) == []


def test_due_nodes_excludes_out_of_scope():
    now = now_utc()
    a = _assignment(due_at=now + timedelta(days=1), status="submitted")
    assert due_nodes(a, DEFAULTS, now) == []


def test_overdue_guard_rules():
    from ddlhub.core.reminders import overdue_node
    now = now_utc()
    # 提前见过、刚逾期 → 提醒
    seen_early = _assignment(due_at=now - timedelta(days=1),
                             first_seen_at=now - timedelta(days=5))
    assert overdue_node(seen_early, now) == -1
    # 首次同步导入的历史作业（seen 晚于 due）→ 不提醒
    imported = _assignment(due_at=now - timedelta(days=3),
                           first_seen_at=now - timedelta(days=1))
    assert overdue_node(imported, now) is None
    # 逾期超过 7 天 → 不提醒
    ancient = _assignment(due_at=now - timedelta(days=30),
                          first_seen_at=now - timedelta(days=40))
    assert overdue_node(ancient, now) is None
