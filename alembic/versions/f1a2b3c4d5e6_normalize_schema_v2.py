"""normalize_schema_v2

Full structural normalization:
  1. Rename IDR -> quote columns across all tables
  2. Add `exchange` dimension to trading tables
  3. Add `positions.created_at` (fixes duration_seconds=0 bug)
  4. Add `trade_analytics.trade_id` (FK to trade_history)
  5. Add `trade_history.fee_quote` column
  6. NEW TABLE: pair_runtime_state  (replaces 700+ bot_state KV entries)
  7. NEW TABLE: trading_sessions    (track bot uptime & session P&L)
  8. NEW TABLE: signals_log         (audit trail of all signals, hypertable)
  9. Migrate gate_ts_* / gate_skip_* from bot_state -> pair_runtime_state
 10. Stamp alembic from e2f1a2b3c4d5 -> current (handles manually-applied a1b2c3d4e5f6)

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-27 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # 1. RENAME: amount_idr -> amount_quote  (trade_history, positions, backtest)
    # =========================================================================
    op.execute("ALTER TABLE trade_history    RENAME COLUMN amount_idr TO amount_quote;")
    op.execute("ALTER TABLE positions        RENAME COLUMN amount_idr TO amount_quote;")
    op.execute("ALTER TABLE backtest_history RENAME COLUMN amount_idr TO amount_quote;")

    # =========================================================================
    # 2. RENAME: pnl_idr -> pnl_quote  (trade_history, trade_analytics, backtests)
    # =========================================================================
    op.execute("ALTER TABLE trade_history      RENAME COLUMN pnl_idr TO pnl_quote;")
    op.execute("ALTER TABLE trade_analytics    RENAME COLUMN pnl_idr TO pnl_quote;")
    op.execute("ALTER TABLE backtest_history   RENAME COLUMN pnl_idr TO pnl_quote;")
    op.execute("ALTER TABLE backtest_analytics RENAME COLUMN pnl_idr TO pnl_quote;")

    # =========================================================================
    # 3. RENAME: balance_history IDR -> quote
    # =========================================================================
    op.execute("ALTER TABLE balance_history RENAME COLUMN idr_free         TO quote_free;")
    op.execute("ALTER TABLE balance_history RENAME COLUMN idr_total        TO quote_total;")
    op.execute("ALTER TABLE balance_history RENAME COLUMN equity_total_idr TO equity_total_quote;")

    # =========================================================================
    # 4. RENAME: coin_analysis IDR columns
    # =========================================================================
    op.execute("ALTER TABLE coin_analysis RENAME COLUMN source_balance_idr TO source_balance_quote;")
    op.execute("ALTER TABLE coin_analysis RENAME COLUMN max_position_idr   TO max_position_quote;")

    # =========================================================================
    # 5. ADD exchange TEXT DEFAULT 'binance' to all trading tables
    # =========================================================================
    for tbl in ['trade_history', 'positions', 'trade_analytics',
                'balance_history', 'coin_analysis',
                'backtest_history', 'backtest_analytics']:
        op.execute(
            f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS exchange TEXT NOT NULL DEFAULT 'binance';"
        )

    # =========================================================================
    # 6. FIX duration_seconds BUG: add positions.created_at
    #    Root cause: positions.timestamp is overwritten on every reconcile.
    #    Now entry time is preserved in created_at; exit_manager calculates
    #    duration as EXTRACT(EPOCH FROM (NOW() - positions.created_at)).
    # =========================================================================
    op.execute("""
        ALTER TABLE positions
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
    """)

    # =========================================================================
    # 7. ADD trade_history.fee_quote  (track fees for net-P&L accuracy)
    # =========================================================================
    op.execute("""
        ALTER TABLE trade_history
            ADD COLUMN IF NOT EXISTS fee_quote DOUBLE PRECISION DEFAULT 0.0;
    """)

    # =========================================================================
    # 8. ADD trade_analytics.trade_id  (FK reference to trade_history.trade_id)
    # =========================================================================
    op.execute("""
        ALTER TABLE trade_analytics
            ADD COLUMN IF NOT EXISTS trade_id TEXT;
        CREATE INDEX IF NOT EXISTS idx_trade_analytics_trade_id
            ON trade_analytics(trade_id)
            WHERE trade_id IS NOT NULL;
    """)

    # =========================================================================
    # 9. NEW TABLE: pair_runtime_state
    #    Replaces 700+ bot_state KV rows (gate_ts_*, gate_skip_*, cooldown_*).
    #    PRIMARY KEY (pair, exchange) ensures one row per trading instrument.
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS pair_runtime_state (
            pair                TEXT        NOT NULL,
            exchange            TEXT        NOT NULL DEFAULT 'binance',
            consecutive_losses  INT         NOT NULL DEFAULT 0,
            sl_count_session    INT         NOT NULL DEFAULT 0,
            last_sl_at          TIMESTAMP,
            last_entry_at       TIMESTAMP,
            cooldown_until      DOUBLE PRECISION DEFAULT 0.0,
            gate_ts             TIMESTAMP,
            gate_skip_reason    TEXT,
            updated_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (pair, exchange)
        );
        CREATE INDEX IF NOT EXISTS idx_prs_exchange ON pair_runtime_state(exchange);
        COMMENT ON TABLE pair_runtime_state IS
            'Per-pair runtime state: cooldowns, SL counters, gate diagnostics. '
            'Replaces bot_state KV entries: gate_ts_*, gate_skip_*, pair_cooldown_until_*.';
    """)

    # =========================================================================
    # 10. NEW TABLE: trading_sessions
    #     Track bot restarts, per-session P&L, win/loss counters.
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS trading_sessions (
            id              SERIAL          PRIMARY KEY,
            exchange        TEXT            NOT NULL DEFAULT 'binance',
            dry_run         BOOLEAN         NOT NULL DEFAULT true,
            started_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at        TIMESTAMP,
            total_trades    INT             NOT NULL DEFAULT 0,
            win_count       INT             NOT NULL DEFAULT 0,
            loss_count      INT             NOT NULL DEFAULT 0,
            net_pnl_quote   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            peak_pnl_quote  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            status          TEXT            NOT NULL DEFAULT 'ACTIVE'
        );
        COMMENT ON TABLE trading_sessions IS
            'One row per bot process run. Tracks uptime, per-session P&L, '
            'and win/loss counters. started_at from executive_core_started_at in bot_state.';
    """)

    # =========================================================================
    # 11. NEW TABLE: signals_log (TimescaleDB hypertable)
    #     Full audit trail of every signal generated by brain_inference,
    #     whether executed or not. Enables signal quality analysis separately
    #     from trade outcomes.
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS signals_log (
            id              SERIAL,
            pair            TEXT            NOT NULL,
            exchange        TEXT            NOT NULL DEFAULT 'binance',
            signal          TEXT            NOT NULL,
            buy_score       DOUBLE PRECISION DEFAULT 0.0,
            sell_score      DOUBLE PRECISION DEFAULT 0.0,
            confidence      DOUBLE PRECISION DEFAULT 0.0,
            price           DOUBLE PRECISION,
            volume_ratio    DOUBLE PRECISION DEFAULT 0.0,
            rsi             DOUBLE PRECISION DEFAULT 0.0,
            regime          TEXT            DEFAULT 'UNKNOWN',
            executed        BOOLEAN         DEFAULT false,
            trade_id        TEXT,
            dry_run         BOOLEAN         DEFAULT true,
            created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id, created_at)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_log_pair_ts
            ON signals_log(pair, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_log_signal_executed
            ON signals_log(signal, executed, created_at DESC);
    """)
    # Make it a hypertable partitioned by created_at
    op.execute("SELECT create_hypertable('signals_log', 'created_at', if_not_exists => TRUE);")

    # =========================================================================
    # 12. MIGRATE bot_state -> pair_runtime_state
    #     Move gate_ts_* and gate_skip_* into the new table, then prune bot_state.
    # =========================================================================
    op.execute("""
        INSERT INTO pair_runtime_state (pair, exchange, gate_ts, gate_skip_reason, updated_at)
        SELECT
            LOWER(REPLACE(key, 'gate_ts_', '')) AS pair,
            'binance'                            AS exchange,
            value::TIMESTAMP                     AS gate_ts,
            (SELECT value FROM bot_state b2
             WHERE b2.key = 'gate_skip_' || LOWER(REPLACE(b1.key, 'gate_ts_', ''))) AS gate_skip_reason,
            updated_at
        FROM bot_state b1
        WHERE key LIKE 'gate_ts_%'
        ON CONFLICT (pair, exchange) DO UPDATE
            SET gate_ts          = EXCLUDED.gate_ts,
                gate_skip_reason = EXCLUDED.gate_skip_reason,
                updated_at       = EXCLUDED.updated_at;
    """)

    # Migrate pair_cooldown_until_* into pair_runtime_state
    op.execute("""
        INSERT INTO pair_runtime_state (pair, exchange, cooldown_until, updated_at)
        SELECT
            LOWER(REPLACE(key, 'pair_cooldown_until_', '')) AS pair,
            'binance'                                        AS exchange,
            value::DOUBLE PRECISION                          AS cooldown_until,
            updated_at
        FROM bot_state
        WHERE key LIKE 'pair_cooldown_until_%'
        ON CONFLICT (pair, exchange) DO UPDATE
            SET cooldown_until = EXCLUDED.cooldown_until,
                updated_at     = EXCLUDED.updated_at;
    """)

    # Delete migrated keys from bot_state (keep system config keys)
    op.execute("""
        DELETE FROM bot_state
        WHERE key LIKE 'gate_ts_%'
           OR key LIKE 'gate_skip_%'
           OR key LIKE 'pair_cooldown_until_%';
    """)

    # =========================================================================
    # 13. SEED trading_sessions with the current active session
    #     (executive_core_started_at is a Unix timestamp in bot_state)
    # =========================================================================
    op.execute("""
        INSERT INTO trading_sessions (exchange, dry_run, started_at, status)
        SELECT
            'binance',
            (SELECT value = '1' FROM bot_state WHERE key = 'dry_run'),
            to_timestamp((SELECT value::DOUBLE PRECISION FROM bot_state WHERE key = 'executive_core_started_at')),
            'ACTIVE'
        WHERE EXISTS (SELECT 1 FROM bot_state WHERE key = 'executive_core_started_at')
        ON CONFLICT DO NOTHING;
    """)

    # =========================================================================
    # 14. INDEX improvements for query performance
    # =========================================================================
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_history_pair_ts
            ON trade_history(pair, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_history_exchange
            ON trade_history(exchange, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_history_reason
            ON trade_history(reason, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_positions_exchange
            ON positions(exchange);

        CREATE INDEX IF NOT EXISTS idx_balance_history_exchange
            ON balance_history(exchange, recorded_at DESC);
    """)


def downgrade() -> None:
    # =========================================================================
    # Reverse: restore original column names and remove new structures
    # =========================================================================

    # Restore gate cooldown keys from pair_runtime_state back to bot_state
    op.execute("""
        INSERT INTO bot_state (key, value, updated_at)
        SELECT
            'gate_ts_' || pair,
            gate_ts::TEXT,
            updated_at
        FROM pair_runtime_state
        WHERE gate_ts IS NOT NULL
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

        INSERT INTO bot_state (key, value, updated_at)
        SELECT
            'gate_skip_' || pair,
            gate_skip_reason,
            updated_at
        FROM pair_runtime_state
        WHERE gate_skip_reason IS NOT NULL
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

        INSERT INTO bot_state (key, value, updated_at)
        SELECT
            'pair_cooldown_until_' || pair,
            cooldown_until::TEXT,
            updated_at
        FROM pair_runtime_state
        WHERE cooldown_until IS NOT NULL AND cooldown_until > 0
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """)

    # Drop new tables
    op.execute("DROP TABLE IF EXISTS signals_log;")
    op.execute("DROP TABLE IF EXISTS trading_sessions;")
    op.execute("DROP TABLE IF EXISTS pair_runtime_state;")

    # Remove added columns
    op.execute("ALTER TABLE trade_history   DROP COLUMN IF EXISTS fee_quote;")
    op.execute("ALTER TABLE trade_analytics DROP COLUMN IF EXISTS trade_id;")
    op.execute("ALTER TABLE positions       DROP COLUMN IF EXISTS created_at;")

    for tbl in ['trade_history', 'positions', 'trade_analytics',
                'balance_history', 'coin_analysis',
                'backtest_history', 'backtest_analytics']:
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS exchange;")

    # Restore IDR column names
    op.execute("ALTER TABLE coin_analysis RENAME COLUMN source_balance_quote TO source_balance_idr;")
    op.execute("ALTER TABLE coin_analysis RENAME COLUMN max_position_quote   TO max_position_idr;")

    op.execute("ALTER TABLE balance_history RENAME COLUMN quote_free          TO idr_free;")
    op.execute("ALTER TABLE balance_history RENAME COLUMN quote_total         TO idr_total;")
    op.execute("ALTER TABLE balance_history RENAME COLUMN equity_total_quote  TO equity_total_idr;")

    op.execute("ALTER TABLE backtest_analytics RENAME COLUMN pnl_quote TO pnl_idr;")
    op.execute("ALTER TABLE backtest_history   RENAME COLUMN pnl_quote TO pnl_idr;")
    op.execute("ALTER TABLE trade_analytics    RENAME COLUMN pnl_quote TO pnl_idr;")
    op.execute("ALTER TABLE trade_history      RENAME COLUMN pnl_quote TO pnl_idr;")

    op.execute("ALTER TABLE backtest_history RENAME COLUMN amount_quote TO amount_idr;")
    op.execute("ALTER TABLE positions        RENAME COLUMN amount_quote TO amount_idr;")
    op.execute("ALTER TABLE trade_history    RENAME COLUMN amount_quote TO amount_idr;")
