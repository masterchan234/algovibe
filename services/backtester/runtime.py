import logging
import os
import sys
from datetime import datetime

from services.backtester.db_backtest import get_connection, transaction

OHLCV_DAYS = 90
MIN_TRADE_IDR = 10000
DEFAULT_DAILY_TARGET_PNL_IDR = 1000.0
DEFAULT_BALANCE_IDR = 100000.0
DEFAULT_MIN_LIQUIDITY_24H_IDR = 0.0
DEFAULT_MIN_PRICE_IDR = 0.0

RSI_OVERSOLD_BASE = 35.0
RSI_OVERBOUGHT_BASE = 70.0
RSI_ADAPT_SCALE = 8.0
RSI_OVERSOLD_MIN = 20.0
RSI_OVERSOLD_MAX = 45.0
RSI_OVERBOUGHT_MIN = 55.0
RSI_OVERBOUGHT_MAX = 85.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] backtester — %(message)s",
)
logger = logging.getLogger("backtester")


def _is_running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    cgroup_paths = [
        "/proc/1/cgroup",
        "/proc/self/cgroup",
    ]
    for path in cgroup_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
            if any(token in txt for token in ("docker", "containerd", "kubepods")):
                return True
        except Exception:
            pass
    return False


def _ensure_container_runtime(command: str) -> None:
    if os.getenv("BACKTEST_ALLOW_HOST_RUN", "0") == "1":
        logger.warning("[RUNTIME] BACKTEST_ALLOW_HOST_RUN=1 set, bypassing container-only guard.")
        return
    if _is_running_in_container():
        return

    print("[ERROR] Backtester must run inside Docker container.")
    print("   Use one of these commands:")
    print(f"   docker compose run --rm backtester {command}")
    print(f"   rtk docker compose run --rm backtester {command}")
    print("   (Emergency bypass: BACKTEST_ALLOW_HOST_RUN=1)")
    raise SystemExit(2)


def _load_active_profiles() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE is_active = true ORDER BY id ASC")
        return cur.fetchall()
    finally:
        conn.close()


def _get_latest_ohlcv_ts() -> datetime | None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(ts) AS max_ts FROM ohlcv_1m")
        row = cur.fetchone()
        return row["max_ts"] if row else None
    finally:
        conn.close()


def _progress(count, total, label="", suffix=""):
    bar_len = 30
    filled_len = int(round(bar_len * count / float(total)))
    percents = round(100.0 * count / float(total), 1)
    bar = "" * filled_len + "" * (bar_len - filled_len)
    sys.stdout.write(f"[{bar}] {percents}% {count}/{total} {label} {suffix}\r")
    sys.stdout.flush()


