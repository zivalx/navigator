"""Audit asset currencies against Yahoo after the minor-unit cutover.

Run from backend/ inside the API container:
    python -m scripts.audit_asset_currencies            # dry run
    python -m scripts.audit_asset_currencies --apply    # fix assets, convert prices

Output carries symbols, currency codes, region/exchange names and counts only:
never quantities, costs, prices, thresholds or targets.
"""
import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TextIO

import httpx
from sqlalchemy.orm import Session

from app.models.alert import PriceAlert
from app.models.asset import Asset, AssetType
from app.models.holding import HoldingLot
from app.models.maintenance import (
    ALERTS_CONVERTED_MARKER,
    CUTOVER_MARKER,
    MaintenanceRun,
    get_marker,
)
from app.models.watchlist import WatchlistItem
from app.providers.yahoo import YahooFinanceProvider
from app.services.asset_identity import ListingIdentity, UnsupportedCurrency, identity_from_meta

MINOR_FACTOR = 0.01
ALERT_FIELDS = ("threshold", "trail_amount", "high_water_mark", "triggered_price")


@dataclass
class AuditReport:
    asset_fixes: list[str] = field(default_factory=list)
    lot_mismatches: list[str] = field(default_factory=list)
    converted_alerts: int = 0
    converted_watch_targets: int = 0
    minor_unit_symbols: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    aborted: bool = False  # nothing was written
    incomplete: bool = False  # --apply did not complete every step


def _utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _pre_cutover(created_at: datetime | None, cutover: datetime) -> bool:
    return created_at is None or _utc(created_at) < cutover


def _scale(value: float | None) -> float | None:
    return None if value is None else round(value * MINOR_FACTOR, 10)


def _legacy_rows(db: Session, asset: Asset, cutover: datetime):
    """Pre-cutover alerts and watch targets that hold price values."""
    alerts = [
        a for a in db.query(PriceAlert).filter(PriceAlert.asset_id == asset.id)
        if _pre_cutover(a.created_at, cutover)
        and any(getattr(a, f) is not None for f in ALERT_FIELDS)
    ]
    targets = [
        t for t in db.query(WatchlistItem).filter(WatchlistItem.asset_id == asset.id)
        if t.target_price is not None and _pre_cutover(t.created_at, cutover)
    ]
    return alerts, targets


