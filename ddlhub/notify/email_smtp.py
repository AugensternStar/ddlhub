"""邮件渠道：SMTP(S) 文本邮件（强制 TLS 证书校验，失败即报错，绝不明文降级）。

授权码优先存钥匙串（ddlhub/notify/smtp）。
S-P1-1（审计报告 §3.1）：smtplib 在 context=None 时使用 _create_stdlib_context，
verify_mode=CERT_NONE 且不校验主机名——必须显式传入 create_default_context()。
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import ClassVar, Optional

from ..core.redact import redact
from ..core.security import SERVICE
from .base import NotifierBase, SendResult

SMTP_CRED_ENTRY = f"{SERVICE}/notify/smtp"


class EmailNotifier(NotifierBase):
    key: ClassVar[str] = "email"
    name: ClassVar[str] = "邮件"

    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    def available(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send(self, title: str, body: str, url: Optional[str] = None) -> SendResult:
        # S-P1-1：显式校验上下文；STARTTLS 失败整体失败，绝不降级明文发送授权码
        ctx = ssl.create_default_context()
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[DDLHub] {title}"
            msg["From"] = self.user
            msg["To"] = self.user
            content = f"{body}\n\n{url}" if url else body
            msg.set_content(content)

            if self.port == 465:
                client = smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=15)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=15)
                client.starttls(context=ctx)  # 失败 → 异常向上，走统一失败记录
            with client:
                client.login(self.user, self.password)
                client.send_message(msg)
            return SendResult(ok=True)
        except Exception as exc:
            return SendResult(ok=False, error=redact(f"{type(exc).__name__}: {exc}"))
