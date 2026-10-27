"""邮件渠道：SMTP(S) 明文文本邮件。授权码优先存钥匙串（ddlhub/notify/smtp）。"""
from __future__ import annotations

import smtplib
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
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[DDLHub] {title}"
            msg["From"] = self.user
            msg["To"] = self.user
            content = f"{body}\n\n{url}" if url else body
            msg.set_content(content)

            if self.port == 465:
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=15)
                try:
                    client.starttls()
                except smtplib.SMTPException:
                    pass
            with client:
                client.login(self.user, self.password)
                client.send_message(msg)
            return SendResult(ok=True)
        except Exception as exc:
            return SendResult(ok=False, error=redact(f"{type(exc).__name__}: {exc}"))
