"""MockAdapter：内置种子数据，离线可跑（P0 验收：全新环境 5 分钟看到 Mock 数据）。

- 锚点固定在首次同步日的 08:00，重复同步 due 不漂移 → 幂等可验证。
- 演示场景开关（UI 按钮 → app_setting，下次同步生效）：
    mutate_due     某作业 DDL +3 天        → 演示变更检测 + change 通知
    add_new        临时新增一份作业        → 演示新作业通知
    expire_session 会话失效一次            → 演示 need_relogin + 一键恢复
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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

_TZ = ZoneInfo("Asia/Shanghai")

_COURSES = [
    ("CO-01", "高等数学（下）", "2026春"),
    ("CO-02", "数据结构", "2026春"),
    ("CO-03", "大学英语（四）", "2025-2026-2"),
    ("CO-04", "软件工程导论", "2026春"),
]

# (course_source, local_id, title, delta, est_minutes, priority, status)
_SEED_ASSIGNMENTS = [
    ("CO-01", "MATH-QUIZ", "限时测验：傅里叶级数", timedelta(days=1, hours=2), 45, 1, "open"),
    ("CO-01", "MATH-HW01", "第九次作业：级数", timedelta(days=2, hours=4, minutes=30), 90, 0, "open"),
    ("CO-01", "MATH-HW02", "第十次作业：微分方程", timedelta(days=9), 120, 0, "open"),
    ("CO-01", "MATH-LAB", "随堂实验：矩阵求逆", None, 30, 0, "open"),
    ("CO-02", "DS-HW01", "图论专题作业", timedelta(days=5, hours=7), 150, 0, "open"),
    ("CO-02", "DS-HW02", "红黑树分析报告", timedelta(days=12), 180, 0, "open"),
    ("CO-02", "DS-EXAM", "期中机试（限时）", timedelta(days=20), 120, 1, "open"),
    ("CO-03", "EN-ESSAY", "Essay: Campus Life", timedelta(days=3, hours=5), 60, 0, "open"),
    ("CO-03", "EN-ORAL", "口语录音作业", timedelta(days=-1), 40, 0, "open"),
    ("CO-03", "EN-UNIT", "Unit 5 网络课程", timedelta(days=6), 60, 0, "submitted"),
    ("CO-04", "SE-PROJ", "项目原型提交", timedelta(days=14), 300, 0, "open"),
    ("CO-04", "SE-READ", "阅读报告：人月神话", None, 45, 0, "open"),
]


@register("mock")
class MockAdapter(BaseAdapter):
    name = "演示平台 (Mock)"
    capabilities = Capability.INTERACTIVE
    supports_2fa = True
    scenario_buttons = True

    # ---- 内部状态（ctx.kv，命名空间 per account）----

    def _anchor(self) -> datetime:
        """首次同步日 08:00（UTC naive）；固定锚点保证重复同步幂等。"""
        raw = self.ctx.kv.get("mock.anchor")
        if raw:
            return datetime.fromisoformat(raw)
        anchor = datetime.now(timezone.utc).astimezone(_TZ).replace(
            hour=8, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc).replace(tzinfo=None)
        self.ctx.kv.set("mock.anchor", anchor.isoformat())
        return anchor

    def _scenarios(self) -> dict:
        return self.ctx.kv.get("mock.scenario") or {}

    def _set_scenarios(self, sc: dict) -> None:
        self.ctx.kv.set("mock.scenario", sc)

    # ---- 契约实现 ----

    def login(self, cred: Credentials) -> LoginResult:
        time.sleep(0.1)
        return LoginResult(ok=True, message="演示平台无需真实登录")

    def health_check(self) -> Health:
        sc = self._scenarios()
        if sc.get("expire_session"):
            sc["expire_session"] = False
            self._set_scenarios(sc)
            return Health(ok=False, need_relogin=True, detail="演示：会话已失效")
        return Health(ok=True)

    def relogin(self) -> LoginResult:
        return LoginResult(ok=True, message="演示会话已恢复（无需人工操作）")

    def fetch_courses(self) -> list[CourseDraft]:
        time.sleep(0.05)
        return [CourseDraft(source_id=sid, name=name, term=term) for sid, name, term in _COURSES]

    def fetch_assignments(self, course: CourseDraft) -> list[AssignmentDraft]:
        time.sleep(0.05)
        anchor = self._anchor()
        sc = self._scenarios()
        published = anchor - timedelta(days=2)

        drafts: list[AssignmentDraft] = []
        for csrc, lid, title, delta, est, prio, status in _SEED_ASSIGNMENTS:
            if csrc != course.source_id:
                continue
            due: Optional[datetime] = None
            if delta is not None:
                # 以本地时区构造 aware 时间，演练 upsert 的归一化路径
                local = (anchor + delta).replace(tzinfo=timezone.utc).astimezone(_TZ)
                due = local
            if sc.get("mutate_due") and lid == "DS-HW02" and due is not None:
                due = due + timedelta(days=3)
            drafts.append(
                AssignmentDraft(
                    source_id=lid,
                    course_source_id=csrc,
                    title=title,
                    due_at=due,
                    published_at=published.replace(tzinfo=timezone.utc).astimezone(_TZ),
                    status=status,
                    source_url=f"https://mock.example.org/course/{csrc}/assignment/{lid}",
                    estimated_minutes=est,
                    priority=prio,
                )
            )

        if sc.get("add_new") and course.source_id == "CO-02":
            drafts.append(
                AssignmentDraft(
                    source_id="DS-SURPRISE",
                    course_source_id=csrc,
                    title="随堂测验（临时新增）",
                    due_at=(anchor + timedelta(days=1, hours=6)).replace(
                        tzinfo=timezone.utc
                    ).astimezone(_TZ),
                    published_at=datetime.now(_TZ),
                    status="open",
                    source_url="https://mock.example.org/course/CO-02/assignment/DS-SURPRISE",
                    estimated_minutes=30,
                    priority=1,
                )
            )
        return drafts
