"""Notification de-duplication on a real Postgres (opt-in: TEST_POSTGRES_URL)."""
import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import Base
from app.models.alert import AlertRule, PriceAlert
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.tasks import scheduler as scheduler_module
from tests.pg_helpers import requires_pg, scratch_schema


@requires_pg
def test_pg_notify_skip_locked(monkeypatch):
    with scratch_schema() as scoped:
        Base.metadata.create_all(scoped)
        now = datetime.now(timezone.utc)
        alert_ids = []
        with Session(bind=scoped) as s:
            asset = Asset(
                id=str(uuid.uuid4()), symbol="AAPL", name="AAPL Inc.",
                asset_type=AssetType.STOCK, currency=Currency.USD,
                market_region=MarketRegion.US,
            )
            s.add(asset)
            s.flush()
            for i in range(3):
                a = PriceAlert(
                    id=str(uuid.uuid4()), asset_id=asset.id,
                    rule=AlertRule.PRICE_BELOW, threshold=100.0, is_active=False,
                    triggered_at=now - timedelta(minutes=3 - i), triggered_price=90.0,
                )
                s.add(a)
                alert_ids.append(a.id)
            s.commit()

        lock = threading.Lock()

        class SlowSender:
            enabled = True

            def __init__(self):
                self.sent_ids = []

            async def send(self, text):
                with lock:
                    self.sent_ids.append(text)
                await asyncio.sleep(0.2)
                return True

        sender = SlowSender()
        monkeypatch.setattr(scheduler_module, "NotificationService", lambda: sender)
        # Record which alert each send was for (the message is per-alert).
        monkeypatch.setattr(
            scheduler_module, "format_alert_message", lambda alert: alert.id
        )

        errors = []

        def run():
            try:
                with Session(bind=scoped) as s:
                    asyncio.run(scheduler_module.notify_triggered_alerts(s))
            except Exception as e:  # surface thread failures in the assert
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert sorted(sender.sent_ids) == sorted(alert_ids)
        with Session(bind=scoped) as s:
            assert all(
                s.get(PriceAlert, i).notified_at is not None for i in alert_ids
            )