async def audit(db: Session, *, apply: bool, out: TextIO) -> AuditReport:
    report = AuditReport()
    assets = (
        db.query(Asset)
        .filter(Asset.asset_type.in_([AssetType.STOCK, AssetType.ETF, AssetType.FUND]))
        .order_by(Asset.symbol)
        .all()
    )
    cutover_raw = get_marker(db, CUTOVER_MARKER)
    cutover = _utc(cutover_raw) if cutover_raw else None
    already_converted = get_marker(db, ALERTS_CONVERTED_MARKER) is not None
    convert = cutover is not None and not already_converted

    if cutover is None:
        print("no cutover marker found: price conversion is skipped", file=out)
    elif already_converted:
        print("alert and target conversion already done: skipping that step", file=out)

    # (asset, identity) of assets to fix; (asset) of minor-unit assets to convert.
    fixes: list[tuple[Asset, ListingIdentity]] = []
    minor_assets: list[Asset] = []
    skipped_assets: list[Asset] = []
    unreachable = False

    provider = YahooFinanceProvider()
    try:
        for asset in assets:
            try:
                meta = await provider.get_chart_meta(asset.symbol)
            except (httpx.HTTPError, ValueError) as e:
                print(f"{asset.symbol}: skipped (yahoo unreachable: {type(e).__name__})", file=out)
                report.skipped.append(asset.symbol)
                skipped_assets.append(asset)
                unreachable = True
                continue
            if meta is None:
                print(f"{asset.symbol}: skipped (symbol not found)", file=out)
                report.skipped.append(asset.symbol)
                skipped_assets.append(asset)
                continue
            try:
                ident = identity_from_meta(asset.symbol, meta)
            except UnsupportedCurrency as e:
                print(f"{asset.symbol}: skipped (unsupported currency {e.code})", file=out)
                report.skipped.append(asset.symbol)
                skipped_assets.append(asset)
                continue

            diffs = []
            if asset.currency != ident.currency:
                diffs.append(f"currency {asset.currency.value}->{ident.currency.value}")
            region_changed = ident.market_region is not None and asset.market_region != ident.market_region
            if region_changed:
                diffs.append(f"region {asset.market_region.value}->{ident.market_region.value}")
            exchange_changed = ident.exchange is not None and asset.exchange != ident.exchange
            if exchange_changed:
                diffs.append(f"exchange {asset.exchange or ''}->{ident.exchange}")
            if diffs:
                print(f"{asset.symbol}: " + " ".join(diffs), file=out)
                report.asset_fixes.append(asset.symbol)
                fixes.append((asset, ident))

            lot_counts: dict[str, int] = {}
            for (cur,) in db.query(HoldingLot.cost_currency).filter(HoldingLot.asset_id == asset.id):
                if cur != ident.currency:
                    lot_counts[cur.value] = lot_counts.get(cur.value, 0) + 1
            if lot_counts:
                report.lot_mismatches.append(asset.symbol)
            for code, n in sorted(lot_counts.items()):
                print(f"{asset.symbol}: {n} lot(s) in {code}, listing is {ident.currency.value}"
                      " - fix in the lot edit dialog", file=out)

            if meta.get("unitFactor", 1.0) != 1:
                report.minor_unit_symbols.append(asset.symbol)
                minor_assets.append(asset)
    finally:
        await provider.close()

    alert_rows: list[PriceAlert] = []
    target_rows: list[WatchlistItem] = []
    if convert:
        for asset in minor_assets:
            alerts, targets = _legacy_rows(db, asset, cutover)
            if alerts or targets:
                print(f"{asset.symbol}: {len(alerts)} alert(s), {len(targets)} watch target(s)"
                      " priced in minor units", file=out)
            alert_rows += alerts
            target_rows += targets

    # Assets whose listing is unknown cannot be converted; legacy price data on
    # them would be un-paused by the marker while still in minor units.
    blocked = False
    if convert:
        for asset in skipped_assets:
            alerts, targets = _legacy_rows(db, asset, cutover)
            if alerts or targets:
                blocked = True
                print(f"{asset.symbol}: {len(alerts)} alert(s), {len(targets)} watch target(s) "
                      "not converted (listing unknown) - delete or edit them, then re-run", file=out)

    report.incomplete = bool(report.skipped) or cutover is None

    if not apply:
        print(f"dry run: {len(report.asset_fixes)} asset(s) to fix, "
              f"{len(alert_rows)} alert(s) and {len(target_rows)} watch target(s) to convert; "
              "re-run with --apply to write", file=out)
        return report

    if unreachable or blocked:
        report.aborted = report.incomplete = True
        print("aborted: nothing was written" + (
            "; Yahoo was unreachable for some assets, re-run when it is back" if unreachable else ""),
            file=out)
        return report

    for asset, ident in fixes:
        asset.currency = ident.currency
        if ident.market_region is not None:
            asset.market_region = ident.market_region
        if ident.exchange is not None:
            asset.exchange = ident.exchange

    if convert:
        for a in alert_rows:
            for f in ALERT_FIELDS:
                setattr(a, f, _scale(getattr(a, f)))
        for t in target_rows:
            t.target_price = _scale(t.target_price)
        report.converted_alerts = len(alert_rows)
        report.converted_watch_targets = len(target_rows)
        db.add(MaintenanceRun(name=ALERTS_CONVERTED_MARKER, ran_at=datetime.now(timezone.utc)))

    db.commit()
    print(f"applied: {len(fixes)} asset(s) fixed, {report.converted_alerts} alert(s) and "
          f"{report.converted_watch_targets} watch target(s) converted", file=out)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the fixes (default: dry run)")
    args = parser.parse_args(argv)

    import logging

    from app.database import SessionLocal, engine

    # Never let SQL echo (dev mode) print row values.
    engine.echo = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    db = SessionLocal()
    try:
        report = asyncio.run(audit(db, apply=args.apply, out=sys.stdout))
    finally:
        db.close()
    return 1 if (report.aborted or (args.apply and report.incomplete)) else 0


if __name__ == "__main__":
    sys.exit(main())
