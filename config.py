import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# 1. CORE PATHS & FILES
# ==========================================
BASE_PATH    = os.getenv("APP_BASE_PATH", os.path.dirname(os.path.abspath(__file__)))
DATA_PATH    = os.getenv("APP_DATA_PATH", os.path.join(BASE_PATH, "data"))
RAM_PATH     = os.getenv("APP_RAM_PATH", "/mnt/ramdisk/algotrade")

LOG_FILE        = f"{DATA_PATH}/logs/scalper.log"
POSITION_FILE   = f"{RAM_PATH}/open_positions.json" # DEPRECATED
EXIT_LOG        = f"{DATA_PATH}/indodax_exit_log.json" # DEPRECATED
DAILY_REPORT    = f"{DATA_PATH}/daily_performance.json" # DEPRECATED
EXECUTION_LOG   = f"{DATA_PATH}/indodax_execution_log.json" # DEPRECATED
DB_PATH         = f"{DATA_PATH}/algotrade.db"

# ==========================================
# 2. TRADING RULES & SIZING
# ==========================================
INITIAL_CAPITAL        = 10000000
MIN_BALANCE_THRESHOLD  = 10000
DAILY_LOSS_LIMIT_PCT   = 0.03
MAX_TRADES_PER_DAY     = 1000
MAX_CONSECUTIVE_LOSSES = 3
MAX_TRACKED_PAIRS      = 150
MIN_VOLUME_FILTER      = 10000000  # IDR per 24h
MIN_TRADE_IDR          = 10000  # Min IDR for Indodax Pro

# ==========================================
# 3. EXECUTION SETTINGS (TP/SL/AUTO-CANCEL)
# ==========================================
TP_PCT = 0.015   # +1.5% Target Profit (Baseline Minimum)
SL_PCT = -0.008  # -0.8% Stop Loss
MIN_NET_PROFIT_IDR = 300 # Minimum target keuntungan bersih (Rupiah) per trade
PENDING_BUY_TIMEOUT_MINUTES = 3
PENDING_BUY_MAX_SPREAD_PCT = 0.002

# ==========================================
# 4. TAX & FEE STRUCTURE (Indodax 2026 / PMK 68/2022)
# ==========================================
MAKER_FEE     = 0.0010   # 0.10% Maker fee Indodax Pro
PPH_RATE      = 0.0010   # 0.10% PPh Pasal 22 (tiap sisi)
PPN_RATE      = 0.0011   # 0.11% PPN (tiap sisi)
FEE_PER_SIDE  = MAKER_FEE + PPH_RATE + PPN_RATE  # 0.31%
FEE_ROUNDTRIP = FEE_PER_SIDE * 2                  # 0.62%

# ==========================================
# 5. DRY RUN MODE
# ==========================================
# Set ke False untuk eksekusi order nyata ke Indodax.
# Saat True, semua order hanya disimulasikan — tidak ada uang yang bergerak.
DRY_RUN = True
