"""add_unique_constraint_to_analytics

Revision ID: b637af66b8d2
Revises: f80e881d8a28
Create Date: 2026-05-08 21:49:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b637af66b8d2'
down_revision: Union[str, Sequence[str], None] = 'f80e881d8a28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Cleanup duplicates and add constraint for backtest_analytics
    op.execute("""
    DELETE FROM backtest_analytics WHERE ctid IN (
        SELECT ctid FROM (
            SELECT ctid, ROW_NUMBER() OVER (PARTITION BY pair, entry_timestamp ORDER BY entry_timestamp) as rn
            FROM backtest_analytics
        ) t WHERE t.rn > 1
    );
    """)
    op.execute("ALTER TABLE backtest_analytics ADD CONSTRAINT idx_backtest_analytics_unique UNIQUE (pair, entry_timestamp);")

    # 2. Cleanup duplicates and add constraint for trade_analytics
    op.execute("""
    DELETE FROM trade_analytics WHERE ctid IN (
        SELECT ctid FROM (
            SELECT ctid, ROW_NUMBER() OVER (PARTITION BY pair, entry_timestamp ORDER BY entry_timestamp) as rn
            FROM trade_analytics
        ) t WHERE t.rn > 1
    );
    """)
    op.execute("ALTER TABLE trade_analytics ADD CONSTRAINT idx_trade_analytics_unique UNIQUE (pair, entry_timestamp);")

def downgrade() -> None:
    op.execute("ALTER TABLE backtest_analytics DROP CONSTRAINT IF EXISTS idx_backtest_analytics_unique;")
    op.execute("ALTER TABLE trade_analytics DROP CONSTRAINT IF EXISTS idx_trade_analytics_unique;")
