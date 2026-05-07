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

from config import DATABASE_URL_LIVE, DATABASE_URL_DRYRUN
from .migrations import run_migrations

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
        self.pool = ThreadedConnectionPool(1, 20, self.db_url)
        self._init_db()

    @contextmanager
    
    def get_raw_connection(self):
        raw = self.pool.getconn()
        return ConnectionWrapper(raw, self.pool)

    @contextmanager
    def get_connection(self):
        raw_conn = self.pool.getconn()
        conn = ConnectionWrapper(raw_conn, self.pool)
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            self.pool.putconn(raw_conn)

    def _init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Seeding data awal yang dibutuhkan bot untuk running pertama kali
            # (Tabel sudah dibuat oleh run_migrations di __init__)
            
            # Initialize dry_run state from config default if not exists
            from config import DRY_RUN as DEFAULT_DRY_RUN
            try:
                cursor.execute("INSERT INTO bot_state (key, value) VALUES ('dry_run', %s) ON CONFLICT DO NOTHING ON CONFLICT (key) DO NOTHING", (str(DEFAULT_DRY_RUN),))
            except: pass

            # Initialize weights if empty
            try:
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('ai_prob', 1.0)")
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('whale_boost', 1.0)")
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('sector_sentiment', 1.0)")
                cursor.execute("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES ('imbalance', 1.0)")
            except: pass

            
            # Committing is handled by the context manager automatically
            
        # ── Jalankan Sistem Migrasi Terpisah ──
        run_migrations(self.db_url)
    # ── METODE UNTUK POSITIONS ──
    
    def get_open_positions(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            rows = cursor.fetchall()
            return {row['pair']: dict(row) for row in rows}

    def get_active_positions_count(self, dry_run=None):
        """Menghitung jumlah posisi terbuka untuk mode tertentu (Dry Run/Live)."""
        if dry_run is None:
            dry_run = 1 if self.is_dry_run() else 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM positions WHERE dry_run = %s", (int(dry_run),))
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
            dry_run = 1 if data.get('dry_run') else 0
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
            dry_run_int = 1 if dry_run else 0
            
            sql = '''
                INSERT INTO trade_history (
                    pair, entry_price, exit_price, amount_crypto, amount_idr,
                    pnl_idr, pnl_pct, reason, exec_type, duration_seconds, dry_run, trade_id, is_manual, exit_order_id, ai_confidence
                '''
            params = [pair, entry_price, exit_price, amount_crypto, amount_idr, pnl_idr, pnl_pct, reason, exec_type, duration_seconds, dry_run_int, trade_id, is_manual, exit_order_id, ai_confidence]
            
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
                        pnl_idr, pnl_pct, reason, duration_seconds, dry_run_int,
                    ]

                    if created_at:
                        legacy_sql += ", created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        legacy_params.append(created_at)
                    else:
                        legacy_sql += ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (pair) DO UPDATE SET history_json=EXCLUDED.history_json, prediction_prob=EXCLUDED.prediction_prob, trend=EXCLUDED.trend, signal=EXCLUDED.signal, ch_5m=EXCLUDED.ch_5m, ch_15m=EXCLUDED.ch_15m, ch_1h=EXCLUDED.ch_1h, volatility=EXCLUDED.volatility, score=EXCLUDED.score, updated_at=CURRENT_TIMESTAMP"

                    cursor.execute(legacy_sql, tuple(legacy_params))
                else:
                    raise

    def insert_backtest_data(self, pair, dna, pnl_idr, pnl_pct, entry_price, exit_price, amount_idr, duration_seconds):
        """Metode khusus untuk menyuntikkan hasil simulasi masa lalu ke database memori AI."""
        try:
            with self.get_connection() as conn:
                # 1. Simpan ke trade_analytics (DNA)
                conn.execute('''
                    INSERT INTO trade_analytics (
                        pair, entry_timestamp, ai_prob, whale_boost, 
                        sector_sentiment, imbalance, volatility, rsi, ema_dist, pnl_idr, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CLOSED')
                ''', (
                    pair, dna.get('timestamp'), dna.get('ai_prob'), dna.get('whale_boost'),
                    dna.get('sector_sentiment'), dna.get('imbalance'), dna.get('volatility'),
                    dna.get('rsi'), dna.get('ema_dist'), pnl_idr
                ))
                
                # 2. Simpan ke trade_history (Outcome)
                conn.execute('''
                    INSERT INTO trade_history (
                        pair, entry_price, exit_price, amount_crypto, amount_idr,
                        pnl_idr, pnl_pct, reason, duration_seconds, dry_run
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                ''', (
                    pair, entry_price, exit_price, amount_idr/entry_price, amount_idr,
                    pnl_idr, pnl_pct, 'BACKTEST', duration_seconds
                ))
        except Exception as e:
            print(f"DB Error (insert_backtest_data): {e}")

    def get_daily_stats(self, date_str=None, dry_run=None):
        """Ambil statistik performa harian (24 jam terakhir) dari trade_history."""
        if dry_run is None:
            dry_run = 1 if self.is_dry_run() else 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Default: Rolling 24 jam terakhir (lebih berguna untuk crypto 24/7)
            date_filter = "created_at >= NOW() - INTERVAL '24 hours'"
            
            if date_str:
                # Jika spesifik tanggal, gunakan filter tanggal kalender
                date_filter = f"date(created_at, 'localtime') = date('{date_str}')"
                
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
                WHERE {date_filter} AND dry_run = %s
            '''
            cursor.execute(query, (int(dry_run),))
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
            dry_run = 1 if self.is_dry_run() else 0
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
                WHERE dry_run = %s
            '''
            cursor.execute(query, (int(dry_run),))
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
            cursor.execute("SELECT * FROM trade_history ORDER BY created_at DESC LIMIT %s", (limit,))
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (pair, datetime.now().isoformat(), ai_prob, dna_data.get('whale_boost', 0.0),
                     sector_sentiment, dna_data.get('imbalance', 0.0), dna_data.get('volatility', 0.0),
                     dna_data.get('rsi'), dna_data.get('ema_dist'), dna_data.get('eta_minutes', 15),
                     dna_data.get('llm_verdict', 'HOLD'), dna_data.get('llm_confidence', 0.0),
                     dna_data.get('llm_reason', ''), 1 if dna_data.get('dry_run', True) else 0)
                )
        except Exception as e:
            print(f"DB Error (save_trade_dna): {e}")

    def get_quant_audit_report(self, window_hours=24, dry_run=None):
        """Menghasilkan laporan audit kuantitatif untuk 24 jam terakhir."""
        if dry_run is None:
            dry_run = 1 if self.is_dry_run() else 0
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
                    WHERE created_at >= datetime('now', %s) AND dry_run = %s
                    """,
                    (f'-{active_window_hours} hours', int(dry_run))
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
                    WHERE created_at >= datetime('now', %s) AND dry_run = %s
                    """,
                    (f'-{active_window_hours} hours', int(dry_run))
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
                      AND entry_timestamp >= datetime('now', %s)
                      AND dry_run = %s
                    GROUP BY UPPER(COALESCE(llm_verdict, 'HOLD'))
                    """,
                                        (f'-{active_window_hours} hours', int(dry_run))
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
                    WHERE created_at >= datetime('now', %s)
                      AND dry_run = %s
                      AND pnl_idr < 0
                    GROUP BY reason
                    ORDER BY cnt DESC, avg_loss ASC
                    LIMIT 5
                    """,
                                        (f'-{active_window_hours} hours', int(dry_run))
                ).fetchall()

                report['dominant_loss_reasons'] = [
                    {
                        "reason": row['reason'] or 'UNKNOWN',
                        "count": int(row['cnt'] or 0),
                        "avg_loss_idr": float(row['avg_loss'] or 0.0)
                    }
                    for row in loss_rows
                ]

            return report
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
                query = """
                    SELECT * FROM trade_analytics
                    WHERE status = 'CLOSED' AND pnl_idr IS NOT NULL
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
                conn.execute("DELETE FROM ticker_history WHERE updated_at < datetime('now', '-3 days')")
                # Hapus analytics > 7 hari
                conn.execute("DELETE FROM trade_analytics WHERE entry_timestamp < datetime('now', '-7 days')")
                # Hapus sector history > 7 hari
                conn.execute("DELETE FROM sector_history WHERE timestamp < datetime('now', '-7 days')")
                # Hapus order logs > 7 hari
                conn.execute("DELETE FROM order_logs WHERE created_at < datetime('now', '-7 days')")
                
                conn.execute("VACUUM") # Kompres file database
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
                    SELECT pair_id, MIN(updated_at) as first_seen 
                    FROM ticker_history 
                    GROUP BY pair_id 
                    ORDER BY first_seen DESC 
                    LIMIT 5
                """)
                return [{"pair": row['pair_id'], "first_seen": row['first_seen']} for row in cursor.fetchall()]
        except Exception:
            return []

    def save_market_depth(self, pair, bids, asks):
        """Simpan Top 10 Bids & Asks ke DB."""
        try:
            with self.get_connection() as conn:
                conn.execute("DELETE FROM market_depth WHERE pair = %s", (pair,))
                # Simpan bids (is_bid=1)
                for b in bids:
                    conn.execute("INSERT INTO market_depth (pair, price, vol, is_bid) VALUES (%s, %s, %s, 1)",
                               (pair, b['price'], b['vol']))
                # Simpan asks (is_bid=0)
                for a in asks:
                    conn.execute("INSERT INTO market_depth (pair, price, vol, is_bid) VALUES (%s, %s, %s, 0)",
                               (pair, a['price'], a['vol']))
        except Exception: pass

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

    def prune_old_data(self, hours=24):
        """Hapus log sistem yang lebih lama dari X jam agar DB tidak bengkak."""
        try:
            with self.get_connection() as conn:
                # 1. Prune system_events (logs)
                res1 = conn.execute(
                    "DELETE FROM system_events WHERE created_at < datetime('now', %s)",
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
        self._backup_thread: threading.Thread | None = None
        self._backup_running: bool = False
        self._start_backup_scheduler()

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

    def __getattr__(self, name: str):
        return getattr(self._active(), name)


# Instantiate a global db object for ease of use
db = DatabaseRouter()
