"""add_equity_performance

Revision ID: d4e5f6a7b8c9
Revises: f1a2b3c4d5e6
Create Date: 2026-05-28

Tabel equity_performance — snapshot periodik portofolio bot.

Normalisasi:
  - Satu baris per (ts, snapshot_type): tidak ada redudansi partial/transitive.
  - balance_quote, position_value, realized_pnl_day, dll adalah derived aggregates
    yang dihitung saat snapshot diambil — bukan disimpan ganda dari tabel lain.
  - exchange + dry_run sebagai metadata partisi agar query cross-mode tetap efisien.
  - Tabel hypertable TimescaleDB dengan chunk interval 1 hari, compressed >30 hari.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Tabel utama ────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS equity_performance (
        ts                  TIMESTAMPTZ      NOT NULL,
        snapshot_type       TEXT             NOT NULL DEFAULT 'auto',

        -- Saldo & valuasi
        balance_quote       DOUBLE PRECISION NOT NULL DEFAULT 0,
        position_value      DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_equity        DOUBLE PRECISION NOT NULL DEFAULT 0,

        -- PnL
        realized_pnl_day    DOUBLE PRECISION NOT NULL DEFAULT 0,
        realized_pnl_total  DOUBLE PRECISION NOT NULL DEFAULT 0,
        unrealized_pnl      DOUBLE PRECISION NOT NULL DEFAULT 0,

        -- Drawdown
        peak_equity         DOUBLE PRECISION NOT NULL DEFAULT 0,
        drawdown_pct        DOUBLE PRECISION NOT NULL DEFAULT 0,

        -- Aktivitas trading
        open_positions      INTEGER          NOT NULL DEFAULT 0,
        trade_count_day     INTEGER          NOT NULL DEFAULT 0,
        win_count_day       INTEGER          NOT NULL DEFAULT 0,
        loss_count_day      INTEGER          NOT NULL DEFAULT 0,
        win_rate_day        DOUBLE PRECISION NOT NULL DEFAULT 0,

        -- Konteks eksekusi
        exchange            TEXT             NOT NULL DEFAULT 'unknown',
        dry_run             BOOLEAN          NOT NULL DEFAULT FALSE,

        PRIMARY KEY (ts, snapshot_type)
    );
    """)

    # ── 2. Jadikan hypertable (partisi per hari) ───────────────────────────────
    op.execute("""
    SELECT create_hypertable(
        'equity_performance', 'ts',
        chunk_time_interval => INTERVAL '1 day',
        if_not_exists       => TRUE
    );
    """)

    # ── 3. Kompresi chunk > 30 hari ───────────────────────────────────────────
    op.execute("""
    ALTER TABLE equity_performance SET (
        timescaledb.compress,
        timescaledb.compress_orderby   = 'ts DESC',
        timescaledb.compress_segmentby = 'snapshot_type, exchange, dry_run'
    );
    """)
    op.execute("""
    SELECT add_compression_policy(
        'equity_performance',
        INTERVAL '30 days',
        if_not_exists => TRUE
    );
    """)

    # ── 4. Index untuk query dashboard & daily-stats ──────────────────────────
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_equity_exchange_dry_ts
        ON equity_performance (exchange, dry_run, ts DESC);
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_equity_snapshot_type_ts
        ON equity_performance (snapshot_type, ts DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS equity_performance CASCADE;")
