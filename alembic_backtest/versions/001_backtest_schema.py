"""initial_backtest_schema

Revision ID: 001_backtest_schema
Revises:
Create Date: 2026-05-13 23:00:00.000000

Tables created:
  - ohlcv_1m           : 1-minute OHLCV candles (hypertable, 90-day retention)
  - backtest_profiles  : trading parameter profiles for tune-up
  - backtest_results   : summary result per (profile × date-range)
  - backtest_trades    : per-trade detail rows
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_backtest_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────────────
    # 1. ohlcv_1m — raw market data (TimescaleDB hypertable)
    #    Primary bucket: (pair, timestamp).
    #    90-day retention policy handled at application level or via
    #    `add_retention_policy` called after migration.
    # ─────────────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS ohlcv_1m (
        pair        TEXT        NOT NULL,
        ts          TIMESTAMPTZ NOT NULL,
        open        DOUBLE PRECISION NOT NULL,
        high        DOUBLE PRECISION NOT NULL,
        low         DOUBLE PRECISION NOT NULL,
        close       DOUBLE PRECISION NOT NULL,
        volume      DOUBLE PRECISION NOT NULL DEFAULT 0,
        PRIMARY KEY (pair, ts)
    );

    SELECT create_hypertable(
        'ohlcv_1m', 'ts',
        partitioning_column => 'pair',
        number_partitions    => 4,
        if_not_exists        => TRUE
    );

    CREATE INDEX IF NOT EXISTS idx_ohlcv_pair_ts ON ohlcv_1m (pair, ts DESC);
    """)

    # ─────────────────────────────────────────────────────────────────────
    # 2. backtest_profiles — trading variable profiles
    #
    #  Setiap row = satu "gaya trading" yang dapat di-backtest & dicompare.
    #  Semua default VALUE diambil 1:1 dari implementasi bot aktual:
    #    - config.py      : TP/SL, fee, DCA, cooldown, capital, thresholds
    #    - technicals.py  : indicator periods, weights, gate thresholds
    #    - entry_gates.py : ATR limit, EMA-50 filter, min history
    #
    #  Profile type: 'scalping' | 'swing' | 'conservative' | 'aggressive'
    #
    #  Backtester HARUS membaca semua parameter dari tabel ini dan meneruskan
    #  ke derive_trade_signal(), calculate_atr(), dll — bukan hardcode.
    # ─────────────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS backtest_profiles (
        id                          SERIAL PRIMARY KEY,
        profile_name                TEXT UNIQUE NOT NULL,
        profile_type                TEXT NOT NULL DEFAULT 'scalping',
        description                 TEXT DEFAULT '',
        is_active                   BOOLEAN NOT NULL DEFAULT FALSE,

        -- ════════════════════════════════════════════════════════════
        -- INDICATOR PERIODS
        -- Sumber: function signatures di app/domain/technicals.py
        -- ════════════════════════════════════════════════════════════

        -- RSI (Wilder smoothing) — calculate_rsi(prices, periods=14)
        rsi_period                  INT NOT NULL DEFAULT 14,

        -- MACD — calculate_macd(prices, fast=12, slow=26, signal_period=9)
        macd_fast                   INT NOT NULL DEFAULT 12,
        macd_slow                   INT NOT NULL DEFAULT 26,
        macd_signal_period          INT NOT NULL DEFAULT 9,
        -- Normalisasi MACD: (|hist| / price) * factor; bot pakai 1000
        macd_norm_factor            DOUBLE PRECISION NOT NULL DEFAULT 1000.0,

        -- Bollinger Bands — calculate_bollinger_bands(prices, period=20, std_dev=2.0)
        bb_period                   INT NOT NULL DEFAULT 20,
        bb_std                      DOUBLE PRECISION NOT NULL DEFAULT 2.0,

        -- Stochastic RSI — calculate_stochastic_rsi(prices, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3)
        stoch_rsi_period            INT NOT NULL DEFAULT 14,
        stoch_period                INT NOT NULL DEFAULT 14,
        stoch_k_smooth              INT NOT NULL DEFAULT 3,
        stoch_d_smooth              INT NOT NULL DEFAULT 3,

        -- EMA crossover — EMA-9 vs EMA-21, trend filter EMA-50
        ema_fast                    INT NOT NULL DEFAULT 9,
        ema_slow                    INT NOT NULL DEFAULT 21,
        ema_trend                   INT NOT NULL DEFAULT 50,

        -- Williams %R — calculate_williams_r(prices, period=14)
        williams_r_period           INT NOT NULL DEFAULT 14,

        -- ROC — calculate_roc(prices, period=10)
        roc_period                  INT NOT NULL DEFAULT 10,

        -- Volume MA — calculate_volume_ratio(volumes, ma_period=20)
        volume_ma_period            INT NOT NULL DEFAULT 20,

        -- OBV slope lookback (candle) — dalam calculate_obv, lookback=5
        obv_slope_lookback          INT NOT NULL DEFAULT 5,

        -- ATR — calculate_atr(highs, lows, closes, period=14) — dari entry_gates.py
        atr_period                  INT NOT NULL DEFAULT 14,

        -- Adaptive threshold: jumlah candle untuk compute_adaptive_thresholds()
        adaptive_threshold_candles  INT NOT NULL DEFAULT 50,

        -- Minimum candle history sebelum entry dibolehkan
        -- config.TECH_MIN_HISTORY_POINTS = 30, EMA-50 butuh 50
        min_candles_required        INT NOT NULL DEFAULT 50,

        -- ════════════════════════════════════════════════════════════
        -- SCORING THRESHOLDS
        -- Sumber: derive_trade_signal() — BUY_THRESHOLD=6.0, SELL_THRESHOLD=6.0, OPPOSING_MAX=4.0
        -- ════════════════════════════════════════════════════════════
        buy_threshold               DOUBLE PRECISION NOT NULL DEFAULT 6.0,
        sell_threshold              DOUBLE PRECISION NOT NULL DEFAULT 6.0,
        opposing_max_score          DOUBLE PRECISION NOT NULL DEFAULT 4.0,

        -- Adaptive RSI thresholds default (compute_adaptive_thresholds fallback)
        rsi_oversold                DOUBLE PRECISION NOT NULL DEFAULT 35.0,
        rsi_overbought              DOUBLE PRECISION NOT NULL DEFAULT 70.0,

        -- Volume reliability guard — VOLUME_RELIABILITY_THRESHOLD = 200_000_000
        volume_reliability_idr      DOUBLE PRECISION NOT NULL DEFAULT 200000000.0,

        -- ════════════════════════════════════════════════════════════
        -- INDICATOR BASE WEIGHTS
        -- Sumber: derive_trade_signal() — hardcoded _w_xxx = N.N * sw['key']
        -- Default = bot live weights. Tune untuk optimasi.
        -- ════════════════════════════════════════════════════════════
        weight_rsi                  DOUBLE PRECISION NOT NULL DEFAULT 3.0,
        weight_macd                 DOUBLE PRECISION NOT NULL DEFAULT 2.5,
        weight_bb                   DOUBLE PRECISION NOT NULL DEFAULT 2.0,
        weight_stoch_rsi            DOUBLE PRECISION NOT NULL DEFAULT 2.0,
        weight_ema_cross            DOUBLE PRECISION NOT NULL DEFAULT 1.5,
        weight_williams_r           DOUBLE PRECISION NOT NULL DEFAULT 1.5,
        weight_slope                DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        weight_roc                  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        weight_ob_pressure          DOUBLE PRECISION NOT NULL DEFAULT 2.0,
        weight_volume_ratio         DOUBLE PRECISION NOT NULL DEFAULT 1.5,
        weight_obv_slope            DOUBLE PRECISION NOT NULL DEFAULT 1.5,

        -- ════════════════════════════════════════════════════════════
        -- SECTOR WEIGHT MULTIPLIERS
        -- Sumber: _SECTOR_WEIGHTS dict di derive_trade_signal()
        -- Format: sw_<sektor>_<indikator> — default = 1.0 (no change)
        -- ════════════════════════════════════════════════════════════
        -- MEME: pump cepat, RSI terlambat, momentum & volume lebih relevan
        sw_meme_rsi                 DOUBLE PRECISION NOT NULL DEFAULT 0.7,
        sw_meme_macd                DOUBLE PRECISION NOT NULL DEFAULT 1.4,
        sw_meme_bb                  DOUBLE PRECISION NOT NULL DEFAULT 1.3,
        sw_meme_stoch               DOUBLE PRECISION NOT NULL DEFAULT 1.5,
        sw_meme_ema                 DOUBLE PRECISION NOT NULL DEFAULT 0.6,
        sw_meme_wr                  DOUBLE PRECISION NOT NULL DEFAULT 1.2,
        sw_meme_slope               DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_meme_roc                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_meme_ob                  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_meme_vol                 DOUBLE PRECISION NOT NULL DEFAULT 1.3,
        sw_meme_obv                 DOUBLE PRECISION NOT NULL DEFAULT 0.5,

        -- AI: mid-term momentum predictable
        sw_ai_rsi                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_macd                  DOUBLE PRECISION NOT NULL DEFAULT 1.2,
        sw_ai_bb                    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_stoch                 DOUBLE PRECISION NOT NULL DEFAULT 1.2,
        sw_ai_ema                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_wr                    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_slope                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_roc                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_ob                    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_vol                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_ai_obv                   DOUBLE PRECISION NOT NULL DEFAULT 0.8,

        -- L1/L2: semua indikator default 1.0 (bluechip)
        sw_l1_rsi                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_macd                  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_bb                    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_stoch                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_ema                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_wr                    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_slope                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_roc                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_ob                    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_vol                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_l1_obv                   DOUBLE PRECISION NOT NULL DEFAULT 1.0,

        -- DEFI: OBV lebih relevan (smart money flows)
        sw_defi_rsi                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_macd                DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_bb                  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_stoch               DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_ema                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_wr                  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_slope               DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_roc                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_ob                  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_vol                 DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        sw_defi_obv                 DOUBLE PRECISION NOT NULL DEFAULT 1.2,

        -- ════════════════════════════════════════════════════════════
        -- ENTRY FILTERS
        -- Sumber: entry_gates.py gate 4b (ATR) dan gate 4c (EMA-50)
        -- ════════════════════════════════════════════════════════════
        -- ATR limit — config: 3.0% normal, 4.5% loose gates
        atr_max_pct                 DOUBLE PRECISION NOT NULL DEFAULT 0.03,
        atr_max_pct_loose           DOUBLE PRECISION NOT NULL DEFAULT 0.045,
        use_loose_gates             BOOLEAN NOT NULL DEFAULT FALSE,

        -- EMA-50 trend filter: tolak entry jika close < EMA-50 dan data >= 50 candle
        ema_trend_filter_enabled    BOOLEAN NOT NULL DEFAULT TRUE,

        -- Sell-block gate: tolak BUY jika sell_score >= threshold
        sell_block_threshold        DOUBLE PRECISION NOT NULL DEFAULT 5.0,

        -- ════════════════════════════════════════════════════════════
        -- TP / SL / TRAILING
        -- Sumber: config.py TP_PCT=0.035, SL_PCT=-0.035
        -- AUTO_SL_ENABLED=False (SL off di bot live → configurable di backtest)
        -- ════════════════════════════════════════════════════════════
        target_tp_pct               DOUBLE PRECISION NOT NULL DEFAULT 0.035,
        target_sl_pct               DOUBLE PRECISION NOT NULL DEFAULT -0.035,
        -- Trailing SL: di bot live AUTO_SL_ENABLED=False
        trailing_sl_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
        -- Aktifkan trailing setelah profit >= activation_pct
        trailing_sl_activation_pct  DOUBLE PRECISION NOT NULL DEFAULT 0.02,
        -- Jarak trailing dari highest price
        trailing_sl_distance_pct    DOUBLE PRECISION NOT NULL DEFAULT 0.015,
        -- Maximum hold time (menit) sebelum force-exit — config.MAX_HOLD_MINUTES=10
        max_hold_minutes            INT NOT NULL DEFAULT 10,

        -- ════════════════════════════════════════════════════════════
        -- DCA (Averaging Down)
        -- Sumber: config.py DCA_ENABLED=True, DCA_MAX_TIERS=1,
        --         DCA_STEP_PCT=-0.015, DCA_MULTIPLIER=1.0
        -- ════════════════════════════════════════════════════════════
        dca_enabled                 BOOLEAN NOT NULL DEFAULT TRUE,
        dca_max_tiers               INT NOT NULL DEFAULT 1,
        dca_step_pct                DOUBLE PRECISION NOT NULL DEFAULT -0.015,
        dca_multiplier              DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        dca_min_tech_confidence     DOUBLE PRECISION NOT NULL DEFAULT 0.65,

        -- ════════════════════════════════════════════════════════════
        -- FEE STRUCTURE
        -- Sumber: config.py — Indodax IDR Market PRO fee (Taker)
        -- Backtester menggunakan Taker karena market order
        -- ════════════════════════════════════════════════════════════
        fee_buy_taker               DOUBLE PRECISION NOT NULL DEFAULT 0.002111,
        fee_sell_taker              DOUBLE PRECISION NOT NULL DEFAULT 0.004211,
        fee_buy_maker               DOUBLE PRECISION NOT NULL DEFAULT 0.001111,
        fee_sell_maker              DOUBLE PRECISION NOT NULL DEFAULT 0.003211,
        fee_slippage_buffer         DOUBLE PRECISION NOT NULL DEFAULT 0.0021,
        -- Round-trip taker: 0.2111% + 0.4211% = 0.6322%
        round_trip_fee_pct          DOUBLE PRECISION NOT NULL DEFAULT 0.006322,
        -- Break-even threshold: round_trip + slippage = 0.85%
        smart_break_even_pct        DOUBLE PRECISION NOT NULL DEFAULT 0.0085,
        -- Order type untuk backtest: 'taker' | 'maker'
        order_type                  TEXT NOT NULL DEFAULT 'taker',

        -- ════════════════════════════════════════════════════════════
        -- SIZING & CAPITAL
        -- ════════════════════════════════════════════════════════════
        initial_capital_idr         DOUBLE PRECISION NOT NULL DEFAULT 1000000.0,
        max_position_idr            DOUBLE PRECISION NOT NULL DEFAULT 100000.0,
        min_position_idr            DOUBLE PRECISION NOT NULL DEFAULT 10000.0,
        -- Jumlah posisi maksimum bersamaan
        max_open_positions          INT NOT NULL DEFAULT 3,

        -- ════════════════════════════════════════════════════════════
        -- COOLDOWN
        -- Sumber: config.py DUST_RECOVERY_COOLDOWN_SEC=600
        -- Cooldown antar entry pada pair yang sama (detik)
        -- ════════════════════════════════════════════════════════════
        cooldown_seconds            INT NOT NULL DEFAULT 300,

        created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    COMMENT ON TABLE backtest_profiles IS
        'Trading parameter profiles — mirror 1:1 dari parameter bot aktual (config.py + technicals.py).
