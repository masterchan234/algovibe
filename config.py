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
#INITIAL_CAPITAL        = 10000000
INITIAL_CAPITAL        = 100000000
MIN_BALANCE_THRESHOLD  = 10000
DAILY_LOSS_LIMIT_PCT   = 0.03
MAX_TRADES_PER_DAY     = 1000
MAX_CONSECUTIVE_LOSSES = 3
MAX_TRACKED_PAIRS      = 150
MIN_VOLUME_FILTER      = 10000000  # IDR per 24h

# ==========================================
# --- [Risk & Sizing] ---
MAX_TRADES_PER_DAY = 9999       # [UNLIMITED] Scalp non-stop 24/7
CAPITAL_PER_TRADE_PCT = 0.25    # [AGGRESSIVE] Gunakan 25% saldo per koin (Fixed for 90k Balance)
MIN_BALANCE_THRESHOLD = 10000   # Jangan trading jika saldo < 10rb
MIN_TRADE_IDR = int(os.getenv("MIN_TRADE_IDR", 10500)) # Minimum order Indodax (10k + buffer)

# --- [Scalping Targets] ---
TP_PCT = 0.012                  # [SCALP] Target 1.2% untuk rotasi cepat
SL_PCT = -0.015                 # [SCALP] Stop Loss -1.5%
TRAILING_STOP_ACTIVATION_PCT = 0.012 # Aktifkan trailing setelah +1.2%
TRAILING_STOP_DISTANCE_PCT = 0.005   # Jarak trailing 0.5%
MAX_HOLD_MINUTES = 15           # [SCALP] Rotasi cepat, jangan jadi bagholder

MIN_NET_PROFIT_IDR = 500  # [DRY RUN TEST] Dilonggarkan dari 2500 → 500
PENDING_BUY_TIMEOUT_MINUTES = 3
PENDING_BUY_MAX_SPREAD_PCT = 0.002

# --- Filter Criteria (Mean Reversion) ---
MAX_ALLOWED_SPREAD_PCT = 0.010 # [DRY RUN TEST] Dilonggarkan dari 0.005 → 0.010
MIN_TRACKING_VOLATILITY = 0.3  # [DRY RUN TEST] Dilonggarkan dari 0.8 → 0.3
# Imbalance sudah tidak dipakai sebagai primary trigger di backtester, tapi berguna untuk oracle
STRONG_IMBALANCE_RATIO  = 1.4  

# --- AI & Probability ---
AI_CONFIDENCE_THRESHOLD = 0.25 # [PRO SCALP] Lebih responsif terhadap momentum mikro
AI_SL_OVERRIDE_THRESHOLD = 0.70 # Tahan SL jika probabilitas bounce >= 70%
AI_MIN_HISTORY_POINTS   = 30   

# --- [Senior Strategy: Averaging Down] ---
DCA_ENABLED = True
DCA_MAX_TIERS = 2              # Maksimal 2x tambah muatan
DCA_STEP_PCT = -0.035          # Tambah muatan setiap turun 3.5%
DCA_MULTIPLIER = 1.0           # Jumlah muatan sama dengan sebelumnya (1:1)
DCA_MIN_AI_CONFIDENCE = 0.40   # Hanya tambah muatan jika AI masih optimis > 40%# Minimal data points sebelum AI aktif

# --- Execution Offsets ---
BUY_MAKER_OFFSET = 0.002 # (Tidak terpakai di limit order EMA-20)
SELL_MAKER_OFFSET = 0.0002 # Antri jual 0.02% di atas market (TP)
SELL_TAKER_OFFSET = 0.0080 # [CRITICAL FIX] Jual 0.8% di bawah market (was 5.0%!)

# --- Deadman Switch (Safety) ---
DEADMAN_SWITCH_ENABLED = True
DEADMAN_INTERVAL_SEC = 30
DEADMAN_TIMEOUT_MS = 120000 # 120 seconds

