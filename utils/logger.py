import logging
import os
from datetime import datetime
from engines.telegram_bot import send_telegram_message

# Setup logging ke file dan konsol
from config import LOG_FILE
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("Predator")

def log_message(message: str, telegram: bool = False):
    """
    Fungsi universal untuk mencatat log ke konsol/file dan opsional ke Telegram.
    """
    clean_msg = message.replace('*', '').replace('_', '').replace('`', '')
    logger.info(clean_msg)
    
    if telegram:
        send_telegram_message(message)

def run_performance_report(log_fn=None):
    """
    Placeholder untuk fungsi laporan performa harian.
    Dapat diisi dengan logika rekap PnL dari database.
    """
    from engines.database import db
    stats = db.get_daily_stats()
    report = (
        f"📊 *DAILY PERFORMANCE REPORT* 📊\n"
        f"Trades: {stats['total_trades']}\n"
        f"Win/Loss: {stats['win_count']}/{stats['loss_count']}\n"
        f"Total PnL: Rp {stats['total_pnl_idr']:,.2f}\n"
        f"Avg PnL: {stats['avg_pnl_pct']:.2f}%"
    )
    if log_fn:
        log_fn(report, telegram=True)
    else:
        log_message(report, telegram=True)
