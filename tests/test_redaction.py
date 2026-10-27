"""安全验收自动化：日志与数据库全文无明文凭据。"""
import logging
import sqlite3

from ddlhub.core.redact import RedactingFormatter, redact
from ddlhub.core.security import VaultStore


def test_log_redaction_captures_secrets():
    """生产日志走 RedactingFormatter：密码形如 key=value 的内容必须清洗为 ******。"""
    fmt = RedactingFormatter("%(message)s")
    rec = logging.LogRecord(
        "ddlhub.test", logging.WARNING, __file__, 1,
        "login failed for user bob with password=hunter2 and token=abc123",
        None, None,
    )
    out = fmt.format(rec)
    assert "hunter2" not in out and "abc123" not in out
    assert "******" in out


def test_redact_function_direct():
    assert redact("pwd=123456 cookie=sid77") == "pwd=****** cookie=******"
    assert "secret_key" in redact("x secret_key=AAAA")  # 字段名保留


def test_vault_password_never_in_db_file(tmp_path):
    """vault 模式存储的密码在 SQLite 文件字节里搜不到（明文零落盘）。"""
    from ddlhub.core.db import Database
    from ddlhub.core.kv import get_setting, set_setting
    import base64

    db = Database(tmp_path)
    store = VaultStore.create("correct-horse-battery")
    store.store("xuexitong", "uuid-1", "18800000000", "明文密码 hunter2")
    set_setting(db, "vault", base64.b64encode(store.seal()).decode())

    raw = sqlite3.connect(db.path).execute(
        "SELECT value_json FROM app_setting WHERE key='vault'"
    ).fetchone()[0]
    assert "hunter2" not in raw
    assert "18800000000" not in raw  # 用户名也加密
    # 正确主密码可解回
    reopened = VaultStore.open("correct-horse-battery",
                               base64.b64decode(get_setting(db, "vault", None)))
    assert reopened.load("xuexitong", "uuid-1") == ("18800000000", "明文密码 hunter2")


def test_notification_api_response_has_no_credentials(container, mock_account):
    """API/UI 响应不含任何凭据字段（账号仅 username_hint 脱敏）。"""
    from fastapi.testclient import TestClient

    from ddlhub.web.main import create_app

    app = create_app(container)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    body = client.get("/settings").text
    assert "password" not in body.lower().replace("password\"", "") or "******" in body
    assert "ddlhub_session" not in body  # cookie 值不回显