# --- Indodax Pro Fee Structure (Image Reference) ---
# [Senior Audit] Fee diupdate ke angka konservatif (Standard Taker 0.5% + Tax + CFX)
# Jika user memiliki VIP level, bot akan otomatis menyesuaikan lewat PrivateWS fillInformation.
FEE_BUY_MAKER  = 0.001111  # 0.1111% (0.1% Service + 0.0111% CFX)
FEE_BUY_TAKER  = 0.005111  # 0.5111% (0.5% Service + 0.0111% CFX)
FEE_SELL_MAKER = 0.003211  # 0.3211% (0.1% Service + 0.21% Tax + 0.0111% CFX)
FEE_SELL_TAKER = 0.007211  # 0.7211% (0.5% Service + 0.21% Tax + 0.0111% CFX)
FEE_SLIPPAGE_BUFFER = 0.0005 # 0.05% extra buffer for market impact

# --- Predator Strategy Parameters ---
WHALE_THRESHOLD_IDR = 300_000_000 # Minimal Rp 300 Juta untuk disebut "Paus"
JARING_ALLOCATION_PCT = 0.05      # Sisihkan 5% modal untuk jaring (flash crash)
JARING_DEPTH_PCT = -0.07          # Pasang jaring 7% di bawah harga pasar

# --- Sector Mapping (Indodax Pro) ---
SECTOR_MAP = {
    # MEME
    "dogeidr": "MEME", "pepeidr": "MEME", "shibidr": "MEME", "flokiidr": "MEME", "bonkidr": "MEME",
    # AI & DePIN
    "fetidr": "AI", "agixidr": "AI", "rndridr": "AI", "oceanidr": "AI", "arkhamidr": "AI", "grtidr": "AI", "ioidr": "AI",
    # BLUECHIP / L1
    "btcidr": "L1", "ethidr": "L1", "solidr": "L1", "bnbidr": "L1", "xrpidr": "L1", "adadr": "L1", "dotidr": "L1", "avaxidr": "L1", "nearidr": "L1",
    # L2 & SCALING
    "maticidr": "L2", "opidr": "L2", "arbidr": "L2", "strkidr": "L2", "mantaidr": "L2", "celoidr": "L2",
    # RWA & DEFI
    "ondoidr": "RWA", "linkidr": "DEFI", "uniidr": "DEFI", "aaveidr": "DEFI", "mkrp_idr": "RWA", "chzidr": "RWA",
    # STORAGE & INFRA
    "filidr": "STORAGE", "aridr": "STORAGE", "storjidr": "STORAGE", "ldoidr": "DEFI"
}

# Set ke False untuk eksekusi order nyata ke Indodax.
# Saat True, semua order hanya disimulasikan — tidak ada uang yang bergerak.
DRY_RUN =   True

# Longgarkan semua gate entry saat DRY_RUN untuk testing dashboard.
# Set ke False sebelum live trading!
DRY_RUN_LOOSE_GATES = False

# --- [Senior Audit] Blacklist Koin "Busuk" & Stablecoins ---
# Koin yang ada di daftar ini akan otomatis diabaikan oleh radar bot.
BLACKLIST_COINS = [
    "usdtidr", "usdcidr", "paxgidr",  # Stablecoins (No profit in scalping)
    "pxgidr", "vexidr", "dgbidr",     # Low liquidity / "Zombie" coins
    "bfidr", "sumoidr",               # Common pump & dump traps
    # Backtest loser list (April 2026) — performa sangat buruk, sementara dihindari
    "jellyjellyidr", "plpaidr", "clvidr", "dlcidr", "metaidr"
]

# --- [Anti-Shitcoin] Filter Harga Rendah ---
# Mengabaikan koin seharga "perak" yang sering memberikan sinyal palsu (misal 1 ke 2 = 100%)
MIN_PRICE_FILTER = 10 
