"""Frozen pre-Alembic converge, used only by app.migrate to adopt a legacy DB.

A database built by the old init_db() (create_all + additive DDL at boot) has
tables but no alembic_version. converge() brings it to the 0001 baseline the
way init_db() used to; verify() checks the result before it is stamped.

Valid only while the baseline tables' models are unchanged. Production has no
legacy database; delete this module and the adopt branch in app.migrate one
release after the local and dev databases are past 0002.
"""
from sqlalchemy import Engine, text

from . import models  # noqa: F401  (registers every table on Base.metadata)
from .database import Base, ensure_cutover_marker

BASELINE_TABLES: tuple[str, ...] = (
    "assets",
    "cash_balances",
    "fx_rates",
    "holding_lots",
    "maintenance_runs",
    "news_items",
    "portfolio_snapshots",
    "price_alerts",
    "price_snapshots",
    "watchlist_items",
    "watchlists",
)

_NEW_CURRENCY_VALUES = (
    "ILS", "HKD", "CNY", "KRW", "TWD", "SGD", "INR",
    "SEK", "DKK", "NOK", "AUD", "CAD", "ZAR",
)

# Label sets, not lists: ADD VALUE appends, so adopted DBs order them differently.
EXPECTED_ENUM_LABELS: dict[str, frozenset[str]] = {
    "currency": frozenset({"USD", "EUR", "GBP", "JPY", "CHF", *_NEW_CURRENCY_VALUES}),
    "assettype": frozenset({"STOCK", "ETF", "CRYPTO", "FUND", "INDEX", "OTHER"}),
    "marketregion": frozenset({"US", "EU", "ASIA", "IL"}),
    "alertrule": frozenset({"PRICE_BELOW", "PRICE_ABOVE", "TRAILING_STOP"}),
    "alertintent": frozenset({"BUY", "SELL"}),
}

REQUIRED_PRICE_ALERT_COLUMNS: frozenset[str] = frozenset({
    "id", "asset_id", "rule", "threshold", "intent", "trail_percent",
    "trail_amount", "high_water_mark", "note", "is_active", "created_at",
    "triggered_at", "triggered_price", "acknowledged_at", "notified_at",
    "notification_skipped_at",
})


def apply_enum_values(engine: Engine) -> None:
    """ADD VALUE statements (caller holds the lock). AUTOCOMMIT so each value
    is visible to later statements."""
    statements = [
        f"ALTER TYPE currency ADD VALUE IF NOT EXISTS '{code}'"
        for code in _NEW_CURRENCY_VALUES
    ]
    statements.append("ALTER TYPE marketregion ADD VALUE IF NOT EXISTS 'IL'")
    # SQLAlchemy stores enum member names, so the label is 'INDEX'.
    statements.append("ALTER TYPE assettype ADD VALUE IF NOT EXISTS 'INDEX'")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def migrate_price_alerts(engine: Engine) -> None:
    """Additive migration for trailing-stop alert columns.

    create_all() only creates missing tables, so pre-existing price_alerts
    tables need the new columns added explicitly. Postgres-only (SQLite test
    DBs are created fresh from the models). Probes information_schema first so
    fully-migrated databases skip the DDL entirely (no locks on every boot).
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.connect() as conn:
        existing = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'price_alerts' AND table_schema = current_schema()"
            )
        }
    new_columns = {
        "intent": "alertintent",
        "trail_percent": "double precision",
        "trail_amount": "double precision",
        "high_water_mark": "double precision",
        "notified_at": "timestamptz",
        "notification_skipped_at": "timestamptz",
    }
    missing = {name: ddl for name, ddl in new_columns.items() if name not in existing}
    if not missing:
        return

    statements = [
        # SQLAlchemy Enum columns store member *names*; extend the existing type.
        "ALTER TYPE alertrule ADD VALUE IF NOT EXISTS 'TRAILING_STOP'",
        (
            "DO $$ BEGIN CREATE TYPE alertintent AS ENUM ('BUY', 'SELL'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        ),
        *[
            f"ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS {name} {ddl}"
            for name, ddl in missing.items()
        ],
        "ALTER TABLE price_alerts ALTER COLUMN threshold DROP NOT NULL",
    ]
    if "notified_at" in missing:
        # Alerts that triggered before this feature existed were already seen
        # in the UI; stamp them delivered so the first notification pass
        # doesn't blast the whole history to Telegram.
        statements.append(
            "UPDATE price_alerts SET notified_at = triggered_at "
            "WHERE triggered_at IS NOT NULL AND notified_at IS NULL"
        )
    # AUTOCOMMIT so ALTER TYPE ADD VALUE takes effect before later statements
    # reference the new value/type.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def converge(engine: Engine) -> None:
    """Bring a legacy DB to the baseline, as the old init_db() did. The caller
    holds MIGRATION_LOCK_KEY. price_snapshots is left to the revisions: its
    model moves past the baseline in 0002."""
    apply_enum_values(engine)
    Base.metadata.create_all(
        engine,
        tables=[Base.metadata.tables[name] for name in BASELINE_TABLES if name != "price_snapshots"],
    )
    migrate_price_alerts(engine)
    ensure_cutover_marker(engine)


def verify(engine: Engine) -> list[str]:
    """Problems that stop the DB being stamped at 0001; [] means adoptable."""
    with engine.connect() as conn:
        tables = set(conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )).scalars())
        problems = [f"missing table {name}" for name in BASELINE_TABLES if name not in tables]

        for type_name, expected in EXPECTED_ENUM_LABELS.items():
            labels = set(conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE t.typname = :name AND n.nspname = current_schema()"
                ),
                {"name": type_name},
            ).scalars())
            if labels != expected:
                problems.append(
                    f"enum {type_name}: missing {sorted(expected - labels)}, "
                    f"unexpected {sorted(labels - expected)}"
                )

        columns = set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'price_alerts' AND table_schema = current_schema()"
        )).scalars())
        missing = sorted(REQUIRED_PRICE_ALERT_COLUMNS - columns)
        if missing:
            problems.append(f"price_alerts missing columns: {', '.join(missing)}")
    return problems
