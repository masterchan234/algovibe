"""add_intelligence_reports_and_coin_analysis

Revision ID: a1b2c3d4e5f6
Revises: e2f1a2b3c4d5
Create Date: 2026-05-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e2f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS intelligence_reports (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        excerpt TEXT,
        content TEXT,
        category TEXT DEFAULT 'GENERAL',
        author TEXT DEFAULT 'ALGOVIBE_ORACLE',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_intelligence_reports_category_ts
        ON intelligence_reports(category, created_at DESC);

    CREATE TABLE IF NOT EXISTS coin_analysis (
        pair TEXT PRIMARY KEY,
        analysis_ts TIMESTAMPTZ NOT NULL,
        window_start TIMESTAMPTZ,
        window_end TIMESTAMPTZ,
        source_target_daily_pnl DOUBLE PRECISION,
        source_balance_idr DOUBLE PRECISION,
        buy_score_threshold DOUBLE PRECISION,
        sell_score_threshold DOUBLE PRECISION,
        opposing_max DOUBLE PRECISION,
        sell_block_threshold DOUBLE PRECISION,
        target_tp_pct DOUBLE PRECISION,
        target_sl_pct DOUBLE PRECISION,
        cooldown_seconds INTEGER,
        max_hold_minutes INTEGER,
        ema_trend_filter_enabled BOOLEAN,
        atr_max_pct DOUBLE PRECISION,
        max_position_idr DOUBLE PRECISION,
        dca_enabled BOOLEAN,
        dca_max_tiers INTEGER,
        rsi_oversold DOUBLE PRECISION,
        rsi_overbought DOUBLE PRECISION,
        bb_extreme_low DOUBLE PRECISION,
        bb_low DOUBLE PRECISION,
        bb_high DOUBLE PRECISION,
        bb_extreme_high DOUBLE PRECISION,
        stoch_k_oversold DOUBLE PRECISION,
        stoch_d_oversold DOUBLE PRECISION,
        stoch_k_overbought DOUBLE PRECISION,
        stoch_d_overbought DOUBLE PRECISION,
        williams_deep_oversold DOUBLE PRECISION,
        williams_oversold DOUBLE PRECISION,
        williams_overbought DOUBLE PRECISION,
        williams_deep_overbought DOUBLE PRECISION,
        slope_strong_up DOUBLE PRECISION,
        slope_up DOUBLE PRECISION,
        slope_down DOUBLE PRECISION,
        slope_strong_down DOUBLE PRECISION,
        roc_oversold DOUBLE PRECISION,
        roc_mild_down DOUBLE PRECISION,
        roc_mild_up DOUBLE PRECISION,
        roc_overbought DOUBLE PRECISION,
        ob_strong_bid DOUBLE PRECISION,
        ob_bid_dom DOUBLE PRECISION,
        ob_ask_dom DOUBLE PRECISION,
        ob_strong_ask DOUBLE PRECISION,
        volume_spike DOUBLE PRECISION,
        volume_high DOUBLE PRECISION,
        volume_low DOUBLE PRECISION,
        volume_dry DOUBLE PRECISION,
        obv_accumulate DOUBLE PRECISION,
        obv_rising DOUBLE PRECISION,
        obv_falling DOUBLE PRECISION,
        obv_distribute DOUBLE PRECISION,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS intelligence_reports;
    DROP TABLE IF EXISTS coin_analysis;
    """)
