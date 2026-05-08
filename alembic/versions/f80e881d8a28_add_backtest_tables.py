"""add_backtest_tables

Revision ID: f80e881d8a28
Revises: c045420367aa
Create Date: 2026-05-08 21:41:24.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f80e881d8a28'
down_revision: Union[str, Sequence[str], None] = 'c045420367aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create backtest tables
    op.execute("""
    CREATE TABLE backtest_history (
        id SERIAL,
        pair TEXT,
        entry_price FLOAT,
        exit_price FLOAT,
        amount_crypto FLOAT,
        amount_idr FLOAT,
        pnl_idr FLOAT,
        pnl_pct FLOAT,
        reason TEXT,
        exec_type TEXT DEFAULT 'MAKER',
        duration_seconds INT,
        dry_run BOOLEAN,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        trade_id TEXT,
        is_manual INT DEFAULT 0,
        exit_order_id TEXT,
        ai_confidence FLOAT DEFAULT 0.0,
        PRIMARY KEY (id, created_at)
    );
    SELECT create_hypertable('backtest_history', 'created_at', if_not_exists => TRUE);

    CREATE TABLE backtest_analytics (
        pair TEXT,
        entry_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ai_prob FLOAT DEFAULT 0.0,
        whale_boost FLOAT,
        sector_sentiment FLOAT,
        imbalance FLOAT,
        volatility FLOAT,
        eta_minutes INT DEFAULT 15,
        llm_verdict TEXT DEFAULT 'HOLD',
        llm_confidence FLOAT DEFAULT 0.0,
        llm_reason TEXT DEFAULT '',
        dry_run BOOLEAN DEFAULT true,
        pnl_idr FLOAT,
        status TEXT DEFAULT 'OPEN',
        rsi FLOAT DEFAULT 0.0,
        ema_dist FLOAT DEFAULT 0.0
    );
    SELECT create_hypertable('backtest_analytics', 'entry_timestamp', if_not_exists => TRUE);
    """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS backtest_history;")
    op.execute("DROP TABLE IF EXISTS backtest_analytics;")
