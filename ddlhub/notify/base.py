"""通知渠道契约：可插拔，新增渠道 = notify/ 下新文件（设计 §8.4）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional


@dataclass
class SendResult:
    ok: bool
    error: str = ""


class NotifierBase(ABC):
    key: ClassVar[str] = ""
    name: ClassVar[str] = ""

    @abstractmethod
    def send(self, title: str, body: str, url: Optional[str] = None) -> SendResult: ...

    def available(self) -> bool:
        return True

    def send_test(self) -> SendResult:
        return self.send("DDLHub 测试通知", "如果你看到这条，说明该通知渠道工作正常。")
