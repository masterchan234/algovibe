"""initial_schema

Revision ID: c045420367aa
Revises: 
Create Date: 2026-05-07 23:32:33.919305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c045420367aa'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create standard tables
    op.execute("""
    CREATE TABLE positions (
        pair TEXT PRIMARY KEY,
        entry_price FLOAT,
        amount_crypto FLOAT,
        amount_idr FLOAT,
        status TEXT,
        entry_order_id TEXT,
        exit_order_id TEXT,
        timestamp TIMESTAMP,
        dry_run BOOLEAN,
        target_tp_pct FLOAT DEFAULT 0.05,
        target_sl_pct FLOAT DEFAULT -0.02,
        trailing_sl_price FLOAT DEFAULT 0.0,
        highest_price FLOAT DEFAULT 0.0,
        reason_backup TEXT,
        ai_confidence FLOAT DEFAULT 0.0,
        dca_tier INT DEFAULT 0
    );

    CREATE TABLE market_depth (
        pair TEXT,
        price FLOAT,
        vol FLOAT,
        is_bid INT
    );
    CREATE INDEX idx_depth_pair ON market_depth(pair);

    CREATE TABLE ticker_history (
        pair TEXT PRIMARY KEY,
        history_json TEXT,
        prediction_prob FLOAT DEFAULT 0.0,
        trend TEXT DEFAULT 'N/A',
        signal TEXT DEFAULT 'HOLD',
        ch_5m FLOAT DEFAULT 0.0,
        ch_15m FLOAT DEFAULT 0.0,
        ch_1h FLOAT DEFAULT 0.0,
        volatility FLOAT DEFAULT 0.0,
        score FLOAT DEFAULT 0.0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE bot_state (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE blacklist (
        pair TEXT PRIMARY KEY,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        api_key TEXT,
        api_secret TEXT,
        role TEXT DEFAULT 'USER',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE shadow_orders (
        pair TEXT PRIMARY KEY,
        order_id TEXT,
        price FLOAT,
        amount FLOAT,
        side TEXT DEFAULT 'BUY',
        status TEXT DEFAULT 'OPEN',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE dynamic_weights (
        feature_name TEXT PRIMARY KEY,
        weight_value FLOAT DEFAULT 1.0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE account_balances (
        asset TEXT PRIMARY KEY,
        free FLOAT DEFAULT 0.0,
        used FLOAT DEFAULT 0.0,
        total FLOAT DEFAULT 0.0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE order_book (
        id SERIAL PRIMARY KEY,
        order_id TEXT UNIQUE,
        pair TEXT,
        side TEXT,
        price FLOAT,
        amount FLOAT,
        status TEXT DEFAULT 'open',
        raw_response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE user_sessions (
        session_id TEXT PRIMARY KEY,
        user_id INT NOT NULL,
        ip_address TEXT,
        user_agent TEXT,
        is_active INT DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
    CREATE INDEX idx_user_sessions_active ON user_sessions(user_id, is_active);
    """)

    # 2. Create Hypertables (requires partitioning column to be part of unique/pk constraints if they exist)
    op.execute("""
    CREATE TABLE trade_history (
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
        PRIMARY KEY (id, created_at),
        UNIQUE (trade_id, created_at)
    );
    CREATE UNIQUE INDEX idx_trade_exit_order_id ON trade_history(exit_order_id, created_at) WHERE exit_order_id IS NOT NULL;
    SELECT create_hypertable('trade_history', 'created_at');

    CREATE TABLE trade_analytics (
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
    SELECT create_hypertable('trade_analytics', 'entry_timestamp');

    CREATE TABLE order_logs (
        id SERIAL,
        pair TEXT,
        side TEXT,
        price FLOAT,
        amount FLOAT,
        status TEXT,
        response_json TEXT,
        error_msg TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id, created_at)
    );
    SELECT create_hypertable('order_logs', 'created_at');

    CREATE TABLE system_events (
        id SERIAL,
        pair TEXT,
        type TEXT,
        message TEXT,
        level TEXT DEFAULT 'INFO',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id, created_at)
    );
    CREATE INDEX idx_events_pair ON system_events(pair, created_at);
    SELECT create_hypertable('system_events', 'created_at');

    CREATE TABLE sector_history (
        sector TEXT,
        sentiment_score FLOAT,
        momentum_score FLOAT DEFAULT 0.0,
        leader_pair TEXT DEFAULT 'N/A',
        leader_change FLOAT DEFAULT 0.0,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    SELECT create_hypertable('sector_history', 'timestamp');

    CREATE TABLE balance_history (
        id SERIAL,
        idr_free FLOAT DEFAULT 0.0,
        idr_total FLOAT DEFAULT 0.0,
        equity_total_idr FLOAT DEFAULT 0.0,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id, recorded_at)
    );
    SELECT create_hypertable('balance_history', 'recorded_at');
    """)

def downgrade() -> None:
    pass
