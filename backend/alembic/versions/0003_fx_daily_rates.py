"""fx_daily_rates: one stored FX rate per currency per day

Daily closes of Yahoo `{CCY}=X` (units of the currency per 1 USD), used to
value past portfolio days at that day's own rate. The `currency` enum type
already exists (0001), so it is reused, not created.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CURRENCY = postgresql.ENUM(
    'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'ILS', 'HKD', 'CNY', 'KRW', 'TWD', 'SGD',
    'INR', 'SEK', 'DKK', 'NOK', 'AUD', 'CAD', 'ZAR',
    name='currency', create_type=False,
)


def upgrade() -> None:
    op.create_table('fx_daily_rates',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('currency', CURRENCY, nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('per_usd', sa.Float(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('currency', 'date', name='uq_fx_daily_currency_date'),
    )


def downgrade() -> None:
    op.drop_table('fx_daily_rates')
