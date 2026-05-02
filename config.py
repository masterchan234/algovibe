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
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 10 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 5))
MODEL_PATH   = "models/gemma-4-E2B-it.litertlm"
MODEL_CACHE_DIR = "models/cache"
EXIT_LOG        = f"{DATA_PATH}/indodax_exit_log.json" # DEPRECATED
DAILY_REPORT    = f"{DATA_PATH}/daily_performance.json" # DEPRECATED
EXECUTION_LOG   = f"{DATA_PATH}/indodax_execution_log.json" # DEPRECATED
DB_PATH         = f"{DATA_PATH}/algotrade.db"

# ==========================================
# 2. TRADING RULES & SIZING
# ==========================================
#INITIAL_CAPITAL        = 10000000
INITIAL_CAPITAL        = 100000000
DAILY_LOSS_LIMIT_PCT   = 0.03
MAX_CONSECUTIVE_LOSSES = 3
MAX_TRACKED_PAIRS      = 200
MIN_VOLUME_FILTER      = 10000000  # IDR per 24h (Global Minimum)
LIQUIDITY_MULTIPLIER   = 50        # [DYNAMIC] Volume 24h harus minimal 50x besar modal per trade

# ==========================================
# --- [Risk & Sizing] ---
MAX_TRADES_PER_DAY = 9999       # [UNLIMITED] Scalp non-stop 24/7
CAPITAL_PER_TRADE_PCT = 0.04    # [DYNAMIC BASE] Dasar 4% per koin → lebih banyak posisi dari modal sama
MAX_OPEN_POSITIONS = 25          # [WIDE NET] Maksimal 25 posisi terbuka (dari 15 → 25)
MIN_BALANCE_THRESHOLD = 10000   # Jangan trading jika saldo < 10rb
MIN_TRADE_IDR = int(os.getenv("MIN_TRADE_IDR", 10500)) # Minimum order Indodax (10k + buffer)

# --- [Scalping Targets] ---
TP_PCT = 0.030                  # [TRAILING DOMINANT] Fallback TP 3.0% — biarkan trailing stop yang lock profit
SL_PCT = -0.010                 # [KETAT] Stop Loss -1.0% (dari -1.5%) → kurangi avg loss ~37%
TRAILING_STOP_ACTIVATION_PCT = 0.008 # Aktifkan trailing lebih cepat: +0.8% (dari +1.2%)
TRAILING_STOP_DISTANCE_PCT = 0.004   # Jarak trailing lebih ketat: 0.4% (dari 0.5%) → lock profit
MAX_HOLD_MINUTES = 10           # [ANTI-ZOMBIE] Bunuh posisi mandek lebih cepat (dari 15 → 10 menit)

MIN_NET_PROFIT_IDR = 2000  # Filter noise: minimal 2000 IDR net profit per trade
PENDING_BUY_TIMEOUT_MINUTES = 3
PENDING_BUY_MAX_SPREAD_PCT = 0.002

# --- Filter Criteria (Mean Reversion) ---
MAX_ALLOWED_SPREAD_PCT = 0.010 # [DRY RUN TEST] Dilonggarkan dari 0.005 → 0.010
MIN_TRACKING_VOLATILITY = 0.3  # [DRY RUN TEST] Dilonggarkan dari 0.8 → 0.3
# Imbalance sudah tidak dipakai sebagai primary trigger di backtester, tapi berguna untuk oracle
STRONG_IMBALANCE_RATIO  = 1.4  

# --- AI & Probability ---
AI_CONFIDENCE_THRESHOLD = 0.35 # [BALANCED] Filter noise terbawah, masih high-frequency
AI_SL_OVERRIDE_THRESHOLD = 0.70 # Tahan SL jika probabilitas bounce >= 70%
AI_MIN_HISTORY_POINTS   = 30   

# --- [Senior Strategy: Averaging Down] ---
DCA_ENABLED = True
DCA_MAX_TIERS = 2              # Maksimal 2x tambah muatan
DCA_STEP_PCT = -0.025          # Tambah muatan setiap turun 2.5% (dari 3.5%) → DCA lebih cepat
DCA_MULTIPLIER = 1.0           # Jumlah muatan sama dengan sebelumnya (1:1)
DCA_MIN_AI_CONFIDENCE = 0.35   # Hanya tambah muatan jika AI masih optimis > 35% (dari 40%)# Minimal data points sebelum AI aktif

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

# Set ke True untuk keamanan (Safety First). 
# Bot akan selalu mulai di mode simulasi kecuali diubah lewat dashboard ter-otentikasi.
_DRY_RUN_INTERNAL = True 
_DRY_RUN_CACHE = {"value": None, "last_check": 0}

def get_dynamic_dry_run():
    import time
    now = time.time()
    # Cache for 2 seconds to avoid slamming SQLite on high-freq loops
    if _DRY_RUN_CACHE["value"] is not None and (now - _DRY_RUN_CACHE["last_check"]) < 2:
        return _DRY_RUN_CACHE["value"]

    try:
        from app.infrastructure.database import db
        db_val = db.get_state("dry_run")
        if db_val is not None:
            val = db_val.lower() == "true"
            _DRY_RUN_CACHE["value"] = val
            _DRY_RUN_CACHE["last_check"] = now
            return val
    except Exception:
        pass
    
    return _DRY_RUN_INTERNAL

def get_dynamic_loose_gates():
    try:
        from app.infrastructure.database import db
        db_val = db.get_state("dry_run_loose_gates")
        if db_val is not None:
            return db_val.lower() == "true"
    except Exception:
        pass
    return False # Default

def clear_dry_run_cache():
    _DRY_RUN_CACHE["value"] = None
    _DRY_RUN_CACHE["last_check"] = 0

def __getattr__(name):
    if name == "DRY_RUN":
        return get_dynamic_dry_run()
    if name == "DRY_RUN_LOOSE_GATES":
        return get_dynamic_loose_gates()
    raise AttributeError(f"module {__name__} has no attribute {name}")

# --- [Senior Audit] Blacklist Koin "Busuk" & Stablecoins (Managed via Database) ---
# Default stablecoins akan diinisialisasi ke database saat startup.
# Untuk menambah/mengurangi blacklist, gunakan db.add_to_blacklist() atau dashboard.
# Bot akan membaca dari tabel blacklist (bukan hardcoded list lagi).
DEFAULT_BLACKLIST_COINS = [
    "usdtidr", "usdcidr", "paxgidr",  # Stablecoins (ESSENTIAL - No profit in scalping)
]

# --- [Anti-Shitcoin] Filter Harga Rendah ---
# Mengabaikan koin seharga "perak" yang sering memberikan sinyal palsu (misal 1 ke 2 = 100%)
MIN_PRICE_FILTER = 10

# ==========================================
# AUTO-TRAINER (Background Self-Training)
# ==========================================
AUTO_TRAIN_ENABLED         = True
AUTO_TRAIN_INTERVAL_HOURS  = 6      # Retrain setiap 6 jam
AUTO_TRAIN_MIN_SAMPLES     = 300    # Minimal closed trades sebelum training diizinkan
AUTO_TRAIN_MIN_ACCURACY    = 0.55   # Floor accuracy — model baru tidak di-deploy jika di bawah ini
AUTO_TRAIN_MIN_DELTA       = 0.01   # Model baru harus >= model lama + 1% agar menggantikan
AUTO_TRAIN_RF_MODEL_PATH   = "models/scalper_v2.joblib"
AUTO_TRAIN_RF_MODEL_TMP    = "models/scalper_v2.joblib.tmp"
AUTO_TRAIN_LOG_PATH        = "data/logs/auto_trainer.log"
