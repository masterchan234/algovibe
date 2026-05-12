"""add_unique_constraint_to_backtest_history

Revision ID: a846c2688f1c
Revises: b637af66b8d2
Create Date: 2026-05-12 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a846c2688f1c'
down_revision: Union[str, Sequence[str], None] = 'b637af66b8d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Cleanup duplicates in backtest_history if any exist
    op.execute("""
    DELETE FROM backtest_history WHERE ctid IN (
        SELECT ctid FROM (
            SELECT ctid, ROW_NUMBER() OVER (PARTITION BY pair, entry_price, exit_price, pnl_idr, created_at ORDER BY created_at) as rn
            FROM backtest_history
        ) t WHERE t.rn > 1
    );
    """)
    # 2. Add constraint
    op.execute("ALTER TABLE backtest_history ADD CONSTRAINT idx_backtest_history_unique UNIQUE (pair, entry_price, exit_price, pnl_idr, created_at);")

def downgrade() -> None:
    op.execute("ALTER TABLE backtest_history DROP CONSTRAINT IF EXISTS idx_backtest_history_unique;")
