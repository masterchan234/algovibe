"""add_timeframe_to_profiles

Revision ID: 002_add_timeframe
Revises: 001_backtest_schema
Create Date: 2026-05-14 07:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002_add_timeframe"
down_revision: Union[str, Sequence[str], None] = "001_backtest_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('backtest_profiles', sa.Column('timeframe_minutes', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('backtest_profiles', sa.Column('strategy_mode', sa.String(), nullable=False, server_default='scalping'))

def downgrade() -> None:
    op.drop_column('backtest_profiles', 'strategy_mode')
    op.drop_column('backtest_profiles', 'timeframe_minutes')
