"""操作/API 路由：作业操作、同步、账号管理、课程/渠道开关、测试通知。

全部走 harden 中间件（非 GET 需同源 + 会话 cookie）。htmx 请求返回局部片段，
普通表单提交返回 303 重定向。
"""
from __future__ import annotations

import json
import uuid as uuidlib
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlmodel import select

from ..adapters.registry import REGISTRY
from ..core.models import Account, Assignment, Course, NotificationLog
from ..core.normalize import now_utc
from ..core.redact import redact
from ..core.security import mask_username
from ..core.sync import sync_account, sync_all
from ..core.interactive import LoginState
from .routes_ui import Row, _build_row, _statusbar_ctx, container_of

router = APIRouter()

def _statusbar(request: Request, msg: str = ""):
    c = container_of(request)
    return c.templates.TemplateResponse(request, "_statusbar.html", _statusbar_ctx(c, msg))


# ---------- 作业操作 ----------

@router.post("/api/assignments/{aid}/action")
def assignment_action(aid: int, request: Request, action: str = Form(...)):
    c = container_of(request)
    with c.db.session() as s:
        a = s.get(Assignment, aid)
        if a is None:
            return PlainTextResponse("not found", status_code=404)
        if action == "done":
            a.local_done = True
        elif action == "undo":
            a.local_done = False
        elif action == "ignore":
            a.local_ignored = True
        elif action == "unignore":
            a.local_ignored = False
        elif action in ("pin", "unpin"):
            a.pinned = not a.pinned
        else:
            return PlainTextResponse("bad action", status_code=400)
        course = s.get(Course, a.course_id)
        account = s.get(Account, a.account_id)
        s.commit()
    row: Row = _build_row(c, a, course, account)
    return c.templates.TemplateResponse(request, "_card.html", {"row": row})


# ---------- 同步 ----------

@router.post("/api/sync")
def sync_now(request: Request, account_id: int | None = Form(None)):
    c = container_of(request)
    if account_id:
        outcome = sync_account(c, account_id, "manual")
        msg = outcome.message
    else:
        outcomes = sync_all(c, "manual")
        ok_count = sum(1 for o in outcomes if o.ok)
        msg = f"同步完成：{ok_count}/{len(outcomes)} 个账号成功"
        for o in outcomes:
            if not o.ok:
                msg += f"｜{o.message}"
    return _statusbar(request, msg)


# ---------- 账号管理 ----------

@router.post("/api/accounts")
def create_account(
    request: Request,
    adapter_key: str = Form(...),
    label: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    entry_url: str = Form(""),
    cookie_domain: str = Form(""),
    portal_url: str = Form(""),
):
    c = container_of(request)
    if adapter_key not in REGISTRY:
        return RedirectResponse("/?msg=" + quote("未知平台类型"), status_code=303)
    config_json = None
    if adapter_key == "custom":
        entry_url = entry_url.strip()
        if not entry_url.startswith(("http://", "https://")):
            return RedirectResponse("/?msg=" + quote("自定义平台需要填写正确的登录页 URL（http/https）"), status_code=303)
        try:
            _host = urlsplit(entry_url).hostname  # CR-08：畸形 URL（如未闭合 IPv6）在创建时即拒绝
        except ValueError:
            return RedirectResponse("/?msg=" + quote("登录页 URL 无法解析，请检查格式"), status_code=303)
        if not _host:
            return RedirectResponse("/?msg=" + quote("登录页 URL 缺少主机名"), status_code=303)
        host = _host
        parts = host.split(".")
        second_level = {"edu.cn", "com.cn", "org.cn", "net.cn", "gov.cn", "ac.cn"}
        if cookie_domain.strip():
            cookie_domain = cookie_domain.strip()
        elif len(parts) > 2 and ".".join(parts[-2:]) in second_level:
            cookie_domain = "." + ".".join(parts[-3:])
        elif len(parts) > 2:
            cookie_domain = "." + ".".join(parts[-2:])
        else:
            cookie_domain = host
        config_json = json.dumps({
            "entry_url": entry_url,
            "cookie_domain": cookie_domain,
            "portal_url": portal_url.strip(),
        }, ensure_ascii=False)
    account_uuid = str(uuidlib.uuid4())
    hint = ""
    if username or password:
        try:
            c.credential_store.store(adapter_key, account_uuid, username, password)
            hint = mask_username(username)
        except Exception as exc:
            c.logger.exception("credential store failed")
            return RedirectResponse("/?msg=" + quote(redact(f"凭据保存失败：{exc}")), status_code=303)
    with c.db.session() as s:
        acc = Account(
            uuid=account_uuid, adapter_key=adapter_key, label=label[:128],
            username_hint=hint, status="ok", created_at=now_utc(),
            config_json=config_json,
        )
        s.add(acc)
        s.commit()
        aid = acc.id
    c.logger.info("account created: adapter=%s label=%s user=%s", adapter_key, label, hint)
    cls = REGISTRY[adapter_key]
    if getattr(cls, "login_mode", "credentials") == "interactive":
        # 学习通类平台：不立即同步（必然 need_relogin），直接弹浏览器人工登录一次
        if c.interactive:
            c.interactive.start(aid)
            return RedirectResponse(
                "/?msg=" + quote("账号已创建：请在弹出的浏览器中完成一次登录（3 分钟内），完成后会自动同步"),
                status_code=303,
            )
        return RedirectResponse("/?msg=" + quote("账号已创建。有头登录组件未就绪，请到设置页点击「打开浏览器登录」"), status_code=303)
    outcome = sync_account(c, aid, "manual")
    return RedirectResponse("/?msg=" + quote(f"账号已创建。{outcome.message}"), status_code=303)


