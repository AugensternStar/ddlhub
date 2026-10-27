"""页面路由：三视图（时间线/列表/日历）+ 筛选，服务端渲染 + htmx 局部刷新。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import APIRouter, Query, Request
from sqlmodel import select

from ..adapters.registry import REGISTRY
from ..core.models import Account, Assignment, Course, SyncRun
from ..core.normalize import (
    format_date_local,
    format_local,
    from_utc,
    is_overdue,
    now_utc,
    remaining_text,
)

router = APIRouter()

VIEWS = ("timeline", "list", "calendar")
STATUS_TEXT = {"ok": "正常", "need_relogin": "需要重新登录", "disabled": "已停用"}


def container_of(request: Request):
    return request.app.state.container


@dataclass
class Row:
    a: Assignment
    course_name: str
    platform: str
    is_overdue: bool
    remaining: str
    due_local: str
    due_iso_local: str
    done: bool
    offset_preset: str = "default"


@dataclass
class Group:
    label: str
    rows: list = field(default_factory=list)
    overdue: bool = False


def _build_row(container, a: Assignment, course: Course | None, account: Account | None) -> Row:
    now = now_utc()
    local = from_utc(a.due_at, container.settings.timezone)
    preset = "default"
    if a.reminder_offsets_json == "[]":
        preset = "off"
    elif a.reminder_offsets_json == "[1440,60]":
        preset = "light"
    elif a.reminder_offsets_json == "[720,180,60]":
        preset = "urgent"
    elif a.reminder_offsets_json == "[10080,4320,1440,360,60]":
        preset = "full"
    return Row(
        a=a,
        course_name=course.name if course else "（课程已删除）",
        platform=account.label if account else "未知平台",
        is_overdue=is_overdue(a.due_at, now),
        remaining=remaining_text(a.due_at, now),
        due_local=format_local(a.due_at, container.settings.timezone),
        due_iso_local=local.isoformat() if local else "",
        done=bool(a.local_done or a.status == "submitted"),
        offset_preset=preset,
    )


def _rows(container, *, account_id, course_id, status_f, days7) -> list[Row]:
    now = now_utc()
    with container.db.session() as s:
        stmt = select(Assignment)
        if status_f == "open":
            stmt = stmt.where(
                Assignment.local_done == False,  # noqa: E712
                Assignment.local_ignored == False,  # noqa: E712
                Assignment.status != "submitted",
            )
        elif status_f == "done":
            stmt = stmt.where(
                (Assignment.local_done == True) | (Assignment.status == "submitted")  # noqa: E712
            )
        elif status_f == "ignored":
            stmt = stmt.where(Assignment.local_ignored == True)  # noqa: E712
        if account_id:
            stmt = stmt.where(Assignment.account_id == account_id)
        if course_id:
            stmt = stmt.where(Assignment.course_id == course_id)
        if days7:
            stmt = stmt.where(
                Assignment.due_at.is_not(None),
                Assignment.due_at <= now + timedelta(days=7),
            )
        assignments = s.exec(stmt).all()
        course_map = {co.id: co for co in s.exec(select(Course)).all()}
        account_map = {a.id: a for a in s.exec(select(Account)).all()}

    # 课程选择：已关闭课程的数据保留但不在任何视图出现
    assignments = [a for a in assignments
                   if course_map.get(a.course_id) is not None and course_map[a.course_id].enabled]
    rows = [_build_row(container, a, course_map.get(a.course_id), account_map.get(a.account_id))
            for a in assignments]
    # 默认排序：置顶在前 → 剩余时间升序 → 无截止最后
    rows.sort(key=lambda r: (not r.a.pinned, r.a.due_at is None, r.a.due_at or now))
    return rows


def _groups(rows: list[Row], tz_name: str) -> list[Group]:
    groups: list[Group] = []
    overdue = [r for r in rows if r.a.due_at is not None and r.is_overdue]
    future = [r for r in rows if r.a.due_at is not None and not r.is_overdue]
    nodue = [r for r in rows if r.a.due_at is None]
    if overdue:
        groups.append(Group("已逾期", overdue, True))
    by_date: dict[str, list[Row]] = {}
    for r in future:
        by_date.setdefault(format_date_local(r.a.due_at, tz_name), []).append(r)
    for label in sorted(by_date):
        groups.append(Group(label, by_date[label]))
    if nodue:
        groups.append(Group("无截止时间", nodue))
    return groups


def _statusbar_ctx(container, msg: str = "") -> dict:
    with container.db.session() as s:
        accs = s.exec(select(Account).order_by(Account.id)).all()
        last = s.exec(select(SyncRun).order_by(SyncRun.started_at.desc())).first()
    accounts = []
    for a in accs:
        cls = REGISTRY.get(a.adapter_key)
        accounts.append({
            "id": a.id,
            "label": a.label,
            "status": a.status,
            "status_text": STATUS_TEXT.get(a.status, a.status),
            "scenario_buttons": bool(cls and cls.scenario_buttons),
            "last_ok": format_local(a.last_ok_at, container.settings.timezone),
        })
    return {
        "accounts": accounts,
        "last_sync_text": (
            format_local(last.finished_at or last.started_at, container.settings.timezone)
            if last else "尚未同步"
        ),
        "msg": msg,
        "adapters": sorted((k, cls.name) for k, cls in REGISTRY.items()),
    }


@router.get("/settings")
def settings_page(request: Request, msg: str = Query("")):
    """设置页：平台管理（添加/删除/重新登录/演示场景）+ 按账号的课程选择。"""
    c = container_of(request)
    ctx = _statusbar_ctx(c, msg)
    ctx["view"] = "settings"
    from ..notify import build_notifiers, get_channels_enabled

    ctx["channels"] = get_channels_enabled(c.db)
    ctx["email_ready"] = build_notifiers(c.settings)["email"].available()
    with c.db.session() as s:
        accs = s.exec(select(Account).order_by(Account.id)).all()
        courses = s.exec(select(Course).order_by(Course.name)).all()
    by_account: dict[int, list] = {}
    for co in courses:
        by_account.setdefault(co.account_id, []).append(co)
    ctx["platform_courses"] = [
        {
            "account": a,
            "courses": by_account.get(a.id, []),
            "enabled_count": sum(1 for co in by_account.get(a.id, []) if co.enabled),
        }
        for a in accs
    ]
    return c.templates.TemplateResponse(request, "settings.html", ctx)


def _int_or_none(v: str | None) -> int | None:
    """筛选表单的空串（"全部平台/全部课程"的 value=""）安全转 None。"""
    if v in (None, ""):
        return None
    try:
        return int(v)
    except ValueError:
        return None


@router.get("/")
def index(
    request: Request,
    view: str = Query("timeline"),
    account: str = Query(""),
    course: str = Query(""),
    status: str = Query("open"),
    days7: bool = Query(False),
    msg: str = Query(""),
):
    c = container_of(request)
    view = view if view in VIEWS else "timeline"
    account_id = _int_or_none(account)
    course_id = _int_or_none(course)
    rows = _rows(c, account_id=account_id, course_id=course_id, status_f=status, days7=days7)

    ctx = _statusbar_ctx(c, msg)
    with c.db.session() as s:
        ctx["courses"] = [
            {"id": co.id, "name": co.name}
            for co in s.exec(select(Course).where(Course.enabled == True).order_by(Course.name)).all()  # noqa: E712
        ]
    ctx.update({
        "view": view,
        "filters": {"account": account_id, "course": course_id, "status": status, "days7": days7},
    })

    if view == "calendar":
        events = []
        for r in rows:
            if r.a.due_at is None:
                continue
            classes = ["ev-overdue" if r.is_overdue else ("ev-done" if r.done else "")]
            if r.a.priority:
                classes.append("ev-high")
            events.append({
                "title": f"{r.course_name} · {r.a.title}",
                "start": r.due_iso_local,
                "url": r.a.source_url,
                "classNames": [c_ for c_ in classes if c_],
            })
        ctx["events"] = events
    elif view == "timeline":
        ctx["groups"] = _groups(rows, c.settings.timezone)
    else:
        ctx["flat_rows"] = rows
    return c.templates.TemplateResponse(request, "index.html", ctx)
