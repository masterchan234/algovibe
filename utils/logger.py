import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from app.interfaces.telegram_bot import send_telegram_message

# Setup logging ke file dan konsol
from config import LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES, ENABLE_FILE_LOGGING

file_handler = None
if ENABLE_FILE_LOGGING:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )

# --- Logger Core Setup ---
root_logger = logging.getLogger()
# Hindari duplikasi jika module di-import ulang atau basicConfig sudah dipanggil di tempat lain
if not root_logger.handlers:
    # Setup awal jika root belum punya handler
    handlers = [logging.StreamHandler()]
    if file_handler:
        handlers.append(file_handler)
        
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=handlers
    )
else:
    # Jika sudah ada handler (misal dari Flask/SocketIO), pastikan FileHandler kita masuk
    if file_handler:
        has_file_handler = any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
        if not has_file_handler:
            file_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
            root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.INFO)

# Logger khusus aplikasi
logger = logging.getLogger("Predator")
logger.propagate = True # Pastikan mengalir ke root yang sudah kita beri handler

def log_message(message: str, telegram: bool = False):
    """
    Fungsi universal untuk mencatat log ke konsol/file dan opsional ke Telegram.
    """
    logger.info(message)
    
    if telegram:
        clean_msg = message.replace('*', '').replace('_', '').replace('`', '')
        send_telegram_message(clean_msg)

def run_performance_report(log_fn=None):
    """
    Placeholder untuk fungsi laporan performa harian.
    Dapat diisi dengan logika rekap PnL dari database.
    """
    from app.infrastructure.database import db
    stats = db.get_daily_stats()
    report = (
        f"DAILY PERFORMANCE REPORT\n"
        f"Trades: {stats['total_trades']}\n"
        f"Win/Loss: {stats['win_count']}/{stats['loss_count']}\n"
        f"Total PnL: Rp {stats['total_pnl_idr']:,.2f}\n"
        f"Avg PnL: {stats['avg_pnl_pct']:.2f}%"
    )
    if log_fn:
        log_fn(report, telegram=True)
    else:
        log_message(report, telegram=True)
