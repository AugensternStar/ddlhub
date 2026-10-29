"""S-P1-1 复现测试：smtplib 缺省上下文不校验证书 + STARTTLS 失败静默降级。

修前两条用例应红色（缺陷复现），修复后全绿。
不联网：SMTP 双打桩，只验证"是否传入校验上下文"与"是否明文降级"。
"""
import logging
import smtplib
import ssl

import pytest

from ddlhub.notify.email_smtp import EmailNotifier


@pytest.fixture
def notifier():
    return EmailNotifier("smtp.example.edu.cn", 465, "user@x", "authcode-123")


def test_ssl_port_uses_verifying_context(notifier, monkeypatch):
    """465 端口必须显式传入 CERT_REQUIRED 上下文（缺省 _create_stdlib_context 不校验）。"""
    captured = {}

    class FakeSMTPSSL:
        def __init__(self, host, port, context=None, timeout=None):
            captured["context"] = context

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            pass

        def send_message(self, msg):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    result = notifier.send("t", "b")

    assert result.ok, result.error
    ctx = captured["context"]
    assert ctx is not None, "未传 context：smtplib 将用 _create_stdlib_context（不校验证书）"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_starttls_failure_must_not_fall_back_to_plaintext(notifier, monkeypatch):
    """587 端口 STARTTLS 失败必须整体失败，绝不允许明文继续发送授权码。"""
    calls = {"login": 0, "starttls_context": None}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def starttls(self, context=None):
            calls["starttls_context"] = context
            raise smtplib.SMTPException("STARTTLS 协商失败（模拟中间人剥离）")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            calls["login"] += 1  # 明文登录被调用即留下证据

        def send_message(self, msg):
            calls["sent_plaintext"] = True

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notifier, "port", 587)

    result = notifier.send("t", "b")

    assert result.ok is False, "STARTTLS 失败后不得继续明文发送"
    assert "STARTTLS" in result.error or "TLS" in result.error
    assert calls["login"] == 0, "授权码在明文通道上被发送了"
    assert calls["starttls_context"] is not None and \
        calls["starttls_context"].verify_mode == ssl.CERT_REQUIRED


def test_starttls_success_uses_verifying_context(notifier, monkeypatch):
    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def starttls(self, context=None):
            FakeSMTP.tls_ctx = context

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            pass

        def send_message(self, msg):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notifier, "port", 587)

    assert notifier.send("t", "b").ok
    assert FakeSMTP.tls_ctx is not None
    assert FakeSMTP.tls_ctx.verify_mode == ssl.CERT_REQUIRED
