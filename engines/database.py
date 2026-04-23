import sqlite3
import os
from contextlib import contextmanager

from config import DB_PATH

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
                    dry_run BOOLEAN
                )
            ''')
            
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

            # Tabel untuk daftar koin yang diblokir
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blacklist (
                    pair TEXT PRIMARY KEY,
                    reason TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabel untuk sector history (Project Predator)
            cursor.execute('''CREATE TABLE IF NOT EXISTS sector_history (
                sector TEXT,
                sentiment_score REAL,
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
                pnl_idr REAL,
                status TEXT DEFAULT 'OPEN'
            )''')

            # 9. Dynamic Weights (Project Evolution)
            cursor.execute('''CREATE TABLE IF NOT EXISTS dynamic_weights (
                feature_name TEXT PRIMARY KEY,
                weight_value REAL DEFAULT 1.0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Initialize weights if empty
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('ai_prob', 1.0)")
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('whale_boost', 1.0)")
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('sector_sentiment', 1.0)")
            cursor.execute("INSERT OR IGNORE INTO dynamic_weights (feature_name, weight_value) VALUES ('imbalance', 1.0)")
            
            # Committing is handled by the context manager automatically
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
            
            cursor.execute('''
                INSERT INTO positions (
                    pair, entry_price, amount_crypto, amount_idr, status, 
                    entry_order_id, exit_order_id, timestamp, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pair) DO UPDATE SET
                    entry_price=excluded.entry_price,
                    amount_crypto=excluded.amount_crypto,
                    amount_idr=excluded.amount_idr,
                    status=excluded.status,
                    entry_order_id=excluded.entry_order_id,
                    exit_order_id=excluded.exit_order_id,
                    timestamp=excluded.timestamp,
                    dry_run=excluded.dry_run
            ''', (
                pair,
                data.get('entry_price'),
                data.get('amount_crypto') or data.get('amount', 0), # Fallback untuk backward compatibility dict lama
                data.get('amount_idr'),
                data.get('status'),
                data.get('entry_order_id'),
                data.get('exit_order_id'),
                data.get('timestamp'),
                dry_run
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
    
    def insert_trade_history(self, pair, entry_price, exit_price, amount_crypto, amount_idr, pnl_idr, pnl_pct, reason, duration_seconds, dry_run):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            dry_run_int = 1 if dry_run else 0
            cursor.execute('''
                INSERT INTO trade_history (
                    pair, entry_price, exit_price, amount_crypto, amount_idr,
                    pnl_idr, pnl_pct, reason, duration_seconds, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pair, entry_price, exit_price, amount_crypto, amount_idr,
                pnl_idr, pnl_pct, reason, duration_seconds, dry_run_int
            ))

    def get_daily_stats(self, date_str=None):
        """Mengambil rekap performa untuk tanggal tertentu (YYYY-MM-DD). Default = hari ini."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            date_filter = "date(created_at, 'localtime') = date('now', 'localtime')"
            if date_str:
                date_filter = f"date(created_at, 'localtime') = date('{date_str}')"
                
            query = f'''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl_idr <= 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl_idr) as total_pnl_idr,
                    AVG(pnl_pct) as avg_pnl_pct
                FROM trade_history
                WHERE {date_filter}
            '''
            cursor.execute(query)
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

    def save_ticker_history(self, pair, history_list, prediction_prob=0.0, trend='N/A'):
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO ticker_history (pair, history_json, prediction_prob, trend, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(pair) DO UPDATE SET
                    history_json=excluded.history_json,
                    prediction_prob=excluded.prediction_prob,
                    trend=excluded.trend,
                    updated_at=excluded.updated_at
            ''', (pair, json.dumps(history_list), prediction_prob, trend))

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

    def save_sector_sentiment(self, sector, score):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO sector_history (sector, sentiment_score) VALUES (?, ?)",
                (sector, score)
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
                SELECT sector, sentiment_score 
                FROM sector_history s1
                WHERE timestamp = (SELECT MAX(timestamp) FROM sector_history s2 WHERE s1.sector = s2.sector)
            """)
            rows = cursor.fetchall()
            return {row['sector']: row['sentiment_score'] for row in rows}

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
                    (pair, entry_timestamp, ai_prob, whale_boost, sector_sentiment, imbalance, volatility) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (pair, datetime.now().isoformat(), dna_data['ai_prob'], dna_data['whale_boost'], 
                     dna_data['sector_sentiment'], dna_data['imbalance'], dna_data['volatility'])
                )
        except Exception as e:
            print(f"DB Error (save_trade_dna): {e}")

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

    def update_dynamic_weight(self, feature, value):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE dynamic_weights SET weight_value = ?, last_updated = CURRENT_TIMESTAMP WHERE feature_name = ?",
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

    def get_all_weights(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("SELECT feature_name, weight_value FROM dynamic_weights")
                return {row['feature_name']: row['weight_value'] for row in cursor.fetchall()}
        except Exception:
            return {}

# Instantiate a global db object for ease of use
db = Database()
