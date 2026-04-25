import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# 1. CORE PATHS & FILES
# ==========================================
BASE_PATH    = os.getenv("APP_BASE_PATH", os.path.dirname(os.path.abspath(__file__)))
DATA_PATH    = "data"
LOG_FILE     = f"{DATA_PATH}/logs/scalper.log"
MODEL_PATH   = "models/gemma-4-E2B-it.litertlm"
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
# 3. STRATEGY PARAMETERS
# ==========================================
TP_PCT = 0.02   # +2.0% Target Profit (Baseline Minimum)
SL_PCT = -0.012  # -1.2% Stop Loss (Lebih ketat untuk memotong kerugian lebih cepat)

# --- Trailing Stop Loss ---
TRAILING_STOP_ACTIVATION_PCT = 0.01  # Activate trailing stop when profit hits 1.0%
TRAILING_STOP_DISTANCE_PCT = 0.005   # Trail price by 0.5% once activated

MIN_NET_PROFIT_IDR = 300 # Minimum target keuntungan bersih (Rupiah) per trade
PENDING_BUY_TIMEOUT_MINUTES = 3
PENDING_BUY_MAX_SPREAD_PCT = 0.002

# --- Filter Criteria ---
MAX_ALLOWED_SPREAD_PCT = 0.005 # Max 0.5% spread untuk masuk ranking
MIN_TRACKING_VOLATILITY = 0.5  # Volatilitas minimal untuk micro-tracking
STRONG_IMBALANCE_RATIO  = 2.0  # Ratio Bid/Ask untuk sinyal tambahan

# --- AI & Probability ---
AI_CONFIDENCE_THRESHOLD = 0.60 # Beli jika probabilitas >= 60%
AI_SL_OVERRIDE_THRESHOLD = 0.70 # Tahan SL jika probabilitas bounce >= 70%
AI_MIN_HISTORY_POINTS   = 30   # Minimal data points sebelum AI aktif

# --- Execution Offsets ---
BUY_MAKER_OFFSET = 0.002 # Antri beli 0.2% di bawah pucuk harga (Pullback Entry Strategy)
SELL_MAKER_OFFSET = 0.0005 # Antri jual 0.05% di atas market (TP)
SELL_TAKER_OFFSET = 0.0500 # Jual 5% di bawah market (Instant Sell / SL)

# --- Sizing ---
CAPITAL_PER_TRADE_PCT = 0.05 # Gunakan 5% modal per trade (Prop-firm standard for scalping)

# --- Indodax Pro Fee Structure (Image Reference) ---
FEE_BUY_MAKER  = 0.001111  # 0.1111% (0.1% Service + 0.0111% CFX)
FEE_BUY_TAKER  = 0.002111  # 0.2111% (0.2% Service + 0.0111% CFX)
FEE_SELL_MAKER = 0.003211  # 0.3211% (0.1% Service + 0.21% Tax + 0.0111% CFX)
FEE_SELL_TAKER = 0.004211  # 0.4211% (0.2% Service + 0.21% Tax + 0.0111% CFX)



# --- Predator Strategy Parameters ---
WHALE_THRESHOLD_IDR = 300_000_000 # Minimal Rp 300 Juta untuk disebut "Paus"
JARING_ALLOCATION_PCT = 0.05      # Sisihkan 5% modal untuk jaring (flash crash)
JARING_DEPTH_PCT = -0.07          # Pasang jaring 7% di bawah harga pasar

# --- Sector Mapping (Indodax Pro) ---
SECTOR_MAP = {
    "dogeidr": "MEME", "pepeidr": "MEME", "shibidr": "MEME", "flokiidr": "MEME",
    "fetidr": "AI", "agixidr": "AI", "rndridr": "AI", "oceanidr": "AI",
    "btcidr": "BLUECHIP", "ethidr": "BLUECHIP", "solidr": "BLUECHIP", "bnbidr": "BLUECHIP",
    "maticidr": "L2", "opidr": "L2", "arbidr": "L2"
}

# Set ke False untuk eksekusi order nyata ke Indodax.
# Saat True, semua order hanya disimulasikan — tidak ada uang yang bergerak.
DRY_RUN = True