def _run_backtest_migrations():
    try:
        from alembic import command
        from alembic.config import Config
        from services.backtester.db_backtest import DATABASE_URL_BACKTEST

        logger.info(f"[BACKTEST] [SCHEMA] Running migrations for {DATABASE_URL_BACKTEST}...")
        alembic_cfg = Config("alembic_backtest.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL_BACKTEST)
        command.upgrade(alembic_cfg, "head")
        logger.info("[SCHEMA] Backtest migrations complete.")
    except Exception as e:
        logger.warning(f"[SCHEMA] Backtest migration failed (might already be up to date): {e}")


def _ensure_schema_upgraded() -> None:
    _run_backtest_migrations()

    with transaction() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'backtest_profiles'
            );
            """
        )
        if not cur.fetchone()["exists"]:
            logger.error("[SCHEMA] Table 'backtest_profiles' still missing after migrations. Check your DB setup.")
            return

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_coin_analysis (
                id BIGSERIAL PRIMARY KEY,
                analysis_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                window_start TIMESTAMPTZ NOT NULL,
                window_end TIMESTAMPTZ NOT NULL,
                pair TEXT NOT NULL,
                n_candles BIGINT NOT NULL,
                n_days BIGINT NOT NULL,
                mu_1m DOUBLE PRECISION,
                sigma_1m DOUBLE PRECISION,
                mad_1m DOUBLE PRECISION,
                up_ratio DOUBLE PRECISION,
                jump_1pct_ratio DOUBLE PRECISION,
                acf1 DOUBLE PRECISION,
                avg_day_range DOUBLE PRECISION,
                sigma_day DOUBLE PRECISION,
                mu_day DOUBLE PRECISION,
                sharpe_day_like DOUBLE PRECISION,
                momentum_quality DOUBLE PRECISION,
                target_daily_pnl DOUBLE PRECISION NOT NULL,
                balance_idr DOUBLE PRECISION NOT NULL,
                fee_rt DOUBLE PRECISION,
                p_hat DOUBLE PRECISION,
                breakeven_p DOUBLE PRECISION,
                win_net DOUBLE PRECISION,
                loss_net DOUBLE PRECISION,
                edge_trade_raw DOUBLE PRECISION,
                edge_trade DOUBLE PRECISION,
                edge_trade_effective DOUBLE PRECISION,
                trades_needed_full_cap DOUBLE PRECISION,
                rec_buy_threshold DOUBLE PRECISION,
                rec_sell_block_threshold DOUBLE PRECISION,
                rec_buy_score_threshold DOUBLE PRECISION,
                rec_sell_score_threshold DOUBLE PRECISION,
                rec_opposing_max DOUBLE PRECISION,
                rec_target_tp_pct DOUBLE PRECISION,
                rec_target_sl_pct DOUBLE PRECISION,
                rec_cooldown_seconds INTEGER,
                rec_max_hold_minutes INTEGER,
                rec_ema_trend_filter_enabled BOOLEAN,
                rec_atr_max_pct DOUBLE PRECISION,
                rec_max_position_idr DOUBLE PRECISION,
                rec_max_open_positions INTEGER,
                rec_dca_enabled BOOLEAN,
                rec_dca_max_tiers INTEGER,
                rec_rsi_oversold DOUBLE PRECISION,
                rec_rsi_overbought DOUBLE PRECISION,
                rec_bb_extreme_low DOUBLE PRECISION,
                rec_bb_low DOUBLE PRECISION,
                rec_bb_high DOUBLE PRECISION,
                rec_bb_extreme_high DOUBLE PRECISION,
                rec_stoch_k_oversold DOUBLE PRECISION,
                rec_stoch_d_oversold DOUBLE PRECISION,
                rec_stoch_k_overbought DOUBLE PRECISION,
                rec_stoch_d_overbought DOUBLE PRECISION,
                rec_williams_deep_oversold DOUBLE PRECISION,
                rec_williams_oversold DOUBLE PRECISION,
                rec_williams_overbought DOUBLE PRECISION,
                rec_williams_deep_overbought DOUBLE PRECISION,
                rec_slope_strong_up DOUBLE PRECISION,
                rec_slope_up DOUBLE PRECISION,
                rec_slope_down DOUBLE PRECISION,
                rec_slope_strong_down DOUBLE PRECISION,
                rec_roc_oversold DOUBLE PRECISION,
                rec_roc_mild_down DOUBLE PRECISION,
                rec_roc_mild_up DOUBLE PRECISION,
                rec_roc_overbought DOUBLE PRECISION,
                rec_ob_strong_bid DOUBLE PRECISION,
                rec_ob_bid_dom DOUBLE PRECISION,
                rec_ob_ask_dom DOUBLE PRECISION,
                rec_ob_strong_ask DOUBLE PRECISION,
                rec_volume_spike DOUBLE PRECISION,
                rec_volume_high DOUBLE PRECISION,
                rec_volume_low DOUBLE PRECISION,
                rec_volume_dry DOUBLE PRECISION,
                rec_obv_accumulate DOUBLE PRECISION,
                rec_obv_rising DOUBLE PRECISION,
                rec_obv_falling DOUBLE PRECISION,
                rec_obv_distribute DOUBLE PRECISION,
                UNIQUE (pair, window_start, window_end, target_daily_pnl, balance_idr)
            )
            """
        )

        coin_cols = [
            ("rec_buy_score_threshold", "DOUBLE PRECISION"),
            ("rec_sell_score_threshold", "DOUBLE PRECISION"),
            ("rec_opposing_max", "DOUBLE PRECISION"),
            ("rec_rsi_oversold", "DOUBLE PRECISION"),
            ("rec_rsi_overbought", "DOUBLE PRECISION"),
            ("rec_bb_extreme_low", "DOUBLE PRECISION"),
            ("rec_bb_low", "DOUBLE PRECISION"),
            ("rec_bb_high", "DOUBLE PRECISION"),
            ("rec_bb_extreme_high", "DOUBLE PRECISION"),
            ("rec_stoch_k_oversold", "DOUBLE PRECISION"),
            ("rec_stoch_d_oversold", "DOUBLE PRECISION"),
            ("rec_stoch_k_overbought", "DOUBLE PRECISION"),
            ("rec_stoch_d_overbought", "DOUBLE PRECISION"),
            ("rec_williams_deep_oversold", "DOUBLE PRECISION"),
            ("rec_williams_oversold", "DOUBLE PRECISION"),
            ("rec_williams_overbought", "DOUBLE PRECISION"),
            ("rec_williams_deep_overbought", "DOUBLE PRECISION"),
            ("rec_slope_strong_up", "DOUBLE PRECISION"),
            ("rec_slope_up", "DOUBLE PRECISION"),
            ("rec_slope_down", "DOUBLE PRECISION"),
            ("rec_slope_strong_down", "DOUBLE PRECISION"),
            ("rec_roc_oversold", "DOUBLE PRECISION"),
            ("rec_roc_mild_down", "DOUBLE PRECISION"),
            ("rec_roc_mild_up", "DOUBLE PRECISION"),
            ("rec_roc_overbought", "DOUBLE PRECISION"),
            ("rec_ob_strong_bid", "DOUBLE PRECISION"),
            ("rec_ob_bid_dom", "DOUBLE PRECISION"),
            ("rec_ob_ask_dom", "DOUBLE PRECISION"),
            ("rec_ob_strong_ask", "DOUBLE PRECISION"),
            ("rec_volume_spike", "DOUBLE PRECISION"),
            ("rec_volume_high", "DOUBLE PRECISION"),
            ("rec_volume_low", "DOUBLE PRECISION"),
            ("rec_volume_dry", "DOUBLE PRECISION"),
            ("rec_obv_accumulate", "DOUBLE PRECISION"),
            ("rec_obv_rising", "DOUBLE PRECISION"),
            ("rec_obv_falling", "DOUBLE PRECISION"),
            ("rec_obv_distribute", "DOUBLE PRECISION"),
            ("edge_trade_raw", "DOUBLE PRECISION"),
            ("edge_trade_effective", "DOUBLE PRECISION"),
        ]
        for col, ctype in coin_cols:
            sp = f"sp_coin_{col}"
            try:
                cur.execute(f"SAVEPOINT {sp}")
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_name='backtest_coin_analysis' AND column_name='{col}'
                    """
                )
                if cur.fetchone()["count"] == 0:
                    cur.execute(f"ALTER TABLE backtest_coin_analysis ADD COLUMN {col} {ctype}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_result_pair_setups (
                id BIGSERIAL PRIMARY KEY,
                result_id BIGINT NOT NULL,
                profile_id BIGINT NOT NULL,
                pair TEXT NOT NULL,
                setup_json JSONB NOT NULL,
                source_analysis_ts TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (result_id, pair)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_profile_pair_scope (
                id BIGSERIAL PRIMARY KEY,
                profile_id BIGINT NOT NULL,
                pair TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (profile_id, pair)
            )
            """
        )

        cols = [
            ("target_roi_pct", "DOUBLE PRECISION DEFAULT 0.0"),
            ("target_pnl_idr", "DOUBLE PRECISION DEFAULT 0.0"),
        ]
        for col, ctype in cols:
            savepoint = f"sp_add_col_{col}"
            try:
                cur.execute(f"SAVEPOINT {savepoint}")
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_name='backtest_profiles' AND column_name='{col}'
                    """
                )
                if cur.fetchone()["count"] == 0:
                    cur.execute(f"ALTER TABLE backtest_profiles ADD COLUMN {col} {ctype}")
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                logger.warning(f"[SCHEMA] Failed to add column {col}: {e}")

        cur.execute("SELECT COUNT(*) as count FROM backtest_profiles")
        row = cur.fetchone()
        if row and row["count"] == 0:
            logger.info("[BACKTEST] [SCHEMA] Bootstrapping default profiles...")
            cur.execute(
                """
                INSERT INTO backtest_profiles (profile_name, initial_capital_idr, timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_sl_pct)
                VALUES ('Trend Pullback 5m', 1000000, 5, 'trend_pullback', 6.0, 0.035, -0.035)
                """
            )
            cur.execute(
                """
                INSERT INTO backtest_profiles (
                    profile_name, initial_capital_idr, max_position_idr, max_open_positions,
                    timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_sl_pct,
                    use_loose_gates, target_roi_pct, dca_enabled, dca_max_tiers
                ) VALUES ('jokobodo', 5000000, 200000, 10, 1, 'scalping', 5.5, 0.015, -0.04, true, 10.0, true, 3)
                """
            )
            cur.execute(
                """
                INSERT INTO backtest_profiles (
                    profile_name, initial_capital_idr, max_position_idr, min_position_idr, max_open_positions,
                    timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_sl_pct,
                    target_pnl_idr, use_loose_gates, max_hold_minutes
                ) VALUES ('anglingdharma', 100000, 10000, 10000, 5, 15, 'scalping', 4.5, 0.035, -0.02, 10000, true, 2880)
                """
            )
            logger.info("[schema] [OK] Bootstrap complete.")