Setiap row = satu gaya trading yang dapat di-backtest. Default values = nilai live bot.
Backtester membaca semua parameter dari sini dan meneruskan ke fungsi bot yang sama
(derive_trade_signal, calculate_atr, dll) — tidak ada logika duplikat.';
    """)

    # ─────────────────────────────────────────────────────────────────────
    # 3. backtest_results — agregat per (profile × pair × window)
    # ─────────────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS backtest_results (
        id                      SERIAL PRIMARY KEY,
        profile_id              INT NOT NULL REFERENCES backtest_profiles(id) ON DELETE CASCADE,
        pair                    TEXT,
        window_start            TIMESTAMPTZ NOT NULL,
        window_end              TIMESTAMPTZ NOT NULL,
        total_trades            INT NOT NULL DEFAULT 0,
        winning_trades          INT NOT NULL DEFAULT 0,
        losing_trades           INT NOT NULL DEFAULT 0,
        win_rate                DOUBLE PRECISION,
        total_pnl_idr           DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_pnl_pct           DOUBLE PRECISION NOT NULL DEFAULT 0,
        max_drawdown_pct        DOUBLE PRECISION,
        max_drawdown_idr        DOUBLE PRECISION,
        sharpe_ratio            DOUBLE PRECISION,
        profit_factor           DOUBLE PRECISION,
        avg_trade_duration_sec  INT,
        avg_pnl_per_trade_idr   DOUBLE PRECISION,
        avg_pnl_per_trade_pct   DOUBLE PRECISION,
        max_consecutive_wins    INT,
        max_consecutive_losses  INT,
        initial_capital_idr     DOUBLE PRECISION NOT NULL DEFAULT 1000000,
        final_capital_idr       DOUBLE PRECISION,
        notes                   TEXT DEFAULT '',
        created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_bt_results_profile ON backtest_results (profile_id);
    CREATE INDEX IF NOT EXISTS idx_bt_results_pair    ON backtest_results (pair);

    COMMENT ON TABLE backtest_results IS
        'Aggregated backtest statistics per profile + pair + date window. Populated by the backtester service after a full run.';
    """)

    # ─────────────────────────────────────────────────────────────────────
    # 4. backtest_trades — per-trade detail
    # ─────────────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS backtest_trades (
        id                  BIGSERIAL PRIMARY KEY,
        result_id           INT NOT NULL REFERENCES backtest_results(id) ON DELETE CASCADE,
        pair                TEXT NOT NULL,
        entry_time          TIMESTAMPTZ NOT NULL,
        exit_time           TIMESTAMPTZ,
        entry_price         DOUBLE PRECISION NOT NULL,
        exit_price          DOUBLE PRECISION,
        amount_idr          DOUBLE PRECISION NOT NULL,
        amount_crypto       DOUBLE PRECISION,
        pnl_idr             DOUBLE PRECISION,
        pnl_pct             DOUBLE PRECISION,
        exit_reason         TEXT,   -- 'TP' | 'SL' | 'TRAILING_SL' | 'SIGNAL_REVERSE' | 'END_OF_DATA'
        buy_score           DOUBLE PRECISION,
        sell_score_at_exit  DOUBLE PRECISION,
        duration_seconds    INT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_bt_trades_result ON backtest_trades (result_id);
    CREATE INDEX IF NOT EXISTS idx_bt_trades_pair   ON backtest_trades (pair, entry_time DESC);

    COMMENT ON TABLE backtest_trades IS
        'Per-trade detail rows for each backtest run. Linked to backtest_results via result_id.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS backtest_trades CASCADE;")
    op.execute("DROP TABLE IF EXISTS backtest_results CASCADE;")
    op.execute("DROP TABLE IF EXISTS backtest_profiles CASCADE;")
    op.execute("DROP TABLE IF EXISTS ohlcv_1m CASCADE;")
