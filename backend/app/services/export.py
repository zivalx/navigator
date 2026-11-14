"""Read-only positions export for trender.

Plain DB reads only: no provider, FX or portfolio-service calls. Cost is never
converted between currencies; if lots disagree with the asset currency the
average is reported as unknown.
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.asset import Asset
from ..models.holding import HoldingLot
from ..models.watchlist import Watchlist, WatchlistItem
from ..schemas.export import ExportedAsset, PositionsExport


def _value(v):
    return v.value if hasattr(v, "value") else v


def build_positions_export(db: Session) -> PositionsExport:
    lots_by_asset = defaultdict(list)
    for lot in db.query(HoldingLot).all():
        lots_by_asset[lot.asset_id].append(lot)

    watchlists_by_asset = defaultdict(set)
    rows = (
        db.query(WatchlistItem.asset_id, Watchlist.name)
        .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .all()
    )
    for asset_id, name in rows:
        watchlists_by_asset[asset_id].add(name)

    included = set(lots_by_asset) | set(watchlists_by_asset)
    assets = []
    if included:
        for asset in db.query(Asset).filter(Asset.id.in_(included)).all():
            assets.append(_export_asset(
                asset,
                lots_by_asset.get(asset.id, []),
                sorted(watchlists_by_asset.get(asset.id, set())),
            ))
    assets.sort(key=lambda a: a.symbol)

    return PositionsExport(
        schema_version=1,
        generated_at=datetime.now(timezone.utc),
        asset_count=len(assets),
        assets=assets,
    )


def _export_asset(asset: Asset, lots, watchlists) -> ExportedAsset:
    positive = [l for l in lots if l.quantity > 0]
    total_quantity = sum(l.quantity for l in positive)
    held = total_quantity > 0

    base = dict(
        navigator_asset_id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        exchange=asset.exchange,
        asset_type=_value(asset.asset_type),
        market_region=_value(asset.market_region),
        currency=_value(asset.currency),
        watchlists=watchlists,
    )

    if not held:
        return ExportedAsset(
            **base, held=False, total_quantity=None, avg_cost=None,
            avg_cost_currency=None, cost_basis_complete=False,
            cost_basis_issue=None, first_purchase_date=None,
        )

    first = min(l.purchase_date for l in positive).date().isoformat()
    if all(l.cost_currency == asset.currency for l in positive):
        avg_cost = sum(l.quantity * l.avg_cost for l in positive) / total_quantity
        return ExportedAsset(
            **base, held=True, total_quantity=total_quantity, avg_cost=avg_cost,
            avg_cost_currency=_value(asset.currency), cost_basis_complete=True,
            cost_basis_issue=None, first_purchase_date=first,
        )
    return ExportedAsset(
        **base, held=True, total_quantity=total_quantity, avg_cost=None,
        avg_cost_currency=None, cost_basis_complete=False,
        cost_basis_issue="mixed_cost_currency", first_purchase_date=first,
    )
