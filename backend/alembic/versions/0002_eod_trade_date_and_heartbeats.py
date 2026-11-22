"""EOD trade_date with a partial unique key, and job_heartbeats

Existing EOD rows get the session date they belong to: eod_snapshot rows were
taken at 16:30 ET, eod_backfill rows carry the bar timestamp (on the session
date in UTC). Weekend eod_snapshot rows of US and TASE listings (Mon-Fri
exchanges) move back to Friday; their price equals Friday's close. Then one
row per (asset_id, trade_date) is kept, the latest, which is the row
PortfolioService._latest_snapshot already reads, so no history value changes.
The deleted rows are not restored by downgrade().

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22

"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column('price_snapshots', sa.Column('trade_date', sa.Date(), nullable=True))

    op.execute(
        "UPDATE price_snapshots "
        "SET trade_date = (timestamp AT TIME ZONE 'America/New_York')::date "
        "WHERE source = 'eod_snapshot'"
    )
    op.execute(
        "UPDATE price_snapshots "
        "SET trade_date = (timestamp AT TIME ZONE 'UTC')::date "
        "WHERE source = 'eod_backfill'"
    )
    # Weekend snapshot rows of exchange-traded (Mon-Fri) listings: Sat -1, Sun -2.
    op.execute(
        "UPDATE price_snapshots p "
        "SET trade_date = p.trade_date - (EXTRACT(ISODOW FROM p.trade_date)::int - 5) "
        "FROM assets a "
        "WHERE a.id = p.asset_id AND p.source = 'eod_snapshot' "
        "AND a.asset_type <> 'CRYPTO' "
        "AND (a.market_region IN ('US', 'IL') OR a.symbol LIKE '%.TA') "
        "AND EXTRACT(ISODOW FROM p.trade_date) IN (6, 7)"
    )

    deleted = op.get_bind().exec_driver_sql(
        "DELETE FROM price_snapshots p USING ("
        "  SELECT id, row_number() OVER (PARTITION BY asset_id, trade_date"
        "                                ORDER BY timestamp DESC, id DESC) AS rn"
        "  FROM price_snapshots WHERE trade_date IS NOT NULL) d "
        "WHERE p.id = d.id AND d.rn > 1"
    ).rowcount
    log.info("0002: removed %d duplicate EOD price rows", deleted)

    op.create_index(
        'uq_price_snapshots_eod', 'price_snapshots', ['asset_id', 'trade_date'],
        unique=True, postgresql_where=sa.text('trade_date IS NOT NULL'),
    )

    op.create_table('job_heartbeats',
    sa.Column('job_name', sa.Text(), nullable=False),
    sa.Column('last_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('job_name')
    )


def downgrade() -> None:
    op.drop_table('job_heartbeats')
    op.drop_index('uq_price_snapshots_eod', table_name='price_snapshots')
    op.drop_column('price_snapshots', 'trade_date')
