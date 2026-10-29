"""测试桩适配器：仅在测试进程注册（conftest 导入），不出现在生产下拉里。

确定性种子 + 固定锚点（首次同步日 08:00），保证重复同步幂等、due 不漂移。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ddlhub.adapters.base import (
    AssignmentDraft,
    BaseAdapter,
    Capability,
    CourseDraft,
    Credentials,
    Health,
    LoginResult,
)
from ddlhub.adapters.registry import register

_TZ = ZoneInfo("Asia/Shanghai")

_COURSES = [
    ("CO-01", "高等数学（下）", "2026春"),
    ("CO-02", "数据结构", "2026春"),
    ("CO-03", "大学英语（四）", "2025-2026-2"),
    ("CO-04", "软件工程导论", "2026春"),
]

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
    name = "测试桩平台（仅测试）"
    capabilities = Capability.INTERACTIVE
    supports_2fa = True

    def _anchor(self) -> datetime:
        raw = self.ctx.kv.get("mock.anchor")
        if raw:
            return datetime.fromisoformat(raw)
        anchor = datetime.now(timezone.utc).astimezone(_TZ).replace(
            hour=8, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc).replace(tzinfo=None)
        self.ctx.kv.set("mock.anchor", anchor.isoformat())
        return anchor

    def login(self, cred: Credentials) -> LoginResult:
        time.sleep(0.05)
        return LoginResult(ok=True, message="测试桩无需真实登录")

    def fetch_courses(self) -> list[CourseDraft]:
        time.sleep(0.02)
        return [CourseDraft(source_id=sid, name=name, term=term) for sid, name, term in _COURSES]

    def fetch_assignments(self, course: CourseDraft) -> list[AssignmentDraft]:
        time.sleep(0.02)
        anchor = self._anchor()
        published = anchor - timedelta(days=2)
        drafts: list[AssignmentDraft] = []
        for csrc, lid, title, delta, est, prio, status in _SEED_ASSIGNMENTS:
            if csrc != course.source_id:
                continue
            due: Optional[datetime] = None
            if delta is not None:
                due = (anchor + delta).replace(tzinfo=timezone.utc).astimezone(_TZ)
            drafts.append(AssignmentDraft(
                source_id=lid, course_source_id=csrc, title=title,
                due_at=due,
                published_at=published.replace(tzinfo=timezone.utc).astimezone(_TZ),
                status=status,
                source_url=f"https://mock.example.org/course/{csrc}/assignment/{lid}",
                estimated_minutes=est, priority=prio,
            ))
        return drafts
