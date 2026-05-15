import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import DictCursor
import os
import logging
import shutil
import threading
import time as _time_module
from contextlib import contextmanager
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

from config import DATABASE_URL_LIVE, DATABASE_URL_DRYRUN, DATABASE_URL_BACKTEST, DATA_PATH
from .migrations import run_migrations
import subprocess
import re

logger = logging.getLogger(__name__)

# ── DB Backup Configuration ────────────────────────────────────────────────────
_DB_BACKUP_INTERVAL_SEC = 6 * 3600   # 6 hours
_DB_BACKUP_MAX_SNAPSHOTS = 4          # Keep last 4 snapshots (~24 hours)


class ConnectionWrapper:
    def __init__(self, conn, pool=None):
        self.conn = conn
        self.pool = pool
    def cursor(self):
        return self.conn.cursor(cursor_factory=DictCursor)
    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params or ())
        return cur
    def commit(self):
        self.conn.commit()
    def rollback(self):
        self.conn.rollback()
    def close(self):
        if self.pool:
            self.pool.putconn(self.conn)


class Database:
    def __init__(self, db_url=DATABASE_URL_LIVE):
        self.db_url = db_url
        self.pool = ThreadedConnectionPool(1, 100, self.db_url)
        self._init_db()

    def get_raw_connection(self):
        raw = self.pool.getconn()
        return ConnectionWrapper(raw, self.pool)

    @contextmanager
    def get_connection(self, autocommit=True):
        raw_conn = self.pool.getconn()
        raw_conn.autocommit = autocommit
        conn = ConnectionWrapper(raw_conn, self.pool)
        try:
            yield conn
            if not autocommit:
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            self.pool.putconn(raw_conn)

    def _init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Khusus Database Backtest (algovibe)
            if self.db_url == DATABASE_URL_BACKTEST:
                logger.info("[db] 🛠️  Bootstrapping backtest schema (ohlcv_1m)...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ohlcv_1m (
                        ts TIMESTAMPTZ NOT NULL,
                        pair TEXT NOT NULL,
                        open DOUBLE PRECISION,
                        high DOUBLE PRECISION,
                        low DOUBLE PRECISION,
                        close DOUBLE PRECISION,
                        volume DOUBLE PRECISION,
                        PRIMARY KEY (pair, ts)
                    )
                """)
                # Ubah ke hypertable jika belum (TimescaleDB)
                try:
                    cursor.execute("SELECT create_hypertable('ohlcv_1m', 'ts', if_not_exists => TRUE)")
                except: pass
                
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_pair_ts ON ohlcv_1m (pair, ts DESC)")
                
                # ── Data Seeding (Safe check for table existence) ──
                try:
                    cursor.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_name = 'backtest_profiles'
                        );
                    """)
                    table_exists = cursor.fetchone()[0]
                    
                    if table_exists:
                        cursor.execute("SELECT COUNT(*) as count FROM backtest_profiles")
                        if cursor.fetchone()['count'] == 0:
                            seed_file = os.path.join(os.getcwd(), "seed_data.sql")
                            if os.path.exists(seed_file):
                                logger.info(f"[db] 📥 Loading seed data from {seed_file}...")
                                with open(seed_file, 'r') as f:
                                    sql = f.read()
                                    for statement in sql.split(';'):
                                        if statement.strip():
                                            cursor.execute(statement)
                                logger.info("[db] ✅ Seed data loaded successfully.")
                            else:
                                logger.warning("[db] ⚠️  seed_data.sql not found. Skipping data bootstrap.")
                except Exception as e:
                    logger.warning(f"[db] ⚠️  Backtest bootstrap check skipped or failed: {e}")
                
                logger.info("[db] ✅ Backtest schema check complete.")
                return

            # 2. Database Live/Dryrun (algovibe_live / algovibe_dryrun)
            # Seeding data awal yang dibutuhkan bot untuk running pertama kali
            # (Tabel sudah dibuat oleh run_migrations di __init__)

            # Tabel khusus trading untuk setup teknikal per-koin.
            # Sumber data diisi lewat sync dari DB backtest, agar backtest dan trading terpisah.
            cursor.execute(
                """
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
                )
                """
            )
            
            # Initialize dry_run state from config default if not exists
            from config import DRY_RUN as DEFAULT_DRY_RUN
            try:
                cursor.execute("INSERT INTO bot_state (key, value) VALUES ('dry_run', %s) ON CONFLICT (key) DO NOTHING", (str(DEFAULT_DRY_RUN),))
            except: pass

            # Initialize weights if empty
            try:
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('ai_prob', 1.0) ON CONFLICT (feature_name) DO NOTHING")
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('whale_boost', 1.0) ON CONFLICT (feature_name) DO NOTHING")
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('sector_sentiment', 1.0) ON CONFLICT (feature_name) DO NOTHING")
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('imbalance', 1.0) ON CONFLICT (feature_name) DO NOTHING")
            except: pass

            
            # Committing is handled by the context manager automatically

    def get_coin_indicator_setup(self, pair: str) -> dict:
        """Ambil setup teknikal trading per-koin dari tabel coin_analysis."""
        pair_lc = str(pair or "").lower().strip()
        if not pair_lc:
            return {}
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM coin_analysis
                    WHERE pair = %s
                    """,
                    (pair_lc,),
                ).fetchone()
                if not row:
                    return {}
                return {
                    "buy_score_threshold": float(row.get("buy_score_threshold") or 6.0),
                    "sell_score_threshold": float(row.get("sell_score_threshold") or 6.0),
                    "opposing_max": float(row.get("opposing_max") or 4.0),
                    "sell_block_threshold": float(row.get("sell_block_threshold") or 5.0),
                    "target_tp_pct": float(row.get("target_tp_pct") or 0.02),
                    "target_sl_pct": float(row.get("target_sl_pct") or -0.02),
                    "cooldown_seconds": int(row.get("cooldown_seconds") or 300),
                    "max_hold_minutes": int(row.get("max_hold_minutes") or 1440),
                    "ema_trend_filter_enabled": bool(row.get("ema_trend_filter_enabled")),
                    "atr_max_pct": float(row.get("atr_max_pct") or 3.0),
                    "max_position_idr": float(row.get("max_position_idr") or 100000.0),
                    "dca_enabled": bool(row.get("dca_enabled")),
                    "dca_max_tiers": int(row.get("dca_max_tiers") or 1),
                    "rsi_oversold": float(row.get("rsi_oversold") or 35.0),
                    "rsi_overbought": float(row.get("rsi_overbought") or 70.0),
                    "bb_extreme_low": float(row.get("bb_extreme_low") or 0.10),
                    "bb_low": float(row.get("bb_low") or 0.25),
                    "bb_high": float(row.get("bb_high") or 0.75),
                    "bb_extreme_high": float(row.get("bb_extreme_high") or 0.90),
                    "stoch_k_oversold": float(row.get("stoch_k_oversold") or 20.0),
                    "stoch_d_oversold": float(row.get("stoch_d_oversold") or 30.0),
                    "stoch_k_overbought": float(row.get("stoch_k_overbought") or 80.0),
                    "stoch_d_overbought": float(row.get("stoch_d_overbought") or 70.0),
                    "williams_deep_oversold": float(row.get("williams_deep_oversold") or -85.0),
                    "williams_oversold": float(row.get("williams_oversold") or -70.0),
                    "williams_overbought": float(row.get("williams_overbought") or -30.0),
                    "williams_deep_overbought": float(row.get("williams_deep_overbought") or -15.0),
                    "slope_strong_up": float(row.get("slope_strong_up") or 0.005),
                    "slope_up": float(row.get("slope_up") or 0.001),
                    "slope_down": float(row.get("slope_down") or -0.001),
                    "slope_strong_down": float(row.get("slope_strong_down") or -0.005),
                    "roc_oversold": float(row.get("roc_oversold") or -3.0),
                    "roc_mild_down": float(row.get("roc_mild_down") or -1.0),
                    "roc_mild_up": float(row.get("roc_mild_up") or 1.0),
                    "roc_overbought": float(row.get("roc_overbought") or 3.0),
                    "ob_strong_bid": float(row.get("ob_strong_bid") or 65.0),
                    "ob_bid_dom": float(row.get("ob_bid_dom") or 55.0),
                    "ob_ask_dom": float(row.get("ob_ask_dom") or 45.0),
                    "ob_strong_ask": float(row.get("ob_strong_ask") or 35.0),
                    "volume_spike": float(row.get("volume_spike") or 2.0),
                    "volume_high": float(row.get("volume_high") or 1.5),
                    "volume_low": float(row.get("volume_low") or 0.6),
                    "volume_dry": float(row.get("volume_dry") or 0.4),
                    "obv_accumulate": float(row.get("obv_accumulate") or 5.0),
                    "obv_rising": float(row.get("obv_rising") or 2.0),
                    "obv_falling": float(row.get("obv_falling") or -2.0),
                    "obv_distribute": float(row.get("obv_distribute") or -5.0),
                    "analysis_ts": row.get("analysis_ts"),
                }
        except Exception as e:
            logger.debug("[DB] get_coin_indicator_setup error for %s: %s", pair_lc, e)
            return {}

    def upsert_coin_analysis_rows(self, rows: list[dict]) -> int:
        """Bulk upsert setup per-koin ke tabel coin_analysis (DB trading)."""
        if not rows:
            return 0
        written = 0
        with self.get_connection() as conn:
            cur = conn.cursor()
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO coin_analysis (
                        pair, analysis_ts, window_start, window_end,
                        source_target_daily_pnl, source_balance_idr,
                        buy_score_threshold, sell_score_threshold, opposing_max, sell_block_threshold,
                        target_tp_pct, target_sl_pct, cooldown_seconds, max_hold_minutes,
                        ema_trend_filter_enabled, atr_max_pct, max_position_idr, dca_enabled, dca_max_tiers,
                        rsi_oversold, rsi_overbought,
                        bb_extreme_low, bb_low, bb_high, bb_extreme_high,
                        stoch_k_oversold, stoch_d_oversold, stoch_k_overbought, stoch_d_overbought,
                        williams_deep_oversold, williams_oversold, williams_overbought, williams_deep_overbought,
                        slope_strong_up, slope_up, slope_down, slope_strong_down,
                        roc_oversold, roc_mild_down, roc_mild_up, roc_overbought,
                        ob_strong_bid, ob_bid_dom, ob_ask_dom, ob_strong_ask,
                        volume_spike, volume_high, volume_low, volume_dry,
                        obv_accumulate, obv_rising, obv_falling, obv_distribute,
                        updated_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        NOW()
                    )
                    ON CONFLICT (pair)
                    DO UPDATE SET
                        analysis_ts = EXCLUDED.analysis_ts,
                        window_start = EXCLUDED.window_start,
                        window_end = EXCLUDED.window_end,
                        source_target_daily_pnl = EXCLUDED.source_target_daily_pnl,
                        source_balance_idr = EXCLUDED.source_balance_idr,
                        buy_score_threshold = EXCLUDED.buy_score_threshold,
                        sell_score_threshold = EXCLUDED.sell_score_threshold,
                        opposing_max = EXCLUDED.opposing_max,
                        sell_block_threshold = EXCLUDED.sell_block_threshold,
                        target_tp_pct = EXCLUDED.target_tp_pct,
                        target_sl_pct = EXCLUDED.target_sl_pct,
                        cooldown_seconds = EXCLUDED.cooldown_seconds,
                        max_hold_minutes = EXCLUDED.max_hold_minutes,
                        ema_trend_filter_enabled = EXCLUDED.ema_trend_filter_enabled,
                        atr_max_pct = EXCLUDED.atr_max_pct,
                        max_position_idr = EXCLUDED.max_position_idr,
                        dca_enabled = EXCLUDED.dca_enabled,
                        dca_max_tiers = EXCLUDED.dca_max_tiers,
                        rsi_oversold = EXCLUDED.rsi_oversold,
                        rsi_overbought = EXCLUDED.rsi_overbought,
                        bb_extreme_low = EXCLUDED.bb_extreme_low,
                        bb_low = EXCLUDED.bb_low,
                        bb_high = EXCLUDED.bb_high,
                        bb_extreme_high = EXCLUDED.bb_extreme_high,
                        stoch_k_oversold = EXCLUDED.stoch_k_oversold,
                        stoch_d_oversold = EXCLUDED.stoch_d_oversold,
                        stoch_k_overbought = EXCLUDED.stoch_k_overbought,
                        stoch_d_overbought = EXCLUDED.stoch_d_overbought,
                        williams_deep_oversold = EXCLUDED.williams_deep_oversold,
                        williams_oversold = EXCLUDED.williams_oversold,
                        williams_overbought = EXCLUDED.williams_overbought,
                        williams_deep_overbought = EXCLUDED.williams_deep_overbought,
                        slope_strong_up = EXCLUDED.slope_strong_up,
                        slope_up = EXCLUDED.slope_up,
                        slope_down = EXCLUDED.slope_down,
                        slope_strong_down = EXCLUDED.slope_strong_down,
                        roc_oversold = EXCLUDED.roc_oversold,
                        roc_mild_down = EXCLUDED.roc_mild_down,
                        roc_mild_up = EXCLUDED.roc_mild_up,
                        roc_overbought = EXCLUDED.roc_overbought,
                        ob_strong_bid = EXCLUDED.ob_strong_bid,
                        ob_bid_dom = EXCLUDED.ob_bid_dom,
                        ob_ask_dom = EXCLUDED.ob_ask_dom,
                        ob_strong_ask = EXCLUDED.ob_strong_ask,
                        volume_spike = EXCLUDED.volume_spike,
                        volume_high = EXCLUDED.volume_high,
                        volume_low = EXCLUDED.volume_low,
                        volume_dry = EXCLUDED.volume_dry,
                        obv_accumulate = EXCLUDED.obv_accumulate,
                        obv_rising = EXCLUDED.obv_rising,
                        obv_falling = EXCLUDED.obv_falling,
                        obv_distribute = EXCLUDED.obv_distribute,
                        updated_at = NOW()
                    """,
                    (
                        str(r.get("pair") or "").lower(),
                        r.get("analysis_ts"),
                        r.get("window_start"),
                        r.get("window_end"),
                        r.get("target_daily_pnl"),
                        r.get("balance_idr"),
                        r.get("buy_score_threshold"),
                        r.get("sell_score_threshold"),
                        r.get("opposing_max"),
                        r.get("sell_block_threshold"),
                        r.get("target_tp_pct"),
                        r.get("target_sl_pct"),
                        r.get("cooldown_seconds"),
                        r.get("max_hold_minutes"),
                        r.get("ema_trend_filter_enabled"),
                        r.get("atr_max_pct"),
                        r.get("max_position_idr"),
                        r.get("dca_enabled"),
                        r.get("dca_max_tiers"),
                        r.get("rsi_oversold"),
                        r.get("rsi_overbought"),
                        r.get("bb_extreme_low"),
                        r.get("bb_low"),
                        r.get("bb_high"),
                        r.get("bb_extreme_high"),
                        r.get("stoch_k_oversold"),
                        r.get("stoch_d_oversold"),
                        r.get("stoch_k_overbought"),
                        r.get("stoch_d_overbought"),
                        r.get("williams_deep_oversold"),
                        r.get("williams_oversold"),
                        r.get("williams_overbought"),
                        r.get("williams_deep_overbought"),
                        r.get("slope_strong_up"),
                        r.get("slope_up"),
                        r.get("slope_down"),
                        r.get("slope_strong_down"),
                        r.get("roc_oversold"),
                        r.get("roc_mild_down"),
                        r.get("roc_mild_up"),
                        r.get("roc_overbought"),
                        r.get("ob_strong_bid"),
                        r.get("ob_bid_dom"),
                        r.get("ob_ask_dom"),
                        r.get("ob_strong_ask"),
                        r.get("volume_spike"),
                        r.get("volume_high"),
                        r.get("volume_low"),
                        r.get("volume_dry"),
                        r.get("obv_accumulate"),
                        r.get("obv_rising"),
                        r.get("obv_falling"),
                        r.get("obv_distribute"),
                    ),
                )
                written += 1
        return written
            
        # ── Migrasi dijalankan secara eksplisit oleh DatabaseRouter ──
    # ── METODE UNTUK POSITIONS ──
    
    def get_open_positions(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            rows = cursor.fetchall()
            return {row['pair']: dict(row) for row in rows}

        if dry_run is None:
            dry_run = self.is_dry_run()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM positions WHERE dry_run = %s", (dry_run,))
            return cursor.fetchone()[0] or 0

    def update_position_after_dca(self, pair, new_price, new_amount_crypto, new_amount_idr, new_tier):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE positions 
                SET entry_price = %s, amount_crypto = %s, amount_idr = %s, dca_tier = %s, timestamp = CURRENT_TIMESTAMP
                WHERE pair = %s
            ''', (new_price, new_amount_crypto, new_amount_idr, new_tier, pair))

    def get_position(self, pair):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE pair = %s", (pair,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_position(self, pair, data: dict):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Ubah boolean jadi integer untuk SQLite
            dry_run = bool(data.get('dry_run'))
            trailing_sl_price = data.get('trailing_sl_price')
            highest_price = data.get('highest_price')
            
            cursor.execute('''
                INSERT INTO positions (
                    pair, entry_price, amount_crypto, amount_idr, status, 
                    entry_order_id, exit_order_id, timestamp, dry_run, target_tp_pct, target_sl_pct,
                    trailing_sl_price, highest_price, reason_backup, ai_confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, 0.0), COALESCE(%s, 0.0), %s, %s)
                ON CONFLICT(pair) DO UPDATE SET
                    entry_price=excluded.entry_price,
                    amount_crypto=excluded.amount_crypto,
                    amount_idr=excluded.amount_idr,
                    status=excluded.status,
                    entry_order_id=excluded.entry_order_id,
                    exit_order_id=excluded.exit_order_id,
                    timestamp=excluded.timestamp,
                    dry_run=excluded.dry_run,
                    target_tp_pct=excluded.target_tp_pct,
                    target_sl_pct=excluded.target_sl_pct,
                    trailing_sl_price=COALESCE(excluded.trailing_sl_price, positions.trailing_sl_price),
                    highest_price=COALESCE(excluded.highest_price, positions.highest_price),
                    reason_backup=excluded.reason_backup,
                    ai_confidence=COALESCE(excluded.ai_confidence, positions.ai_confidence)
            ''', (
                pair,
                data.get('entry_price'),
                data.get('amount_crypto') or data.get('amount', 0),
                data.get('amount_idr'),
                data.get('status'),
                data.get('entry_order_id'),
                data.get('exit_order_id'),
                data.get('timestamp'),
                dry_run,
                data.get('target_tp_pct'),
                data.get('target_sl_pct'),
                trailing_sl_price,
                highest_price,
                data.get('reason_backup'),
                data.get('ai_confidence', 0.0)
            ))

    def delete_position(self, pair):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions WHERE pair = %s", (pair,))

    def update_position_status(self, pair, status, exit_order_id=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if exit_order_id is not None:
                cursor.execute("UPDATE positions SET status = %s, exit_order_id = %s WHERE pair = %s", (status, exit_order_id, pair))
            else:
                cursor.execute("UPDATE positions SET status = %s WHERE pair = %s", (status, pair))

    # ── METODE UNTUK TRADE HISTORY ──
    
    def insert_trade_history(self, pair, entry_price, exit_price, amount_crypto, amount_idr, pnl_idr, pnl_pct, reason, duration_seconds, dry_run, exec_type='MAKER', trade_id=None, is_manual=0, created_at=None, exit_order_id=None, ai_confidence=0.0):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            dry_run_bool = bool(dry_run)
            
            sql = '''
                INSERT INTO trade_history (
                    pair, entry_price, exit_price, amount_crypto, amount_idr,
                    pnl_idr, pnl_pct, reason, exec_type, duration_seconds, dry_run, trade_id, is_manual, exit_order_id, ai_confidence
                '''
            params = [pair, entry_price, exit_price, amount_crypto, amount_idr, pnl_idr, pnl_pct, reason, exec_type, duration_seconds, dry_run_bool, trade_id, is_manual, exit_order_id, ai_confidence]
            
            if created_at:
                sql += ", created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                params.append(created_at)
            else:
                sql += ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            
            try:
                cursor.execute(sql, tuple(params))
            except Exception as e:
                # Fallback untuk DB lama yang belum punya kolom trade_id/is_manual/exit_order_id.
                if "no column named trade_id" in str(e).lower():
                    legacy_sql = '''
                        INSERT INTO trade_history (
                            pair, entry_price, exit_price, amount_crypto, amount_idr,
                            pnl_idr, pnl_pct, reason, duration_seconds, dry_run
                    '''
                    legacy_params = [
                        pair, entry_price, exit_price, amount_crypto, amount_idr,
                        pnl_idr, pnl_pct, reason, duration_seconds, dry_run_bool,
                    ]

                    if created_at:
                        legacy_sql += ", created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        legacy_params.append(created_at)
                    else:
                        legacy_sql += ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

                    cursor.execute(legacy_sql, tuple(legacy_params))
                else:
                    raise

    def insert_backtest_data(self, pair, dna, pnl_idr, pnl_pct, entry_price, exit_price, amount_idr, duration_seconds):
        """Metode khusus untuk menyuntikkan hasil simulasi masa lalu ke database memori AI."""
        try:
            # Cast numpy types to native Python types to avoid "schema np does not exist" errors
            def _c(val):
                if hasattr(val, 'item'): return val.item() # for numpy types
                return val

            with self.get_connection() as conn:
                # 1. Simpan ke backtest_analytics (DNA)
                conn.execute('''
                    INSERT INTO backtest_analytics (
                        pair, entry_timestamp, ai_prob, whale_boost, 
                        sector_sentiment, imbalance, volatility, rsi, ema_dist, pnl_idr, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CLOSED')
                    ON CONFLICT (pair, entry_timestamp) DO NOTHING
                ''', (
                    pair, dna.get('timestamp'), _c(dna.get('ai_prob')), _c(dna.get('whale_boost')),
                    _c(dna.get('sector_sentiment')), _c(dna.get('imbalance')), _c(dna.get('volatility')),
                    _c(dna.get('rsi')), _c(dna.get('ema_dist')), _c(pnl_idr)
                ))
                
                # 2. Simpan ke backtest_history (Outcome)
                conn.execute('''
                    INSERT INTO backtest_history (
                        pair, entry_price, exit_price, amount_crypto, amount_idr,
                        pnl_idr, pnl_pct, reason, duration_seconds, dry_run
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (pair, entry_price, exit_price, pnl_idr, created_at) DO NOTHING
                ''', (
                    pair, _c(entry_price), _c(exit_price), _c(amount_idr/entry_price), _c(amount_idr),
                    _c(pnl_idr), _c(pnl_pct), 'BACKTEST', _c(duration_seconds)
                ))
        except Exception as e:
            print(f"DB Error (insert_backtest_data): {e}")

    def get_daily_stats(self, date_str=None, dry_run=None):
        """Ambil statistik performa harian (24 jam terakhir) dari trade_history."""
        if dry_run is None:
            dry_run = self.is_dry_run()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Default: Rolling 24 jam terakhir (lebih berguna untuk crypto 24/7)
            date_filter = "created_at >= NOW() - INTERVAL '24 hours'"
            
            if date_str:
                # Jika spesifik tanggal, gunakan filter tanggal kalender
                date_filter = "created_at::date = %s::date"
                params = (date_str, dry_run)
            else:
                params = (dry_run,)
                
            query = f'''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl_idr <= 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl_idr) as total_pnl_idr,
                    AVG(pnl_pct) as avg_pnl_pct,
                    SUM(CASE WHEN pnl_idr > 0 THEN pnl_idr ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl_idr < 0 THEN pnl_idr ELSE 0 END) as gross_loss,
                    SUM(amount_idr) as total_volume_idr
                FROM trade_history
                WHERE {date_filter} AND dry_run = %s AND reason != 'BACKTEST'
            '''
            cursor.execute(query, params)
            row = cursor.fetchone()
            
            stats = dict(row) if row else {}
            # Handle Nulls
            stats['total_trades'] = stats.get('total_trades') or 0
            stats['win_count'] = stats.get('win_count') or 0
            stats['loss_count'] = stats.get('loss_count') or 0
            stats['total_pnl_idr'] = stats.get('total_pnl_idr') or 0.0
            stats['avg_pnl_pct'] = stats.get('avg_pnl_pct') or 0.0
            stats['gross_profit'] = stats.get('gross_profit') or 0.0
            stats['gross_loss'] = stats.get('gross_loss') or 0.0
            stats['total_volume_idr'] = stats.get('total_volume_idr') or 0.0
            
            return stats

    def get_all_time_stats(self, dry_run=None):
        """Ambil statistik performa all-time dari trade_history."""
        if dry_run is None:
            dry_run = self.is_dry_run()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = '''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl_idr <= 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl_idr) as total_pnl_idr,
                    AVG(pnl_pct) as avg_pnl_pct,
                    SUM(CASE WHEN pnl_idr > 0 THEN pnl_idr ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl_idr < 0 THEN pnl_idr ELSE 0 END) as gross_loss,
                    SUM(amount_idr) as total_volume_idr
                FROM trade_history
                WHERE dry_run = %s AND reason != 'BACKTEST'
            '''
            cursor.execute(query, (dry_run,))
            row = cursor.fetchone()
            
            stats = dict(row) if row else {}
            stats['total_trades'] = stats.get('total_trades') or 0
            stats['win_count'] = stats.get('win_count') or 0
            stats['loss_count'] = stats.get('loss_count') or 0
            stats['total_pnl_idr'] = stats.get('total_pnl_idr') or 0.0
            stats['avg_pnl_pct'] = stats.get('avg_pnl_pct') or 0.0
            stats['gross_profit'] = stats.get('gross_profit') or 0.0
            stats['gross_loss'] = stats.get('gross_loss') or 0.0
            stats['total_volume_idr'] = stats.get('total_volume_idr') or 0.0
            
            return stats

    def get_recent_trades(self, limit=10):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_history WHERE reason != 'BACKTEST' ORDER BY created_at DESC LIMIT %s", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    # ── METODE UNTUK TICKER HISTORY ──

    def save_ticker_history_batch(self, updates_list):
        """
        [Senior Optimization] Bulk update untuk ticker history.
        Menerima list of tuples: (pair, history_list, ch_5m, ch_15m, ch_1h)
        """
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Kita gunakan query yang lebih efisien untuk executemany
            cursor.executemany('''
                INSERT INTO ticker_history (pair, history_json, prediction_prob, trend, signal,
                                          ch_5m, ch_15m, ch_1h, volatility, score, updated_at)
                VALUES (%s, %s, 0.0, 'N/A', 'HOLD', %s, %s, %s, 0.0, 0.0, CURRENT_TIMESTAMP)
                ON CONFLICT(pair) DO UPDATE SET
                    history_json=excluded.history_json,
                    ch_5m=excluded.ch_5m,
                    ch_15m=excluded.ch_15m,
                    ch_1h=excluded.ch_1h,
                    updated_at=excluded.updated_at
            ''', [(p, json.dumps(h), c5, c15, c1) for p, h, c5, c15, c1 in updates_list])

    def save_ticker_history(self, pair, history_list, prediction_prob=0.0, trend='N/A', signal='HOLD',
                            ch_5m=0.0, ch_15m=0.0, ch_1h=0.0, volatility=0.0, score=0.0):
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO ticker_history (pair, history_json, prediction_prob, trend, signal,
                                          ch_5m, ch_15m, ch_1h, volatility, score, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(pair) DO UPDATE SET
                    history_json=excluded.history_json,
                    prediction_prob=excluded.prediction_prob,
                    trend=excluded.trend,
                    signal=excluded.signal,
                    ch_5m=excluded.ch_5m,
                    ch_15m=excluded.ch_15m,
                    ch_1h=excluded.ch_1h,
                    volatility=excluded.volatility,
                    score=excluded.score,
                    updated_at=excluded.updated_at
            ''', (pair, json.dumps(history_list), prediction_prob, trend, signal, ch_5m, ch_15m, ch_1h, volatility, score))

    def get_all_ticker_history(self):
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT pair, history_json FROM ticker_history")
            rows = cursor.fetchall()
            result = {}
            for row in rows:
                try:
                    result[row['pair']] = json.loads(row['history_json'])
                except:
                    continue
            return result

    def get_ticker_history(self, pair, limit=20):
        """Ambil list harga terakhir untuk pair tertentu."""
        import json
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)
            ).fetchone()
            if row and row['history_json']:
                try:
                    history = json.loads(row['history_json'])
                    # Kembalikan data terakhir dalam format list of dict agar kompatibel dengan main.py
                    # Di main.py kita pakai: btc_prices = [h['last'] for h in btc_history]
                    # Jadi kita harus return list of dict dengan key 'last'
                    return [{"last": float(p)} for p in history[-limit:]]
                except:
                    return []
            return []

    # ── METODE UNTUK BOT STATE ──

    def save_state(self, key, value):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO bot_state (key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
            ''', (key, str(value)))

    def get_state(self, key, default=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
            row = cursor.fetchone()
            return row['value'] if row else default

    def get_state_with_time(self, key):
        """Ambil value dan timestamp updated_at."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value, updated_at FROM bot_state WHERE key = %s", (key,))
            row = cursor.fetchone()
            return (row['value'], row['updated_at']) if row else (None, None)

    def is_dry_run(self):
        """Helper to get current dry_run mode dynamically."""
        val = self.get_state("dry_run")
        if val is None:
            # Fallback to config if DB is somehow empty, though it shouldn't be
            from config import DRY_RUN as DEFAULT_DRY_RUN
            return DEFAULT_DRY_RUN
        return val.lower() in ("true", "1")

    # ── [Senior Logic] Realtime Balance Sync ──
    
    def save_balances(self, balances_dict: dict):
        """
        Simpan status wallet lengkap ke database.
        Format balances_dict: { 'IDR': {'free': 100, 'used': 50, 'total': 150}, 'AXL': {...} }
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for asset, data in balances_dict.items():
                if asset == 'info' or asset == 'timestamp' or asset == 'datetime':
                    continue
                cursor.execute('''
                    INSERT INTO account_balances (asset, free, used, total, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(asset) DO UPDATE SET
                        free=excluded.free,
                        used=excluded.used,
                        total=excluded.total,
                        updated_at=excluded.updated_at
                ''', (asset, float(data.get('free', 0)), float(data.get('used', 0)), float(data.get('total', 0))))

    def get_balances(self):
        """Ambil seluruh saldo koin dari database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM account_balances")
            rows = cursor.fetchall()
            return {row['asset']: dict(row) for row in rows}

    # ── METODE UNTUK AUDIT LOG & BLACKLIST ──

    def log_order(self, pair, side, price, amount, status, response=None, error=None):
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            resp_str = json.dumps(response) if response else None
            err_str = str(error) if error else None
            cursor.execute('''
                INSERT INTO order_logs (pair, side, price, amount, status, response_json, error_msg)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (pair, side, price, amount, status, resp_str, err_str))

    def log_system_event(self, pair, event_type, message, level='INFO'):
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO system_events (pair, type, message, level)
                VALUES (%s, %s, %s, %s)
            ''', (pair, event_type, message, level))

    def add_to_blacklist(self, pair, reason="Manual"):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO blacklist (pair, reason) VALUES (%s, %s) ON CONFLICT (pair) DO UPDATE SET reason=EXCLUDED.reason", (pair, reason))

    def get_blacklist(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT pair FROM blacklist")
            return [row['pair'] for row in cursor.fetchall()]

    def is_blacklisted(self, pair):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM blacklist WHERE pair = %s", (pair,))
            return cursor.fetchone() is not None

    def initialize_default_blacklist(self, default_coins):
        """
        Initialize default stablecoins to blacklist if table is empty.
        Called at bot startup to ensure defaults are always in database.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM blacklist")
            count = cursor.fetchone()['cnt']
            
            if count == 0:
                for pair in default_coins:
                    cursor.execute(
                        "INSERT INTO blacklist (pair, reason) VALUES (%s, %s)",
                        (pair, "DEFAULT_STABLECOIN")
                    )
                logging.info(f"✅ Initialized {len(default_coins)} default blacklist entries")

    # ── Sector & Shadow Order Methods (Predator) ─────────────────────────

    def save_sector_sentiment(self, sector, score, momentum=0.0, leader='N/A', leader_ch=0.0):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO sector_history (sector, sentiment_score, momentum_score, leader_pair, leader_change) VALUES (%s, %s, %s, %s, %s)",
                (sector, score, momentum, leader, leader_ch)
            )

    def get_latest_sector_sentiment(self, sector):
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT sentiment_score FROM sector_history WHERE sector = %s ORDER BY timestamp DESC LIMIT 1",
                (sector,)
            )
            row = cursor.fetchone()
            return row['sentiment_score'] if row else 0.0

    def get_all_latest_sector_sentiment(self):
        with self.get_connection() as conn:
            # Menggunakan subquery untuk mengambil entri terbaru per sektor
            cursor = conn.execute("""
                SELECT sector, sentiment_score, momentum_score, leader_pair, leader_change
                FROM sector_history s1
                WHERE timestamp = (SELECT MAX(timestamp) FROM sector_history s2 WHERE s1.sector = s2.sector)
            """)
            rows = cursor.fetchall()
            return {
                row['sector']: {
                    "score": row['sentiment_score'],
                    "momentum": row['momentum_score'],
                    "leader": row['leader_pair'],
                    "leader_ch": row['leader_change']
                } for row in rows
            }

    def upsert_shadow_order(self, pair, order_id, price, amount):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO shadow_orders (pair, order_id, price, amount, status) VALUES (%s, %s, %s, %s, 'OPEN') ON CONFLICT (pair) DO UPDATE SET order_id=EXCLUDED.order_id, price=EXCLUDED.price, amount=EXCLUDED.amount, status=EXCLUDED.status",
                (pair, order_id, price, amount)
            )

    def get_all_shadow_orders(self):
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM shadow_orders WHERE status = 'OPEN'")
            return [dict(row) for row in cursor.fetchall()]

    def delete_shadow_order(self, pair):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM shadow_orders WHERE pair = %s", (pair,))

    # ── Learning & Analytics Methods (Evolution) ─────────────────────────

    def save_trade_dna(self, pair, dna_data):
        try:
            with self.get_connection() as conn:
                # 'ai_prob' is the canonical key (set in trade_execution.py).
                # 'tq_prob' is the legacy key used before the rename; kept as fallback
                # so backtest or external callers that still use the old key work correctly.
                # 'sector_sentiment' is canonical; 'sector_boost' is the legacy parameter name.
                ai_prob = dna_data.get('ai_prob', dna_data.get('tq_prob', 0.0))
                sector_sentiment = dna_data.get('sector_sentiment', dna_data.get('sector_boost', 0.0))
                conn.execute(
                    """INSERT INTO trade_analytics 
                    (pair, entry_timestamp, ai_prob, whale_boost, sector_sentiment, imbalance, volatility, rsi, ema_dist, eta_minutes, llm_verdict, llm_confidence, llm_reason, dry_run) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pair, entry_timestamp) DO NOTHING""",
                    (pair, datetime.now().isoformat(), ai_prob, dna_data.get('whale_boost', 0.0),
                     sector_sentiment, dna_data.get('imbalance', 0.0), dna_data.get('volatility', 0.0),
                     dna_data.get('rsi'), dna_data.get('ema_dist'), dna_data.get('eta_minutes', 15),
                     dna_data.get('llm_verdict', 'HOLD'), dna_data.get('llm_confidence', 0.0),
                     dna_data.get('llm_reason', ''), bool(dna_data.get('dry_run', True)))
                )
        except Exception as e:
            print(f"DB Error (save_trade_dna): {e}")

    def get_quant_audit_report(self, window_hours=24, dry_run=None):
        """Menghasilkan laporan audit kuantitatif untuk 24 jam terakhir."""
        if dry_run is None:
            dry_run = self.is_dry_run()
        """
        Laporan audit kuantitatif untuk dashboard:
        - Precision per LLM verdict
        - Expectancy (IDR/trade)
        - Dominant loss reasons
        """
        report = {
            "sample_trades": 0,
            "expectancy_idr": 0.0,
            "avg_win_idr": 0.0,
            "avg_loss_idr": 0.0,
            "window_hours": int(window_hours),
            "fallback_used": False,
            "verdict_precision": {
                "BUY": {"trades": 0, "wins": 0, "precision": 0.0},
                "HOLD": {"trades": 0, "wins": 0, "precision": 0.0},
                "AVOID": {"trades": 0, "wins": 0, "precision": 0.0}
            },
            "dominant_loss_reasons": []
        }

        try:
            with self.get_connection() as conn:
                active_window_hours = int(window_hours)

                # Fallback otomatis: jika 24h masih kosong, gunakan 7 hari agar panel tetap informatif.
                sample_row = conn.execute(
                    """
                    SELECT COUNT(*) as total
                    FROM trade_history
                    WHERE created_at >= NOW() + (%s)::interval AND dry_run = %s AND reason != 'BACKTEST'
                    """,
                    (f'-{active_window_hours} hours', dry_run)
                ).fetchone()

                sample_total = int((sample_row['total'] if sample_row else 0) or 0)
                if sample_total == 0 and active_window_hours == 24:
                    active_window_hours = 24 * 7
                    report["fallback_used"] = True

                report["window_hours"] = active_window_hours

                # Expectancy dari trade_history
                stats = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        AVG(pnl_idr) as expectancy,
                        AVG(CASE WHEN pnl_idr > 0 THEN pnl_idr END) as avg_win,
                        AVG(CASE WHEN pnl_idr < 0 THEN pnl_idr END) as avg_loss
                    FROM trade_history
                    WHERE created_at >= NOW() + (%s)::interval AND dry_run = %s AND reason != 'BACKTEST'
                    """,
                    (f'-{active_window_hours} hours', dry_run)
                ).fetchone()

                report["sample_trades"] = int((stats['total'] if stats else 0) or 0)
                report["expectancy_idr"] = float((stats['expectancy'] if stats else 0) or 0.0)
                report["avg_win_idr"] = float((stats['avg_win'] if stats else 0) or 0.0)
                report["avg_loss_idr"] = float((stats['avg_loss'] if stats else 0) or 0.0)

                # Precision per verdict dari trade_analytics yang sudah CLOSED
                verdict_rows = conn.execute(
                    """
                    SELECT
                        UPPER(COALESCE(llm_verdict, 'HOLD')) as verdict,
                        COUNT(*) as trades,
                        SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins
                    FROM trade_analytics
                    WHERE status = 'CLOSED'
                      AND entry_timestamp >= NOW() + (%s)::interval
                      AND dry_run = %s
                    GROUP BY UPPER(COALESCE(llm_verdict, 'HOLD'))
                    """,
                                        (f'-{active_window_hours} hours', dry_run)
                ).fetchall()

                for row in verdict_rows:
                    verdict = row['verdict'] if row['verdict'] in report['verdict_precision'] else 'HOLD'
                    trades = int(row['trades'] or 0)
                    wins = int(row['wins'] or 0)
                    precision = (wins / trades * 100.0) if trades > 0 else 0.0
                    report['verdict_precision'][verdict] = {
                        "trades": trades,
                        "wins": wins,
                        "precision": round(precision, 2)
                    }

                # Alasan loss dominan
                loss_rows = conn.execute(
                    """
                    SELECT
                        reason,
                        COUNT(*) as cnt,
                        AVG(pnl_idr) as avg_loss
                    FROM trade_history
                    WHERE created_at >= NOW() + (%s)::interval
                      AND dry_run = %s
                      AND pnl_idr < 0
                      AND reason != 'BACKTEST'
                    GROUP BY reason
                    ORDER BY cnt DESC, avg_loss ASC
                    LIMIT 5
                    """,
                                        (f'-{active_window_hours} hours', dry_run)
                ).fetchall()

                report['dominant_loss_reasons'] = [
                    {
                        "reason": row['reason'] or 'UNKNOWN',
                        "count": int(row['cnt'] or 0),
                        "avg_loss_idr": float(row['avg_loss'] or 0.0)
                    }
                    for row in loss_rows
                ]

        except Exception as e:
            logging.error(f"[DB] get_quant_audit_report error: {e}")
            return report

    def update_llm_reasoning(self, pair, verdict, confidence, reason):
        """Update the latest trade analytics record with fresh LLM insights."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "UPDATE trade_analytics SET llm_verdict = %s, llm_confidence = %s, llm_reason = %s "
                    "WHERE pair = %s AND entry_timestamp = (SELECT MAX(entry_timestamp) FROM trade_analytics WHERE pair = %s)",
                    (verdict, confidence, reason, pair, pair)
                )
        except Exception as e:
            print(f"DB Error (update_llm_reasoning): {e}")

    def update_trade_outcome(self, pair, pnl_idr):
        try:
            with self.get_connection() as conn:
                # Update PnL untuk entri terbaru yang masih OPEN
                conn.execute(
                    "UPDATE trade_analytics SET pnl_idr = %s, status = 'CLOSED' WHERE pair = %s AND status = 'OPEN'",
                    (pnl_idr, pair)
                )
        except Exception as e:
            print(f"DB Error (update_trade_outcome): {e}")

    def get_latest_trade_dna(self, pair):
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM trade_analytics WHERE pair = %s AND status = 'CLOSED' ORDER BY entry_timestamp DESC LIMIT 1",
                    (pair,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    def get_all_closed_dnas(self, limit=1000):
        """Menarik data DNA historis untuk Pattern Matching (k-NN)."""
        try:
            with self.get_connection() as conn:
                # [Fix] Hapus JOIN ke trade_history — JOIN tersebut hanya menghasilkan 5 record
                # karena kondisi abs(pnl_idr) < 10 tidak cocok untuk trade dengan nilai berbeda.
                # trade_analytics sudah memiliki pnl_idr sendiri dari update_trade_outcome().
                # [Senior Fix] Ambil DNA dari data Live/Dry DAN data Backtest
                query = """
                    (SELECT pair, entry_timestamp, ai_prob, whale_boost, sector_sentiment, imbalance, volatility, rsi, ema_dist, pnl_idr, status FROM trade_analytics WHERE status = 'CLOSED' AND pnl_idr IS NOT NULL AND rsi IS NOT NULL AND ema_dist IS NOT NULL)
                    UNION ALL
                    (SELECT pair, entry_timestamp, ai_prob, whale_boost, sector_sentiment, imbalance, volatility, rsi, ema_dist, pnl_idr, status FROM backtest_analytics WHERE status = 'CLOSED' AND pnl_idr IS NOT NULL AND rsi IS NOT NULL AND ema_dist IS NOT NULL)
                    ORDER BY entry_timestamp DESC LIMIT %s
                """
                cursor = conn.execute(query, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"DB Error (get_all_closed_dnas): {e}")
            return []

    def update_dynamic_weight(self, feature, value):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE dynamic_weights SET weight_value = %s, updated_at = CURRENT_TIMESTAMP WHERE feature_name = %s",
                (value, feature)
            )

    def purge_old_data(self, days=7):
        """
        [Maintenance] Hapus data lama agar database tidak membengkak.
        """
        try:
            with self.get_connection() as conn:
                # Hapus ticker history > 3 hari (karena kita hanya butuh 40 candle terbaru)
                conn.execute("DELETE FROM ticker_history WHERE updated_at < NOW() - INTERVAL '3 days'")
                conn.execute("DELETE FROM trade_analytics WHERE entry_timestamp < NOW() - INTERVAL '7 days'")
                conn.execute("DELETE FROM sector_history WHERE timestamp < NOW() - INTERVAL '7 days'")
                conn.execute("DELETE FROM order_logs WHERE created_at < NOW() - INTERVAL '7 days'")
                
                # conn.execute("VACUUM") # Not needed in Postgres (autovacuum handles it)
                print(f"Database Cleanup: Old data purged (>{days} days).")
        except Exception as e:
            print(f"Cleanup Error: {e}")

    def update_weight(self, feature_name, weight_value):
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO dynamic_weights (feature_name, weight_value, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(feature_name) DO UPDATE SET 
                        weight_value=excluded.weight_value,
                        updated_at=excluded.updated_at
                """, (feature_name, weight_value))
        except Exception as e:
            print(f"Update Weight Error: {e}")

    def get_all_weights(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("SELECT feature_name, weight_value FROM dynamic_weights")
                return {row['feature_name']: row['weight_value'] for row in cursor.fetchall()}
        except Exception:
            return {}

    def get_new_listings(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT pair, MIN(updated_at) as first_seen 
                    FROM ticker_history 
                    GROUP BY pair 
                    ORDER BY first_seen DESC 
                    LIMIT 5
                """)
                return [{"pair": row['pair'], "first_seen": row['first_seen']} for row in cursor.fetchall()]
        except Exception:
            return []

    def save_market_depth(self, pair, bids, asks):
        """Simpan Top 10 Bids & Asks ke DB dengan Batch INSERT (Optimized)."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM market_depth WHERE pair = %s", (pair,))
                
                # Combine all rows into one batch insert
                values = []
                # Bids (is_bid=1)
                for b in bids[:10]:
                    values.append((pair, float(b['price']), float(b['vol']), 1))
                # Asks (is_bid=0)
                for a in asks[:10]:
                    values.append((pair, float(a['price']), float(a['vol']), 0))
                
                if values:
                    # Optimized Multi-row INSERT
                    args_str = ",".join(cursor.mogrify("(%s, %s, %s, %s)", x).decode('utf-8') for x in values)
                    cursor.execute("INSERT INTO market_depth (pair, price, vol, is_bid) VALUES " + args_str)
        except Exception as e:
            logging.debug(f"[DB] save_market_depth error: {e}")

    def upsert_order_book(self, order_id: str, pair: str, side: str, price: float,
                          amount: float, status: str, raw_response: dict = None):
        """Insert or update an entry in the order_book table."""
        import json as _json
        raw = _json.dumps(raw_response or {})
        try:
            with self.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO order_book (order_id, pair, side, price, amount, status, raw_response)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(order_id) DO UPDATE SET
                        status = excluded.status,
                        raw_response = excluded.raw_response,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (order_id, pair, side, price, amount, status, raw),
                )
        except Exception as e:
            logging.warning(f"[DB] upsert_order_book error: {e}")

    def get_market_depth(self, pair):
        """Ambil Top 10 Bids & Asks dari DB."""
        try:
            with self.get_connection() as conn:
                bids = conn.execute("SELECT price, vol FROM market_depth WHERE pair = %s AND is_bid = 1 ORDER BY price DESC", (pair,)).fetchall()
                asks = conn.execute("SELECT price, vol FROM market_depth WHERE pair = %s AND is_bid = 0 ORDER BY price ASC", (pair,)).fetchall()
                return {
                    "bids": [{"price": r['price'], "vol": r['vol']} for r in bids],
                    "asks": [{"price": r['price'], "vol": r['vol']} for r in asks]
                }
        except Exception:
            return {"bids":[], "asks":[]}

    def create_snapshot(self):
        """Create a SQL dump of the database using pg_dump."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_name = self.db_url.split('/')[-1]
        filename = f"snapshot_{db_name}_{timestamp}.sql"
        filepath = os.path.join(DATA_PATH, "backups", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # Parse connection info from self.db_url
        match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', self.db_url)
        if not match:
            logging.error(f"[DB] Could not parse DB URL for backup: {self.db_url}")
            return None
        
        user, password, host, port, dbname = match.groups()
        
        try:
            # 1. Try native pg_dump
            if shutil.which("pg_dump"):
                env = os.environ.copy()
                env["PGPASSWORD"] = password
                cmd = [
                    "pg_dump",
                    "-h", host,
                    "-p", port,
                    "-U", user,
                    "-f", filepath,
                    dbname
                ]
                subprocess.run(cmd, env=env, check=True, capture_output=True)
            # 2. Fallback to docker exec if pg_dump is missing
            elif shutil.which("docker"):
                logging.info("[DB] Native pg_dump missing, trying docker exec fallback...")
                cmd = [
                    "docker", "exec", "-e", f"PGPASSWORD={password}",
                    "algovibe-timescaledb",
                    "pg_dump", "-U", user, dbname
                ]
                with open(filepath, "w") as f:
                    subprocess.run(cmd, stdout=f, check=True)
            else:
                # No backup tools available in this container
                static_warn = "[DB] Backup skipped: neither 'pg_dump' nor 'docker' found in path."
                if not hasattr(self, "_backup_warned"):
                    logging.warning(static_warn)
                    self._backup_warned = True
                return None
            
            # Prune old snapshots
            backups_dir = os.path.dirname(filepath)
            all_backups = sorted([os.path.join(backups_dir, f) for f in os.listdir(backups_dir) if f.startswith(f"snapshot_{db_name}_")])
            if len(all_backups) > _DB_BACKUP_MAX_SNAPSHOTS:
                for old_file in all_backups[:-_DB_BACKUP_MAX_SNAPSHOTS]:
                    os.remove(old_file)
                    
            logging.info(f"[DB] Backup created: {filepath}")
            return filepath
        except Exception as e:
            logging.error(f"[DB] Backup failed: {e}")
            return None

    def prune_old_data(self, hours=24):
        """Hapus log sistem yang lebih lama dari X jam agar DB tidak bengkak."""
        try:
            with self.get_connection() as conn:
                # 1. Prune system_events (logs)
                res1 = conn.execute(
                    "DELETE FROM system_events WHERE created_at < NOW() + (%s)::interval",
                    (f'-{hours} hours',)
                )
                # 2. Prune market_depth (data orderbook lama yang tidak relevan)
                # (biasanya sudah di-update tiap detik, tapi untuk jaga-jaga)
                
                count = res1.rowcount
                if count > 0:
                    logging.info(f"[DB] Pruned {count} old system events (> {hours}h).")
                return count
        except Exception as e:
            logging.error(f"[DB] Failed to prune old data: {e}")
            return 0

    # ── METODE AUTENTIKASI ──

    def create_user(self, username, password, api_key='', api_secret=''):
        """Membuat user baru dengan password yang sudah di-hash."""
        pw_hash = generate_password_hash(password)
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, api_key, api_secret) VALUES (%s, %s, %s, %s)",
                    (username, pw_hash, api_key, api_secret)
                )
            return True
        except Exception as e:
            logging.error(f"[DB] Error creating user: {e}")
            return False

    def get_user(self, username):
        """Ambil data user berdasarkan username."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE username = %s", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def verify_user(self, username, password):
        """Verifikasi username dan password."""
        user = self.get_user(username)
        if user and check_password_hash(user['password_hash'], password):
            return user
        return None

    def update_user_keys(self, username, api_key, api_secret):
        """Update API keys for a specific user."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "UPDATE users SET api_key = %s, api_secret = %s WHERE username = %s",
                    (api_key, api_secret, username)
                )
            return True
        except Exception as e:
            logging.error(f"[DB] Error updating keys: {e}")
            return False

    # ── METODE SESSION MANAGEMENT ──

    def create_user_session(self, session_id: str, user_id: int, ip_address: str, user_agent: str) -> bool:
        """Insert a new active session record."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    """INSERT INTO user_sessions (session_id, user_id, ip_address, user_agent, is_active)
                       VALUES (%s, %s, %s, %s, 1)""",
                    (session_id, user_id, ip_address, user_agent),
                )
            return True
        except Exception as e:
            logging.error(f"[DB] create_user_session error: {e}")
            return False

    def get_active_sessions_for_user(self, user_id: int) -> list:
        """Return all active sessions for a given user."""
        try:
            with self.get_connection() as conn:
                rows = conn.execute(
                    "SELECT session_id, ip_address, user_agent, created_at FROM user_sessions WHERE user_id = %s AND is_active = 1",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logging.error(f"[DB] get_active_sessions_for_user error: {e}")
            return []

    def get_active_session(self, session_id: str) -> dict | None:
        """Return the session record if it exists and is_active=1, else None."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT session_id, user_id, ip_address, user_agent, created_at, last_activity FROM user_sessions WHERE session_id = %s AND is_active = 1",
                    (session_id,),
                ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logging.error(f"[DB] get_active_session error: {e}")
            return None

    def deactivate_session(self, session_id: str) -> bool:
        """Mark a specific session as inactive."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "UPDATE user_sessions SET is_active = 0 WHERE session_id = %s",
                    (session_id,),
                )
            return True
        except Exception as e:
            logging.error(f"[DB] deactivate_session error: {e}")
            return False

    def deactivate_all_user_sessions(self, user_id: int) -> bool:
        """Mark all sessions for a user as inactive (bulk invalidation)."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "UPDATE user_sessions SET is_active = 0 WHERE user_id = %s AND is_active = 1",
                    (user_id,),
                )
            return True
        except Exception as e:
            logging.error(f"[DB] deactivate_all_user_sessions error: {e}")
            return False

    def update_session_activity(self, session_id: str) -> None:
        """Refresh last_activity timestamp for a session."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "UPDATE user_sessions SET last_activity = CURRENT_TIMESTAMP WHERE session_id = %s",
                    (session_id,),
                )
        except Exception as e:
            logging.warning(f"[DB] update_session_activity error: {e}")

    def get_active_positions_count(self) -> int:
        """Return the number of open positions in this DB."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'"
                ).fetchone()
                return row[0] if row else 0
        except Exception as e:
            logging.error(f"[DB] get_active_positions_count error: {e}")
            return 0

class DatabaseRouter:
    """Routes all DB operations to algotrade_live.db or algotrade_dryrun.db
    based on the current dry_run flag in bot_state.

    - Mode-control keys (dry_run, dry_run_loose_gates) are always read from
      the live DB (source of truth) and written to BOTH DBs on change.
    - All other operations go to the currently-active DB.
    - Mode is cached for 2 seconds to avoid hammering SQLite.
    """
    _SYNC_KEYS = {"dry_run", "dry_run_loose_gates"}

    def __init__(self):
        from config import DATABASE_URL_LIVE, DATABASE_URL_DRYRUN
        self._live   = Database(DATABASE_URL_LIVE)
        self._dryrun = Database(DATABASE_URL_DRYRUN)
        self._mode_cache: bool | None = None
        self._mode_cache_ts: float = 0.0
        self._migrated: bool = False
        self._backup_thread: threading.Thread | None = None
        self._backup_running: bool = False
        self._start_backup_scheduler()

    def run_migrations(self):
        """Skip automatic migrations to prevent startup hangs."""
        self._migrated = True
        return

        import os, random, time
        is_primary = os.getenv("MIGRATION_PRIMARY", "false").lower() == "true"
        if not is_primary:
            time.sleep(random.uniform(2.0, 5.0))

        logger.info(f"[DB] Migration check started (Primary: {is_primary})")

        try:
            with self._live.get_connection() as conn:
                conn.autocommit = True
                cursor = conn.cursor()
                
                # Use non-blocking try_advisory_lock to prevent deadlocks
                locked = False
                max_retries = 60 if is_primary else 30
                for i in range(max_retries):
                    cursor.execute("SELECT pg_try_advisory_lock(777777)")
                    if cursor.fetchone()[0]:
                        locked = True
                        break
                    if i % 10 == 0:
                        logger.info(f"[DB] Waiting for migration lock (attempt {i}/{max_retries})...")
                    time.sleep(1)
                
                if not locked:
                    if is_primary:
                        logger.error("[DB] PRIMARY failed to acquire migration lock! Forcing migration anyway...")
                    else:
                        logger.warning("[DB] Could not acquire migration lock, assuming other service is handling it.")
                        self._migrated = True
                        return

                try:
                    from .migrations import run_migrations as _run_mig
                    _run_mig(self._live.db_url)
                    _run_mig(self._dryrun.db_url)
                    self._migrated = True
                finally:
                    if locked:
                        cursor.execute("SELECT pg_advisory_unlock(777777)")
                        logger.info("[DB] Migration lock released.")
        except Exception as e:
            logging.error(f"[DatabaseRouter] Migration failed or lock error: {e}")
            if "advisory" in str(e).lower():
                self._migrated = True

    # ── Scheduled 6-hour backup ────────────────────────────────────────────

    def _start_backup_scheduler(self) -> None:
        """Start a daemon thread that snapshots both DBs every 6 hours."""
        if self._backup_running:
            return
        self._backup_running = True
        self._backup_thread = threading.Thread(
            target=self._backup_loop,
            name="DBBackupScheduler",
            daemon=True,
        )
        self._backup_thread.start()
        logging.info("[DBBackup] 6-hour backup scheduler started.")

    def _backup_loop(self) -> None:
        """Run DB snapshots every _DB_BACKUP_INTERVAL_SEC seconds."""
        # Initial delay so the bot fully initialises before first snapshot.
        _time_module.sleep(60)
        while self._backup_running:
            try:
                self._run_scheduled_backup()
            except Exception as exc:
                logging.error("[DBBackup] Backup loop error: %s", exc)
            _time_module.sleep(_DB_BACKUP_INTERVAL_SEC)

    def _run_scheduled_backup(self) -> None:
        """Create snapshots of both live and dryrun DBs and notify via Telegram."""
        results = []
        for db_instance in (self._live, self._dryrun):
            snap = db_instance.create_snapshot()
            if snap:
                results.append(os.path.basename(snap))
        if results:
            msg = f"💾 [DB BACKUP] 6-hour snapshot created: {', '.join(results)}"
            logging.info(msg)
            try:
                from app.interfaces.telegram_bot import send_telegram_message
                send_telegram_message(msg)
            except Exception:
                pass

    def run_backup_now(self) -> None:
        """Trigger an immediate DB snapshot (e.g. from dashboard or CLI)."""
        self._run_scheduled_backup()

    def _read_mode_raw(self) -> bool:
        """Return True if dry_run, False if live. Reads from live DB with 2s cache."""
        now = _time_module.time()
        if self._mode_cache is not None and (now - self._mode_cache_ts) < 2.0:
            return self._mode_cache
        try:
            with self._live.get_connection() as conn:
                row = conn.execute("SELECT value FROM bot_state WHERE key='dry_run'").fetchone()
                val = (row[0].lower() in ("true", "1", "t", "y")) if row else True
        except Exception:
            val = True  # safe fallback: treat as dryrun if uncertain
        self._mode_cache = val
        self._mode_cache_ts = now
        return val

    def _active(self) -> Database:
        return self._dryrun if self._read_mode_raw() else self._live

    # ── Override mode-control keys so they sync across both DBs ──

    def _sync_write(self, method: str, key: str, value: str):
        """Write a key via `method` ('set_state' or 'save_state'), syncing to both DBs for control keys."""
        if key in self._SYNC_KEYS:
            getattr(self._live,   method)(key, value)
            getattr(self._dryrun, method)(key, value)
            if key == "dry_run":
                self._mode_cache = None  # invalidate cache immediately
        else:
            getattr(self._active(), method)(key, value)

    def set_state(self, key: str, value: str):
        self._sync_write("set_state", key, value)

    def save_state(self, key: str, value):
        self._sync_write("save_state", key, str(value))

    def get_state(self, key: str, default=None):
        # Mode-control keys always come from live DB (single source of truth)
        if key in self._SYNC_KEYS:
            return self._live.get_state(key, default)
        return self._active().get_state(key, default)

    # ── Expose both instances for dashboard multi-DB queries ──

    @property
    def live(self) -> Database:
        return self._live

    @property
    def dryrun(self) -> Database:
        return self._dryrun

    def get_active_positions_count(self, dry_run: int | None = None) -> int:
        """Route to the correct DB and return open positions count."""
        if dry_run == 1:
            db_obj = self._dryrun
        elif dry_run == 0:
            db_obj = self._live
        else:
            db_obj = self._active()
        return db_obj.get_active_positions_count()

    def sync_backtest_coin_analysis_to_trading(self, pairs: list[str] | None = None) -> dict:
        """
        Sinkronkan setup teknikal per-koin terbaru dari DB backtest
        (`backtest_coin_analysis`) ke DB trading (`coin_analysis`) live+dryrun.
        """
        pair_filter = [str(p).lower().strip() for p in (pairs or []) if str(p).strip()]
        try:
            with db_backtest.get_connection() as conn:
                cur = conn.cursor()
                if pair_filter:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (lower(pair))
                            lower(pair) AS pair,
                            analysis_ts,
                            window_start,
                            window_end,
                            target_daily_pnl,
                            balance_idr,
                            rec_buy_score_threshold AS buy_score_threshold,
                            rec_sell_score_threshold AS sell_score_threshold,
                            rec_opposing_max AS opposing_max,
                            rec_sell_block_threshold AS sell_block_threshold,
                            rec_target_tp_pct AS target_tp_pct,
                            rec_target_sl_pct AS target_sl_pct,
                            rec_cooldown_seconds AS cooldown_seconds,
                            rec_max_hold_minutes AS max_hold_minutes,
                            rec_ema_trend_filter_enabled AS ema_trend_filter_enabled,
                            rec_atr_max_pct AS atr_max_pct,
                            rec_max_position_idr AS max_position_idr,
                            rec_dca_enabled AS dca_enabled,
                            rec_dca_max_tiers AS dca_max_tiers,
                            rec_rsi_oversold AS rsi_oversold,
                            rec_rsi_overbought AS rsi_overbought,
                            rec_bb_extreme_low AS bb_extreme_low,
                            rec_bb_low AS bb_low,
                            rec_bb_high AS bb_high,
                            rec_bb_extreme_high AS bb_extreme_high,
                            rec_stoch_k_oversold AS stoch_k_oversold,
                            rec_stoch_d_oversold AS stoch_d_oversold,
                            rec_stoch_k_overbought AS stoch_k_overbought,
                            rec_stoch_d_overbought AS stoch_d_overbought,
                            rec_williams_deep_oversold AS williams_deep_oversold,
                            rec_williams_oversold AS williams_oversold,
                            rec_williams_overbought AS williams_overbought,
                            rec_williams_deep_overbought AS williams_deep_overbought,
                            rec_slope_strong_up AS slope_strong_up,
                            rec_slope_up AS slope_up,
                            rec_slope_down AS slope_down,
                            rec_slope_strong_down AS slope_strong_down,
                            rec_roc_oversold AS roc_oversold,
                            rec_roc_mild_down AS roc_mild_down,
                            rec_roc_mild_up AS roc_mild_up,
                            rec_roc_overbought AS roc_overbought,
                            rec_ob_strong_bid AS ob_strong_bid,
                            rec_ob_bid_dom AS ob_bid_dom,
                            rec_ob_ask_dom AS ob_ask_dom,
                            rec_ob_strong_ask AS ob_strong_ask,
                            rec_volume_spike AS volume_spike,
                            rec_volume_high AS volume_high,
                            rec_volume_low AS volume_low,
                            rec_volume_dry AS volume_dry,
                            rec_obv_accumulate AS obv_accumulate,
                            rec_obv_rising AS obv_rising,
                            rec_obv_falling AS obv_falling,
                            rec_obv_distribute AS obv_distribute
                        FROM backtest_coin_analysis
                        WHERE lower(pair) = ANY(%s)
                        ORDER BY lower(pair), analysis_ts DESC
                        """,
                        (pair_filter,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (lower(pair))
                            lower(pair) AS pair,
                            analysis_ts,
                            window_start,
                            window_end,
                            target_daily_pnl,
                            balance_idr,
                            rec_buy_score_threshold AS buy_score_threshold,
                            rec_sell_score_threshold AS sell_score_threshold,
                            rec_opposing_max AS opposing_max,
                            rec_sell_block_threshold AS sell_block_threshold,
                            rec_target_tp_pct AS target_tp_pct,
                            rec_target_sl_pct AS target_sl_pct,
                            rec_cooldown_seconds AS cooldown_seconds,
                            rec_max_hold_minutes AS max_hold_minutes,
                            rec_ema_trend_filter_enabled AS ema_trend_filter_enabled,
                            rec_atr_max_pct AS atr_max_pct,
                            rec_max_position_idr AS max_position_idr,
                            rec_dca_enabled AS dca_enabled,
                            rec_dca_max_tiers AS dca_max_tiers,
                            rec_rsi_oversold AS rsi_oversold,
                            rec_rsi_overbought AS rsi_overbought,
                            rec_bb_extreme_low AS bb_extreme_low,
                            rec_bb_low AS bb_low,
                            rec_bb_high AS bb_high,
                            rec_bb_extreme_high AS bb_extreme_high,
                            rec_stoch_k_oversold AS stoch_k_oversold,
                            rec_stoch_d_oversold AS stoch_d_oversold,
                            rec_stoch_k_overbought AS stoch_k_overbought,
                            rec_stoch_d_overbought AS stoch_d_overbought,
                            rec_williams_deep_oversold AS williams_deep_oversold,
                            rec_williams_oversold AS williams_oversold,
                            rec_williams_overbought AS williams_overbought,
                            rec_williams_deep_overbought AS williams_deep_overbought,
                            rec_slope_strong_up AS slope_strong_up,
                            rec_slope_up AS slope_up,
                            rec_slope_down AS slope_down,
                            rec_slope_strong_down AS slope_strong_down,
                            rec_roc_oversold AS roc_oversold,
                            rec_roc_mild_down AS roc_mild_down,
                            rec_roc_mild_up AS roc_mild_up,
                            rec_roc_overbought AS roc_overbought,
                            rec_ob_strong_bid AS ob_strong_bid,
                            rec_ob_bid_dom AS ob_bid_dom,
                            rec_ob_ask_dom AS ob_ask_dom,
                            rec_ob_strong_ask AS ob_strong_ask,
                            rec_volume_spike AS volume_spike,
                            rec_volume_high AS volume_high,
                            rec_volume_low AS volume_low,
                            rec_volume_dry AS volume_dry,
                            rec_obv_accumulate AS obv_accumulate,
                            rec_obv_rising AS obv_rising,
                            rec_obv_falling AS obv_falling,
                            rec_obv_distribute AS obv_distribute
                        FROM backtest_coin_analysis
                        ORDER BY lower(pair), analysis_ts DESC
                        """
                    )

                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logging.error("[DB] sync_backtest_coin_analysis_to_trading fetch error: %s", e)
            return {"ok": False, "error": str(e), "fetched": 0, "live_written": 0, "dryrun_written": 0}

        if not rows:
            return {"ok": True, "fetched": 0, "live_written": 0, "dryrun_written": 0}

        live_written = self._live.upsert_coin_analysis_rows(rows)
        dry_written = self._dryrun.upsert_coin_analysis_rows(rows)
        logging.info(
            "[DB] Synced coin_analysis from backtest: fetched=%s live=%s dryrun=%s",
            len(rows), live_written, dry_written,
        )
        return {
            "ok": True,
            "fetched": len(rows),
            "live_written": int(live_written),
            "dryrun_written": int(dry_written),
        }

    def __getattr__(self, name: str):
        return getattr(self._active(), name)



class LazyDatabase:
    """Wrapper that only instantiates the Database connection when first accessed."""
    def __init__(self, db_url):
        self.db_url = db_url
        self._instance = None
    
    def _get_instance(self):
        if self._instance is None:
            logger.info(f"[DB] Lazy-initializing connection to {self.db_url}")
            self._instance = Database(self.db_url)
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

# Instantiate global db objects
db = DatabaseRouter()
# db_backtest is lazy-loaded to prevent OperationalError in host-networked 
# containers (like market-pipe) that don't have access to 'timescaledb' DNS 
# and don't actually need backtest data.
db_backtest = LazyDatabase(DATABASE_URL_BACKTEST)