@router.post("/api/accounts/{aid}/delete")
def delete_account(aid: int, request: Request):
    c = container_of(request)
    with c.db.session() as s:
        account = s.get(Account, aid)
        if account is None:
            return RedirectResponse("/", status_code=303)
        adapter_key, account_uuid, label = account.adapter_key, account.uuid, account.label
        s.delete(account)  # 课程/作业/会话/同步记录由外键级联删除
        s.commit()
    try:
        c.credential_store.delete(adapter_key, account_uuid)
    except Exception:
        c.logger.exception("credential delete failed for %s", label)
    c.logger.info("account deleted: %s（凭据与全部数据已清除）", label)
    return RedirectResponse("/?msg=" + quote("账号已删除，凭据与数据已清除"), status_code=303)


@router.post("/api/accounts/{aid}/relogin")
def relogin(aid: int, request: Request):
    c = container_of(request)
    msg = "账号不存在"
    with c.db.session() as s:
        account = s.get(Account, aid)
        cls = REGISTRY.get(account.adapter_key) if account else None
        if account is None or cls is None:
            return _statusbar(request, msg)
        if getattr(cls, "login_mode", "credentials") == "interactive":
            if not c.interactive:
                return _statusbar(request, "有头登录组件未就绪（服务启动方式异常）")
            c.interactive.start(aid)
            return _statusbar(request, "已打开浏览器：请在窗口完成登录（3 分钟内），成功后自动同步")
        adapter = cls(c.adapter_ctx(aid))
        try:
            result = adapter.relogin()
        finally:
            adapter.close()
        if result.ok:
            account.status = "ok"
            account.last_error = ""
            s.commit()
            msg = result.message
        else:
            msg = result.message or "需要人工重新登录"
    sync_account(c, aid, "manual")
    return _statusbar(request, msg)


@router.post("/api/accounts/{aid}/interactive-login")
def interactive_login(aid: int, request: Request):
    """设置页/状态栏的「打开浏览器登录」按钮：启动有头人工登录（立即返回，前端轮询状态）。"""
    c = container_of(request)
    with c.db.session() as s:
        account = s.get(Account, aid)
        cls = REGISTRY.get(account.adapter_key) if account else None
    if account is None or cls is None:
        return _statusbar(request, "账号不存在或未知平台")
    probe = cls(c.adapter_ctx(aid))
    if not getattr(probe, "login_entry_url", ""):
        probe.close()
        return _statusbar(request, "该平台未配置登录入口（自定义平台需填登录页 URL）")
    probe.close()
    if not c.interactive:
        return _statusbar(request, "有头登录组件未就绪（服务启动方式异常）")
    c.interactive.start(aid)
    return _statusbar(request, f"已打开浏览器：请完成「{cls.name}」登录（3 分钟内），成功后自动同步")


