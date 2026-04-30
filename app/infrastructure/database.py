import sqlite3
import os
import logging
from contextlib import contextmanager
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

from config import DB_PATH
from .migrations import run_migrations

class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            # Mengaktifkan Write-Ahead Logging untuk konkurensi maksimal (penting karena diakses multiprocess)
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabel untuk menyimpan order yang masih berjalan
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    pair TEXT PRIMARY KEY,
                    entry_price REAL,
                    amount_crypto REAL,
                    amount_idr REAL,
                    status TEXT,
                    entry_order_id TEXT,
                    exit_order_id TEXT,
                    timestamp DATETIME,
                    dry_run BOOLEAN,
                    target_tp_pct REAL DEFAULT 0.05,
                    target_sl_pct REAL DEFAULT -0.02,
                    trailing_sl_price REAL DEFAULT 0.0,
                    highest_price REAL DEFAULT 0.0,
                    reason_backup TEXT
                )
            ''')
            
            # Tabel untuk Market Depth (Order Book)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS market_depth (
                    pair TEXT,
                    price REAL,
                    vol REAL,
                    is_bid INTEGER
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_depth_pair ON market_depth(pair)')
            
            # Tabel untuk histori transaksi yang sudah exit (Take Profit, Stop Loss, Cancel)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    amount_crypto REAL,
                    amount_idr REAL,
                    pnl_idr REAL,
                    pnl_pct REAL,
                    reason TEXT,
                    exec_type TEXT DEFAULT 'MAKER',
                    duration_seconds INTEGER,
                    dry_run BOOLEAN,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk menyimpan history harga koin
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ticker_history (
                    pair TEXT PRIMARY KEY,
                    history_json TEXT,
                    prediction_prob REAL DEFAULT 0.0,
                    trend TEXT DEFAULT 'N/A',
                    signal TEXT DEFAULT 'HOLD',
                    ch_5m REAL DEFAULT 0.0,
                    ch_15m REAL DEFAULT 0.0,
                    ch_1h REAL DEFAULT 0.0,
                    volatility REAL DEFAULT 0.0,
                    score REAL DEFAULT 0.0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk status bot (daily counters, last day reset, dll)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk audit log semua order (Buy/Sell/Cancel)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair TEXT,
                    side TEXT,
                    price REAL,
                    amount REAL,
                    status TEXT,
                    response_json TEXT,
                    error_msg TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk System Events (AI reasoning, Oracle decisions, Risk alerts)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair TEXT,
                    type TEXT,
                    message TEXT,
                    level TEXT DEFAULT 'INFO',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_pair ON system_events(pair)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_time ON system_events(created_at)')

            # Tabel untuk daftar koin yang diblokir
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blacklist (
                    pair TEXT PRIMARY KEY,
                    reason TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk Autentikasi Pengguna (SaaS Ready)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password_hash TEXT,
                    api_key TEXT,
                    api_secret TEXT,
                    role TEXT DEFAULT 'USER',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk sector history (Project Predator)
            cursor.execute('''CREATE TABLE IF NOT EXISTS sector_history (
                sector TEXT,
                sentiment_score REAL,
                momentum_score REAL DEFAULT 0.0,
                leader_pair TEXT DEFAULT 'N/A',
                leader_change REAL DEFAULT 0.0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')

            # 7. Shadow Orders / Jaring (Project Predator)
            cursor.execute('''CREATE TABLE IF NOT EXISTS shadow_orders (
                pair TEXT PRIMARY KEY,
                order_id TEXT,
                price REAL,
                amount REAL,
                side TEXT DEFAULT 'BUY',
                status TEXT DEFAULT 'OPEN',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')

            # 8. Trade Analytics / DNA (Project Evolution)
            cursor.execute('''CREATE TABLE IF NOT EXISTS trade_analytics (
                pair TEXT,
                entry_timestamp DATETIME,
                ai_prob REAL,
                whale_boost REAL,
                sector_sentiment REAL,
                imbalance REAL,
                volatility REAL,
                eta_minutes INTEGER DEFAULT 15,
                llm_verdict TEXT DEFAULT 'HOLD',
                llm_confidence REAL DEFAULT 0.0,
                llm_reason TEXT DEFAULT '',
                dry_run BOOLEAN DEFAULT 1,
                pnl_idr REAL,
                status TEXT DEFAULT 'OPEN'
            )''')
            
            # Migration: Tambahkan kolom eta_minutes jika belum ada
            try:
                cursor.execute("ALTER TABLE trade_analytics ADD COLUMN eta_minutes INTEGER DEFAULT 15")
            except: pass
            try:
                cursor.execute("ALTER TABLE trade_analytics ADD COLUMN llm_verdict TEXT DEFAULT 'HOLD'")
            except: pass
            try:
                cursor.execute("ALTER TABLE trade_analytics ADD COLUMN llm_confidence REAL DEFAULT 0.0")
            except: pass
            try:
                cursor.execute("ALTER TABLE trade_analytics ADD COLUMN llm_reason TEXT DEFAULT ''")
            except: pass
            try:
                cursor.execute("ALTER TABLE trade_analytics ADD COLUMN dry_run BOOLEAN DEFAULT 1")
            except: pass

            # 9. Dynamic Weights (Project Evolution)
            cursor.execute('''CREATE TABLE IF NOT EXISTS dynamic_weights (
                feature_name TEXT PRIMARY KEY,
                weight_value REAL DEFAULT 1.0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Initialize weights if empty
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('ai_prob', 1.0)")
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('whale_boost', 1.0)")
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('sector_sentiment', 1.0)")
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('imbalance', 1.0)")
            
            # Committing is handled by the context manager automatically
            
        # ── Jalankan Sistem Migrasi Terpisah ──
        run_migrations(self.db_path)
    # ── METODE UNTUK POSITIONS ──
    
    def get_open_positions(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            rows = cursor.fetchall()
            return {row['pair']: dict(row) for row in rows}

    def get_position(self, pair):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE pair = ?", (pair,))
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
                    trailing_sl_price, highest_price, reason_backup
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0.0), COALESCE(?, 0.0), ?)
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
                    reason_backup=excluded.reason_backup
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
                data.get('reason_backup')
            ))

    def delete_position(self, pair):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions WHERE pair = ?", (pair,))

    def update_position_status(self, pair, status, exit_order_id=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if exit_order_id is not None:
                cursor.execute("UPDATE positions SET status = ?, exit_order_id = ? WHERE pair = ?", (status, exit_order_id, pair))
            else:
                cursor.execute("UPDATE positions SET status = ? WHERE pair = ?", (status, pair))

    # ── METODE UNTUK TRADE HISTORY ──
    
    def insert_trade_history(self, pair, entry_price, exit_price, amount_crypto, amount_idr, pnl_idr, pnl_pct, reason, duration_seconds, dry_run, exec_type='MAKER', trade_id=None, is_manual=0, created_at=None, exit_order_id=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            dry_run_int = 1 if dry_run else 0
            
            sql = '''
                INSERT OR IGNORE INTO trade_history (
                    pair, entry_price, exit_price, amount_crypto, amount_idr,
                    pnl_idr, pnl_pct, reason, exec_type, duration_seconds, dry_run, trade_id, is_manual, exit_order_id
                '''
            params = [pair, entry_price, exit_price, amount_crypto, amount_idr, pnl_idr, pnl_pct, reason, exec_type, duration_seconds, dry_run_int, trade_id, is_manual, exit_order_id]
            
            if created_at:
                sql += ", created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                params.append(created_at)
            else:
                sql += ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            
            try:
                cursor.execute(sql, tuple(params))
            except Exception as e:
                # Fallback untuk DB lama yang belum punya kolom trade_id/is_manual/exit_order_id.
                if "no column named trade_id" in str(e).lower():
                    legacy_sql = '''
                        INSERT OR IGNORE INTO trade_history (
                            pair, entry_price, exit_price, amount_crypto, amount_idr,
                            pnl_idr, pnl_pct, reason, duration_seconds, dry_run
                    '''
                    legacy_params = [
                        pair, entry_price, exit_price, amount_crypto, amount_idr,
                        pnl_idr, pnl_pct, reason, duration_seconds, dry_run_int,
                    ]

                    if created_at:
                        legacy_sql += ", created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        legacy_params.append(created_at)
                    else:
                        legacy_sql += ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"

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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED')
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ''', (
                    pair, entry_price, exit_price, amount_idr/entry_price, amount_idr,
                    pnl_idr, pnl_pct, 'BACKTEST', duration_seconds
                ))
        except Exception as e:
            print(f"DB Error (insert_backtest_data): {e}")

    def get_daily_stats(self, date_str=None):
        """Mengambil rekap performa untuk 24 jam terakhir. Default = last 24h."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Default: Rolling 24 jam terakhir (lebih berguna untuk crypto 24/7)
            date_filter = "created_at >= datetime('now', '-24 hours')"
            
            if date_str:
                # Jika spesifik tanggal, gunakan filter tanggal kalender
                date_filter = f"date(created_at, 'localtime') = date('{date_str}')"
                
            query = f'''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl_idr <= 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl_idr) as total_pnl_idr,
                    AVG(pnl_pct) as avg_pnl_pct
                FROM trade_history
                WHERE {date_filter} AND dry_run = ?
            '''
            cursor.execute(query, (0 if not date_str else 1,)) # Default to Live for daily report
            row = cursor.fetchone()
            
            stats = dict(row) if row else {}
            # Handle Nulls
            stats['total_trades'] = stats.get('total_trades') or 0
            stats['win_count'] = stats.get('win_count') or 0
            stats['loss_count'] = stats.get('loss_count') or 0
            stats['total_pnl_idr'] = stats.get('total_pnl_idr') or 0.0
            stats['avg_pnl_pct'] = stats.get('avg_pnl_pct') or 0.0
            
            return stats

    def get_recent_trades(self, limit=10):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_history ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    # ── METODE UNTUK TICKER HISTORY ──

    def save_ticker_history(self, pair, history_list, prediction_prob=0.0, trend='N/A', signal='HOLD',
                            ch_5m=0.0, ch_15m=0.0, ch_1h=0.0, volatility=0.0, score=0.0):
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO ticker_history (pair, history_json, prediction_prob, trend, signal,
                                          ch_5m, ch_15m, ch_1h, volatility, score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
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
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
            ''', (key, str(value)))

    def get_state(self, key, default=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else default

    def get_state_with_time(self, key):
        """Ambil value dan timestamp updated_at."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value, updated_at FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return (row['value'], row['updated_at']) if row else (None, None)

    # ── METODE UNTUK AUDIT LOG & BLACKLIST ──

    def log_order(self, pair, side, price, amount, status, response=None, error=None):
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            resp_str = json.dumps(response) if response else None
            err_str = str(error) if error else None
            cursor.execute('''
                INSERT INTO order_logs (pair, side, price, amount, status, response_json, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (pair, side, price, amount, status, resp_str, err_str))

    def log_system_event(self, pair, event_type, message, level='INFO'):
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO system_events (pair, type, message, level)
                VALUES (?, ?, ?, ?)
            ''', (pair, event_type, message, level))

    def add_to_blacklist(self, pair, reason="Manual"):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO blacklist (pair, reason) VALUES (?, ?)", (pair, reason))

    def get_blacklist(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT pair FROM blacklist")
            return [row['pair'] for row in cursor.fetchall()]

    def is_blacklisted(self, pair):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM blacklist WHERE pair = ?", (pair,))
            return cursor.fetchone() is not None

    # ── Sector & Shadow Order Methods (Predator) ─────────────────────────

    def save_sector_sentiment(self, sector, score, momentum=0.0, leader='N/A', leader_ch=0.0):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO sector_history (sector, sentiment_score, momentum_score, leader_pair, leader_change) VALUES (?, ?, ?, ?, ?)",
                (sector, score, momentum, leader, leader_ch)
            )

    def get_latest_sector_sentiment(self, sector):
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT sentiment_score FROM sector_history WHERE sector = ? ORDER BY timestamp DESC LIMIT 1",
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
                "INSERT OR REPLACE INTO shadow_orders (pair, order_id, price, amount, status) VALUES (?, ?, ?, ?, 'OPEN')",
                (pair, order_id, price, amount)
            )

    def get_all_shadow_orders(self):
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM shadow_orders WHERE status = 'OPEN'")
            return [dict(row) for row in cursor.fetchall()]

    def delete_shadow_order(self, pair):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM shadow_orders WHERE pair = ?", (pair,))

    # ── Learning & Analytics Methods (Evolution) ─────────────────────────

    def save_trade_dna(self, pair, dna_data):
        try:
            with self.get_connection() as conn:
                conn.execute(
                    """INSERT INTO trade_analytics 
                    (pair, entry_timestamp, ai_prob, whale_boost, sector_sentiment, imbalance, volatility, rsi, ema_dist, eta_minutes, llm_verdict, llm_confidence, llm_reason, dry_run) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pair, datetime.now().isoformat(), dna_data['ai_prob'], dna_data['whale_boost'], 
                     dna_data['sector_sentiment'], dna_data['imbalance'], dna_data['volatility'],
                     dna_data.get('rsi'), dna_data.get('ema_dist'), dna_data.get('eta_minutes', 15),
                     dna_data.get('llm_verdict', 'HOLD'), dna_data.get('llm_confidence', 0.0),
                     dna_data.get('llm_reason', ''), 1 if dna_data.get('dry_run', True) else 0)
                )
        except Exception as e:
            print(f"DB Error (save_trade_dna): {e}")

    def get_quant_audit_report(self, window_hours=24, dry_run=1):
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
                    WHERE created_at >= datetime('now', ?) AND dry_run = ?
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
                    WHERE created_at >= datetime('now', ?) AND dry_run = ?
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
                      AND entry_timestamp >= datetime('now', ?)
                      AND dry_run = ?
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
                    WHERE created_at >= datetime('now', ?)
                      AND dry_run = ?
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

    def update_trade_outcome(self, pair, pnl_idr):
        try:
            with self.get_connection() as conn:
                # Update PnL untuk entri terbaru yang masih OPEN
                conn.execute(
                    "UPDATE trade_analytics SET pnl_idr = ?, status = 'CLOSED' WHERE pair = ? AND status = 'OPEN'",
                    (pnl_idr, pair)
                )
        except Exception as e:
            print(f"DB Error (update_trade_outcome): {e}")

    def get_latest_trade_dna(self, pair):
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM trade_analytics WHERE pair = ? AND status = 'CLOSED' ORDER BY entry_timestamp DESC LIMIT 1",
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
                    ORDER BY entry_timestamp DESC LIMIT ?
                """
                cursor = conn.execute(query, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"DB Error (get_all_closed_dnas): {e}")
            return []

    def update_dynamic_weight(self, feature, value):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE dynamic_weights SET weight_value = ?, updated_at = CURRENT_TIMESTAMP WHERE feature_name = ?",
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
                    VALUES (?, ?, CURRENT_TIMESTAMP)
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
                conn.execute("DELETE FROM market_depth WHERE pair = ?", (pair,))
                # Simpan bids (is_bid=1)
                for b in bids:
                    conn.execute("INSERT INTO market_depth (pair, price, vol, is_bid) VALUES (?, ?, ?, 1)",
                               (pair, b['price'], b['vol']))
                # Simpan asks (is_bid=0)
                for a in asks:
                    conn.execute("INSERT INTO market_depth (pair, price, vol, is_bid) VALUES (?, ?, ?, 0)",
                               (pair, a['price'], a['vol']))
        except Exception: pass

    def get_market_depth(self, pair):
        """Ambil Top 10 Bids & Asks dari DB."""
        try:
            with self.get_connection() as conn:
                bids = conn.execute("SELECT price, vol FROM market_depth WHERE pair = ? AND is_bid = 1 ORDER BY price DESC", (pair,)).fetchall()
                asks = conn.execute("SELECT price, vol FROM market_depth WHERE pair = ? AND is_bid = 0 ORDER BY price ASC", (pair,)).fetchall()
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
                    "DELETE FROM system_events WHERE created_at < datetime('now', ?)",
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
                    "INSERT INTO users (username, password_hash, api_key, api_secret) VALUES (?, ?, ?, ?)",
                    (username, pw_hash, api_key, api_secret)
                )
            return True
        except Exception as e:
            logging.error(f"[DB] Error creating user: {e}")
            return False

    def get_user(self, username):
        """Ambil data user berdasarkan username."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
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
                    "UPDATE users SET api_key = ?, api_secret = ? WHERE username = ?",
                    (api_key, api_secret, username)
                )
            return True
        except Exception as e:
            logging.error(f"[DB] Error updating keys: {e}")
            return False

# Instantiate a global db object for ease of use
db = Database()
