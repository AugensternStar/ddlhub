"""同步互斥（P0-7）与自动触发限频（红线）。"""
import time
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import select

from tests.mock_adapter import MockAdapter
from ddlhub.core.models import SyncRun
from ddlhub.core.sync import sync_account


def _runs(db, account_id):
    with db.session() as s:
        return s.exec(select(SyncRun).where(SyncRun.account_id == account_id)).all()


def test_concurrent_sync_serialized_by_lock(container, mock_account, monkeypatch):
    orig = MockAdapter.fetch_courses

    def slow(self):
        time.sleep(0.4)
        return orig(self)

    monkeypatch.setattr(MockAdapter, "fetch_courses", slow)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(sync_account, container, mock_account, "manual")
        time.sleep(0.1)  # 确保 f1 先拿锁
        f2 = ex.submit(sync_account, container, mock_account, "manual")
        r1, r2 = f1.result(), f2.result()

    outcomes = [r1, r2]
    assert sum(1 for o in outcomes if o.ok) == 1
    blocked = next(o for o in outcomes if not o.ok)
    assert "正在同步中" in blocked.message
    # 被锁挡下的那次不产生 sync_run 记录
    assert len(_runs(container.db, mock_account)) == 1


def test_auto_trigger_throttled_manual_exempt(container, mock_account):
    ok = sync_account(container, mock_account, "manual")
    assert ok.ok

    throttled = sync_account(container, mock_account, "cron")
    assert throttled.ok and "不足" in throttled.message
    runs = _runs(container.db, mock_account)
    assert any(r.status == "skipped" for r in runs)

    manual_again = sync_account(container, mock_account, "manual")
    assert manual_again.ok and "同步完成" in manual_again.message  # 手动豁免限频


def test_failure_isolation_between_accounts(container, db, mock_account, monkeypatch):
    import uuid as uuidlib
    from ddlhub.core.models import Account
    from ddlhub.core.normalize import now_utc
    from ddlhub.core.sync import sync_all

    with db.session() as s:
        s.add(Account(uuid=str(uuidlib.uuid4()), adapter_key="no-such-adapter",
                      label="坏适配器", status="ok", created_at=now_utc()))
        s.commit()

    outcomes = sync_all(container, "manual")
    assert len(outcomes) == 2
    by_ok = {o.ok for o in outcomes}
    assert True in by_ok and False in by_ok  # 坏账号失败，Mock 账号不受影响
