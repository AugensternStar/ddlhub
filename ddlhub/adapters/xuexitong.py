"""学习通（超星）适配器 —— 阶段 3 第一个真实平台。

登录策略（与用户提供的参考工具一致：有头浏览器人工登录，不程序化提交账密）：
- 账密登录常伴滑块验证码/扫码 → 红线不碰验证码，login() 一律引导到有头浏览器，
  用户人工完成一次登录（账密+可能的验证码），core 轮询 login_success 判定后
  storage_state() 加密落库，长期复用（vc3 等会话 cookie）。
- 判定：context.cookies() 中出现 vc3（超星登录票据）即成功。

抓取策略（能力声明 EMBEDDED_JSON 优先，Playwright 兜底留待线上校准后接入）：
- 课程列表：POST mooc1-api/mycourse/backclazzdata（内嵌 JSON）
- 作业列表：GET  mooc2-ans/work/listAll?classId&courseId（服务端渲染 HTML，BeautifulSoup 解析）

⚠ 解析基于 2026-10 抓取的页面结构认知 + 参考工具数据文件；平台改版表现为
  sync_run=failed 或解析为空（fixture 回归测试防回归，见 tests/fixtures/xuexitong/）。
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta
from typing import ClassVar, Optional
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .base import (
    AssignmentDraft,
    BaseAdapter,
    Capability,
    CourseDraft,
    Credentials,
    Health,
    LoginResult,
)
from .registry import register

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")

_COURSE_URL = "https://mooc2-ans.chaoxing.com/mycourse/stu?courseId={course_id}&classId={class_id}"


def parse_backclazzdata(data: dict) -> list[CourseDraft]:
    """解析 backclazzdata JSON → 课程草稿（纯函数，fixture 回归）。

    结构：channelList[].content.id=classId；.course.data[0].{id,name}=courseId/课程名。
    没有课程数据（已解散/非课程频道）的条目跳过。
    """
    drafts: list[CourseDraft] = []
    for channel in data.get("channelList") or []:
        content = channel.get("content") or {}
        course_data = (content.get("course") or {}).get("data") or []
        if not course_data:
            continue
        c0 = course_data[0]
        class_id = str(content.get("id") or "").strip()
        course_id = str(c0.get("id") or "").strip()
        name = (c0.get("name") or "").strip()
        if not class_id or not course_id or not name:
            continue
        drafts.append(CourseDraft(
            source_id=class_id,
            name=name,
            url=_COURSE_URL.format(course_id=course_id, class_id=class_id),
        ))
    return drafts


def parse_work_list_html(
    html: str, course_source_id: str, course_url: str, now: datetime | None = None
) -> list[AssignmentDraft]:
    """解析作业列表页 → 作业草稿（纯函数，fixture 回归；样本=真实页面 work_list_live.html）。

    真实结构（2026-10）：`.bottomList ul li`，每行：
      - li[data]=任务页 URL（含 workId）
      - p.overHidden2=标题；p.status=状态（未交/已交/已批阅）
      - div.time=相对倒计时（"剩余95小时35分钟"/"已截止"）——无绝对日期
    due_at 由 now+倒计时 还原绝对时间（同一截止多次捕获结果稳定，分钟级误差由
    upsert 的 30 分钟容差吸收）。
    """
    if "请登录" in html:
        raise RuntimeError("作业页面要求登录（会话可能已失效）")

    soup = BeautifulSoup(html, "html.parser")
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    drafts: list[AssignmentDraft] = []

    for li in soup.select(".bottomList ul li"):
        data_url = li.get("data") or ""
        params = dict(parse_qsl(urlsplit(data_url).query))
        work_id = params.get("workId") or ""
        if not work_id:
            continue

        title_el = li.select_one(".right-content p.overHidden2")
        title = (title_el.get_text(strip=True) if title_el else "") or "未命名作业"
        status_el = li.select_one(".right-content p.status")
        status_text = status_el.get_text(strip=True) if status_el else ""
        time_el = li.select_one("div.time")
        time_text = time_el.get_text(strip=True) if time_el else ""

        due = _due_from_time_text(time_text, now)
        submitted = any(k in status_text for k in ("已交", "已批阅", "待批阅", "已完成"))
        is_timed = any(k in title for k in ("限时", "测验", "考试"))

        drafts.append(AssignmentDraft(
            source_id=work_id,
            course_source_id=course_source_id,
            title=title[:200],
            due_at=due,
            published_at=None,
            status="submitted" if submitted else "open",
            source_url=data_url or course_url,
            estimated_minutes=45 if is_timed else 60,
            priority=1 if is_timed else 0,
        ))
    return drafts


_REL_RE = re.compile(
    r"剩余((?P<d>\d+)天)?((?P<h>\d+)小时)?((?P<m>\d+)分钟?)?"
)


def _due_from_time_text(text: str, now: datetime) -> datetime | None:
    """'剩余95小时35分钟' → now+95h35m；'已截止/已结束'等无绝对时间 → None。"""
    text = (text or "").strip()
    m = _REL_RE.search(text)
    if not m:
        return None
    delta = timedelta(
        days=int(m.group("d") or 0),
        hours=int(m.group("h") or 0),
        minutes=int(m.group("m") or 0),
    )
    return now + delta


@register("xuexitong")
class XuexitongAdapter(BaseAdapter):
    name = "学习通（超星）"
    capabilities = Capability.EMBEDDED_JSON | Capability.PLAYWRIGHT | Capability.INTERACTIVE
    supports_2fa = True
    login_mode = "interactive"
    login_entry_url = "https://passport2.chaoxing.com/login"

    _API_COURSES = "https://mooc1-api.chaoxing.com/mycourse/backclazzdata"
    _WORK_LIST = "https://mooc2-ans.chaoxing.com/work/listAll"
    _HOME = "https://i.chaoxing.com/"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._client: Optional[httpx.Client] = None
        self._pw = None            # sync_playwright 运行时
        self._browser = None
        self._pw_ctx = None        # 带 storage_state 的浏览器上下文
        self._home = None          # 常驻空间页（课程卡所在）

    # ---- 登录与会话 ----

    def login(self, cred: Credentials) -> LoginResult:
        # 红线：不程序化提交账密（会触发滑块/风控且等同于绕过验证码），
        # 统一走有头浏览器人工登录一次（与参考工具同思路）。
        return LoginResult(
            ok=False, needs_interactive=True,
            message="学习通需在弹出的浏览器中完成一次账密登录（含可能的验证码/扫码），会话将加密保存并长期复用",
        )

    def login_success(self, context, page) -> bool:
        """core 轮询调用：出现登录票据 cookie vc3 即视为登录成功。"""
        for cookie in context.cookies():
            if cookie.get("name") == "vc3" and "chaoxing.com" in (cookie.get("domain") or ""):
                return True
        return False

    def health_check(self) -> Health:
        state = self.ctx.sessions.load()
        if not state or not state.get("cookies"):
            return Health(ok=False, need_relogin=True, detail="无已保存会话，请先完成一次浏览器登录")
        client = self._http()
        try:
            r = client.get(self._HOME)
            if "passport2.chaoxing.com" in str(r.url):
                return Health(ok=False, need_relogin=True, detail="会话已失效（跳转回了登录页）")
            return Health(ok=True)
        except Exception as exc:
            return Health(ok=False, need_relogin=False, detail=f"网络异常：{exc}")
        finally:
            self.close()

    # ---- 抓取 ----

    def fetch_courses(self) -> list[CourseDraft]:
        client = self._http()
        # 实测（2026-10）：GET + view=json 返回 result=1；POST 会 405
        r = client.get(self._API_COURSES, params={"view": "json"})
        r.raise_for_status()
        data = r.json()
        if data.get("result") != 1:
            # result=0 常见于会话失效/参数校验失败
            raise RuntimeError(f"课程列表接口返回异常 result={data.get('result')}（若反复出现请重新登录）")
        drafts = parse_backclazzdata(data)
        self.ctx.log.info("xuexitong: fetched %d courses", len(drafts))
        return drafts

    def fetch_assignments(self, course: CourseDraft) -> list[AssignmentDraft]:
        """作业列表必须走浏览器点击导航（课程页 URL 含前端生成的会话令牌，
        httpx 直连会被"无效的参数"拒绝——与参考刷课工具同款策略）。

        路径：空间页 → courselist iframe 里按课程名点卡片（新标签）→ 点「作业」tab
        → 取「作业列表」frame 的服务端渲染 HTML → 纯函数解析。每账号复用一个浏览器。
        """
        self._ensure_browser()
        name = course.name
        card = self._find_course_card(name)
        if card is None:
            raise RuntimeError(f"课程「{name}」未出现在空间页（可能已结束或被隐藏）")

        with self._pw_ctx.expect_page(timeout=15000) as pop:
            card.click()
        cp = pop.value
        try:
            cp.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        cp.wait_for_timeout(2000)

        clicked = False
        for f in [cp.main_frame] + cp.frames:
            try:
                f.get_by_text("作业", exact=True).first.click(timeout=2500)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            cp.close()
            raise RuntimeError(f"课程「{name}」页面未找到作业入口（页面可能改版）")
        cp.wait_for_timeout(3000)

        html = None
        for f in cp.frames:
            try:
                c = f.content()
            except Exception:
                continue
            if "作业列表" in c or "暂无作业" in c:
                html = c
                break
        if html is None:
            cp.close()
            raise RuntimeError(f"课程「{name}」作业列表未渲染（页面可能改版）")
        cp.close()

        drafts = parse_work_list_html(
            html, course.source_id, course.url,
            now=datetime.now(ZoneInfo(self._TZ)),
        )
        self.ctx.log.info("xuexitong: course %s -> %d assignments", name, len(drafts))
        return drafts

    # ---- 浏览器会话（每账号一次启动，close() 释放）----

    _TZ = "Asia/Shanghai"

    def _ensure_browser(self) -> None:
        if self._pw_ctx is not None:
            return
        state = self.ctx.sessions.load()
        if not state:
            raise RuntimeError("无已保存会话，请先完成一次浏览器登录")
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._pw_ctx = self._browser.new_context(
            storage_state=state, user_agent=_UA,
            viewport={"width": 1366, "height": 900},
        )
        self._home = self._pw_ctx.new_page()
        self._home.goto("https://i.chaoxing.com/base", timeout=30000)
        self._home.wait_for_load_state("load")
        self._home.wait_for_timeout(3500)  # 等课程列表 iframe 就绪

    def _find_course_card(self, name: str):
        for f in self._home.frames:
            if "courselist" not in (f.url or ""):
                continue
            try:
                loc = f.locator(f"text={name}").first
                loc.wait_for(state="visible", timeout=5000)
                return loc
            except Exception:
                continue
        return None

    # ---- 内部 ----

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        state = self.ctx.sessions.load() or {}
        cookies = httpx.Cookies()
        for c in state.get("cookies") or []:
            if "chaoxing.com" not in (c.get("domain") or ""):
                continue
            cookies.set(
                c["name"], c.get("value", ""),
                domain=c.get("domain") or ".chaoxing.com",
                path=c.get("path") or "/",
            )
        self._client = httpx.Client(
            cookies=cookies,
            headers={"User-Agent": _UA, "Referer": "https://i.chaoxing.com/"},
            follow_redirects=True,
            timeout=20,
        )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        # 浏览器资源：内存态 context 随之消亡，磁盘无任何明文
        try:
            if self._pw_ctx is not None:
                self._pw_ctx.close()
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw_ctx = self._browser = self._home = self._pw = None