@router.get("/api/accounts/{aid}/login-status")
def login_status(aid: int, request: Request):
    c = container_of(request)
    state = c.interactive.status(aid) if c.interactive else LoginState("idle")
    return {"state": state.state, "message": state.message}


# ---------- 课程选择（设置页） ----------

@router.post("/api/courses/{cid}/toggle")
def course_toggle(cid: int, request: Request, enabled: str | None = Form(None)):
    c = container_of(request)
    on = enabled not in (None, "", "false")
    with c.db.session() as s:
        co = s.get(Course, cid)
        if co is None:
            return PlainTextResponse("not found", status_code=404)
        co.enabled = on
        name = co.name
        s.commit()
    c.logger.info("course toggle: %s -> %s", name, "开" if on else "关")
    state = "开启" if on else "关闭"
    return c.templates.TemplateResponse(
        request, "_saved_hint.html", {"name": name, "state": state}
    )


# ---------- 提醒档位（单作业覆盖，设计 §8.1） ----------

_OFFSET_PRESETS = {
    "default": None,                      # 跟随全局默认（priority=1 自动加 T-12h）
    "off": "[]",                          # 不提醒
    "light": "[1440,60]",                 # 简约：T-1d / T-1h
    "urgent": "[720,180,60]",             # 冲刺：T-12h / T-3h / T-1h
    "full": "[10080,4320,1440,360,60]",   # 完整：与全局默认相同
}
_OFFSET_PRESET_LABEL = {
    "default": "默认档位", "off": "不提醒", "light": "简约 T-1d/T-1h",
    "urgent": "冲刺 T-12h/3h/1h", "full": "完整 T-7d…T-1h",
}


@router.post("/api/assignments/{aid}/offsets")
def assignment_offsets(aid: int, request: Request, preset: str = Form(...)):
    c = container_of(request)
    if preset not in _OFFSET_PRESETS:
        return PlainTextResponse("bad preset", status_code=400)
    with c.db.session() as s:
        a = s.get(Assignment, aid)
        if a is None:
            return PlainTextResponse("not found", status_code=404)
        a.reminder_offsets_json = _OFFSET_PRESETS[preset]
        course = s.get(Course, a.course_id)
        account = s.get(Account, a.account_id)
        s.commit()
    c.logger.info("assignment %s reminder preset -> %s", aid, preset)
    row: Row = _build_row(c, a, course, account)
    row.offset_preset = preset  # type: ignore[attr-defined]
    return c.templates.TemplateResponse(request, "_card.html", {"row": row})


# ---------- 通知渠道开关（设置页） ----------

@router.post("/api/channels/{key}/toggle")
def channel_toggle(key: str, request: Request, enabled: str | None = Form(None)):
    from ..notify import set_channel_enabled

    c = container_of(request)
    if key not in ("desktop", "email"):
        return PlainTextResponse("bad channel", status_code=400)
    on = enabled not in (None, "", "false")
    set_channel_enabled(c.db, key, on)
    c.logger.info("channel %s -> %s", key, "开" if on else "关")
    state = "开启" if on else "关闭"
    return c.templates.TemplateResponse(request, "_saved_hint.html",
                                        {"name": f"通知渠道 {key}", "state": state})


# ---------- 通知 ----------

@router.post("/api/notify/test")
def notify_test(request: Request):
    c = container_of(request)
    channels = c.active_channels()
    if not channels:
        return _statusbar(request, "没有可用的通知渠道（桌面通知不可用且邮件未配置）")
    parts = []
    now = now_utc()
    with c.db.session() as s:
        for name, notifier in channels.items():
            res = notifier.send_test()
            s.add(NotificationLog(
                channel=name, kind="test", dedupe_key=f"test:{uuidlib.uuid4()}",
                status="sent" if res.ok else "failed", attempts=1,
                summary="DDLHub 测试通知", error=res.error,
                sent_at=now if res.ok else None, created_at=now,
            ))
            parts.append(f"{name}: {'OK' if res.ok else '失败(' + res.error + ')'}")
        s.commit()
    return _statusbar(request, "测试通知 → " + "；".join(parts))
