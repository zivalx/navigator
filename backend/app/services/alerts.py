"""Alert business logic: rule-shape validation, trailing-stop seeding,
create/update orchestration.

The single source of truth for what a well-formed alert looks like — the
create schema validator and the update endpoint both call validate_rule_fields
so the two paths cannot drift.
"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from ..models.alert import AlertIntent, AlertRule, PriceAlert
from ..models.asset import Asset
from .market_data import MarketDataService


class AlertValidationError(ValueError):
    """A rule/field combination that must be rejected (HTTP 400/422)."""


class AlertSeedError(Exception):
    """Current price unavailable — a trailing stop cannot start tracking."""


def validate_rule_fields(
    rule: AlertRule,
    threshold: Optional[float],
    trail_percent: Optional[float],
    trail_amount: Optional[float],
) -> None:
    """Validate the field shape for a rule. Raises AlertValidationError."""
    if rule == AlertRule.TRAILING_STOP:
        if (trail_percent is None) == (trail_amount is None):
            raise AlertValidationError(
                "Trailing stop requires exactly one of trailPercent or trailAmount"
            )
        trail = trail_percent if trail_percent is not None else trail_amount
        if trail <= 0:
            raise AlertValidationError("Trail must be positive")
        if trail_percent is not None and trail_percent >= 100:
            raise AlertValidationError("trailPercent must be below 100")
        if threshold is not None:
            raise AlertValidationError("Trailing stop alerts do not take a threshold")
    else:
        if threshold is None:
            raise AlertValidationError("Threshold is required for price rules")
        if threshold <= 0:
            raise AlertValidationError("Threshold must be positive")
        if trail_percent is not None or trail_amount is not None:
            raise AlertValidationError("Trail fields only apply to trailing stop alerts")


async def fetch_seed_price(db: Session, asset: Asset) -> float:
    """Current quote price, needed to seed a trailing stop's high-water mark.

    Raises AlertSeedError when no price is available. NOTE: the underlying
    quote fetch may commit the session (price-snapshot caching), so callers
    must invoke this BEFORE mutating any ORM state.
    """
    try:
        market_service = MarketDataService(db)
        quote = await market_service.get_quote(asset.symbol, asset.asset_type.value)
        price = quote.get("price")
    except Exception:
        price = None
    if price is None:
        raise AlertSeedError(
            f"Could not fetch a current price for {asset.symbol} to start the trailing stop"
        )
    return price


async def create_alert(
    db: Session,
    asset: Asset,
    *,
    rule: AlertRule,
    threshold: Optional[float],
    intent: Optional[AlertIntent],
    trail_percent: Optional[float],
    trail_amount: Optional[float],
    note: Optional[str],
) -> PriceAlert:
    """Create an alert. Fields are assumed validated (create schema)."""
    is_tsl = rule == AlertRule.TRAILING_STOP
    high_water_mark = await fetch_seed_price(db, asset) if is_tsl else None
    if intent is None and is_tsl:
        # The one canonical place trailing stops default to SELL.
        intent = AlertIntent.SELL

    alert = PriceAlert(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        rule=rule,
        threshold=threshold,
        intent=intent,
        trail_percent=trail_percent,
        trail_amount=trail_amount,
        high_water_mark=high_water_mark,
        note=note,
        is_active=True,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


async def apply_alert_update(
    db: Session,
    alert: PriceAlert,
    update_data: dict,
) -> PriceAlert:
    """Apply a partial update, keeping the alert in a valid state.

    - Merged state is validated with the same rules as create.
    - Switching rule families clears the fields that no longer apply (a stale
      threshold on a new trailing stop, stale trail/high-water fields on a new
      price rule); explicitly contradictory fields in the request are rejected.
    - The quote fetch for (re)seeding a trailing stop happens BEFORE any ORM
      mutation, so its internal commit cannot persist half-applied state.
    - Reactivation (isActive=true) clears triggered/acknowledged/notification
      state and restarts a trailing stop's high-water mark.
    """
    merged = {
        "rule": alert.rule,
        "threshold": alert.threshold,
        "trail_percent": alert.trail_percent,
        "trail_amount": alert.trail_amount,
        **{k: v for k, v in update_data.items()
           if k in ("rule", "threshold", "trail_percent", "trail_amount")},
    }
    new_rule = merged["rule"]
    rule_switched = new_rule != alert.rule
    is_tsl = new_rule == AlertRule.TRAILING_STOP

    if rule_switched:
        # Drop carried-over fields from the old rule family unless the request
        # itself set them (in which case validation judges them below).
        if is_tsl and "threshold" not in update_data:
            merged["threshold"] = None
        if not is_tsl:
            for field in ("trail_percent", "trail_amount"):
                if field not in update_data:
                    merged[field] = None

    validate_rule_fields(
        new_rule, merged["threshold"], merged["trail_percent"], merged["trail_amount"]
    )

    reactivating = update_data.get("is_active") is True

    # Any awaited work that can touch the session happens before mutations.
    new_high_water_mark = None
    needs_seed = is_tsl and (alert.high_water_mark is None or reactivating or rule_switched)
    if needs_seed:
        new_high_water_mark = await fetch_seed_price(db, alert.asset)

    for field, value in update_data.items():
        setattr(alert, field, value)
    alert.threshold = merged["threshold"]
    alert.trail_percent = merged["trail_percent"]
    alert.trail_amount = merged["trail_amount"]

    if is_tsl:
        if needs_seed:
            alert.high_water_mark = new_high_water_mark
        if alert.intent is None:
            alert.intent = AlertIntent.SELL
    else:
        alert.high_water_mark = None

    if reactivating:
        alert.triggered_at = None
        alert.triggered_price = None
        alert.acknowledged_at = None
        alert.notified_at = None
        alert.notification_skipped_at = None

    db.commit()
    db.refresh(alert)
    return alert
