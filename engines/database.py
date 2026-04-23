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

# Instantiate a global db object for ease of use
db = Database()
