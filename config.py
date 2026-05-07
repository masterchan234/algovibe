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
ENABLE_FILE_LOGGING = os.getenv("ENABLE_FILE_LOGGING", "true").lower() == "true"
MODEL_PATH   = "models/gemma-4-E2B-it.litertlm"
MODEL_CACHE_DIR = "models/cache"
EXIT_LOG        = f"{DATA_PATH}/indodax_exit_log.json" # DEPRECATED
DAILY_REPORT    = f"{DATA_PATH}/daily_performance.json" # DEPRECATED
EXECUTION_LOG   = f"{DATA_PATH}/indodax_execution_log.json" # DEPRECATED
DATABASE_URL_LIVE   = os.getenv("DATABASE_URL_LIVE", "postgresql://algovibe:secretpassword@localhost:5432/algovibe_live")
DATABASE_URL_DRYRUN = os.getenv("DATABASE_URL_DRYRUN", "postgresql://algovibe:secretpassword@localhost:5432/algovibe_dryrun")

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
MAX_OPEN_POSITIONS = 100          # [WIDE NET] Maksimal 25 posisi terbuka (dari 15 → 25)
MIN_BALANCE_THRESHOLD = 10000   # Jangan trading jika saldo < 10rb
MIN_TRADE_IDR = int(os.getenv("MIN_TRADE_IDR", 10500)) # Minimum order Indodax (10k + buffer)

# --- [Scalping Targets] ---
TP_PCT = 0.030                  # Target TP 3.0% (net profit dihitung setelah fee)
SL_PCT = -0.035                 # [SL/DCA SYNC] Dilebarkan ke -3.5% agar DCA Sniper bisa berjalan di -1.5% sebelum likuidasi
MAX_HOLD_MINUTES = 10           # [ANTI-ZOMBIE] Bunuh posisi mandek lebih cepat (dari 15 → 10 menit)

MIN_NET_PROFIT_IDR = 2000  # Filter noise: minimal 2000 IDR net profit per trade

# --- [Basket Exit: Guaranteed Profit Harvester] ---
# Trigger basket harvest segera ketika combined net PnL mencapai target ini,
# bahkan jika semua posisi masih profit (tidak perlu tunggu ada yang rugi).
# Set ke 0 untuk nonaktifkan.
BASKET_HARVEST_TARGET_IDR = 20000  # Rp 20.000 combined net profit → panen serentak

# Jika combined PnL turun lebih dari X% dari high-water-mark (peak), trigger basket exit.
# Contoh: 0.30 = jika PnL peak Rp 80k dan sekarang sudah < Rp 56k (30% drop), exit.
BASKET_HWM_PULLBACK_PCT   = 0.30   # 30% drawdown dari peak combined PnL

# Minimum portfolio net profit % (after fees) to trigger automatic harvest.
# Requires BTC/Momentum to show weakening before firing.
AUTO_BASKET_HARVEST_MIN = 0.01  # 1.0% net profit threshold for automatic harvest

# BTC 5-minute price drop % that triggers emergency basket exit (kill switch).
# Bypasses all profit-gate checks to protect capital from total market collapse.
BTC_CRASH_TRIGGER_PCT = 0.02    # 2.0% BTC drop in 5 minutes activates kill switch

# ==========================================
# MOMENTUM EQUITY PROTECTION
# ==========================================
# Daily profit floor — the IDR target that should never be given back once reached.
# Defaults to BASKET_HARVEST_TARGET_IDR so the same harvest level becomes the floor.
MOMENTUM_TARGET_PNL_IDR = BASKET_HARVEST_TARGET_IDR  # Rp 20.000 combined net profit

# Buffer above the floor that defines the "Safety Zone" range
# [MOMENTUM_TARGET_PNL_IDR, MOMENTUM_TARGET_PNL_IDR + MOMENTUM_SAFETY_BUFFER_IDR].
# When portfolio PnL drops into this range from above, momentum-gated logic activates.
MOMENTUM_SAFETY_BUFFER_IDR = 2000   # Rp 2.000 cushion for faster profit lock

# Velocity threshold (IDR / second). Positive velocity exceeding this value means
# the portfolio is recovering → enable Tight Trailing Stop instead of immediate exit.
MOMENTUM_RECOVERY_THRESHOLD = 50    # Rp 50 / sec required to classify as "IMPROVING"

# How often to capture a PnL snapshot for velocity measurement (seconds).
MOMENTUM_VELOCITY_INTERVAL_SEC = 5

# Trailing-stop tightness when inside the Safety Zone with positive (recovering) velocity.
# Overrides BASKET_HWM_PULLBACK_PCT to a much tighter value so profits are locked sooner.
MOMENTUM_TIGHT_TRAILING_PCT = 0.05  # 5% drawdown from session peak (vs default 30%)

# When the majority of open positions are losing (high correlation risk), reduce the
# safety buffer by this fraction to trigger an exit sooner.
MOMENTUM_HIGH_CORR_BUFFER_REDUCTION = 0.5  # halve the buffer under high correlation

# HARVEST_ONLY selective re-entry:
# Allows a small number of new BUY orders only for momentum setups with
# high-quality profit profile while session remains in HARVEST_ONLY.
HARVEST_MOMENTUM_REENTRY_ENABLED = True
HARVEST_MOMENTUM_MAX_NEW_POSITIONS = 1
HARVEST_MOMENTUM_MIN_CONFIDENCE = 82.0   # total gate confidence (%).
HARVEST_MOMENTUM_MIN_TQ_PROB = 0.72      # technical probability (0..1).
HARVEST_MOMENTUM_MIN_SCORE = 65.0        # ranked momentum score.
HARVEST_MOMENTUM_MIN_5M_PCT = 0.25       # short momentum (%).
HARVEST_MOMENTUM_MIN_15M_PCT = 0.60      # follow-through momentum (%).
HARVEST_MOMENTUM_MIN_TP_PCT = 0.018      # minimum expected TP (1.8%).
HARVEST_MOMENTUM_MIN_RR = 1.8            # minimum reward-to-risk ratio.

# ==========================================
# NET-ZERO SURVIVAL GUARD
# ==========================================
# Master switch — set to False to fully disable the guard without removing code.
NET_ZERO_SURVIVAL_ENABLED = True

# PnL velocity (IDR / second) below which the portfolio is considered to be DROPPING.
# Net-Zero Survival only fires when the most recent velocity sample is at or below
# this threshold (already consistent with MOMENTUM_RECOVERY_THRESHOLD sign convention).
NET_ZERO_MOMENTUM_THRESHOLD = -50.0   # IDR/s  (negative = portfolio value falling)

# Minimum number of consecutive seconds the condition must stay true before the
# emergency basket exit is executed (hysteresis: prevents one-tick false positives).
NET_ZERO_HYSTERESIS_SEC = 3.0

# Extreme velocity threshold (IDR / second) that bypasses the hysteresis entirely.
# If the portfolio is collapsing faster than this value, the survival guard fires
# immediately on the first tick — no 3-second wait.
# Example: a -7000 IDR/s momentum spike (well below -500) triggers instant exit.
NET_ZERO_IMMEDIATE_VELOCITY = -500.0  # IDR/s  (any velocity below -500 IDR/s fires instantly)

# Minimum combined Net PnL (realized + unrealized) the portfolio must reach before
# the guard even considers firing.  Prevents triggering on Rp -1 rounding noise or
# a single position dipping -0.3% while heading to its +3% TP.
# Rule of thumb: set to -(3 × BASKET_HARVEST_TARGET_IDR) so the guard only fires
# when the session is in genuine loss territory — not just temporarily underwater.
NET_ZERO_PNL_FLOOR_IDR = -20000        # IDR  (fires only when Net PnL ≤ -Rp 20.000)

# Unrealized loss as a fraction of total invested capital that must be exceeded before
# the guard arms.  Prevents spurious exits on tiny dips (e.g., 5 positions each down
# 0.1% = -0.1% basket loss should NOT trigger).
NET_ZERO_MIN_UNREALIZED_LOSS_PCT = 0.010  # 1.0% of total open capital must be unrealised loss

# Minimum number of open positions required for the basket-level guard to activate.
# A single position at -0.5% is handled by its individual SL — not by a basket nuke.
NET_ZERO_MIN_POSITIONS = 2              # need at least 2 open positions

# Seconds after the guard fires before buys can be auto-re-enabled, provided the
# combined Net PnL has recovered above NET_ZERO_PNL_FLOOR_IDR.
# Set to 0 to require manual re-enable via the dashboard (old behaviour).
NET_ZERO_AUTO_RESET_SEC = 10 #1800          # 30-minute auto-recovery cooldown
# ==========================================
PENDING_BUY_TIMEOUT_MINUTES = 3
PENDING_BUY_MAX_SPREAD_PCT = 0.002

# --- Filter Criteria (Mean Reversion) ---
MAX_ALLOWED_SPREAD_PCT = 0.010 # [DRY RUN TEST] Dilonggarkan dari 0.005 → 0.010
MIN_TRACKING_VOLATILITY = 0.3  # [DRY RUN TEST] Dilonggarkan dari 0.8 → 0.3
# Imbalance sudah tidak dipakai sebagai primary trigger di backtester, tapi berguna untuk oracle
STRONG_IMBALANCE_RATIO  = 1.4  

# --- Technical Probability ---
TECH_CONFIDENCE_THRESHOLD = 0.35 # [BALANCED] Filter noise terbawah, masih high-frequency
TECH_SL_OVERRIDE_THRESHOLD = 0.70 # Tahan SL jika probabilitas bounce >= 70%
TECH_MIN_HISTORY_POINTS   = 30   

# --- [Senior Strategy: Averaging Down] ---
DCA_ENABLED = True
DCA_MAX_TIERS = 1              # Maksimal 1x tambah muatan (dari 2) — batasi exposure
DCA_STEP_PCT = -0.015          # Tambah muatan setiap turun 1.5% (dari 2.5%) — trigger lebih cepat sebelum SL
DCA_MULTIPLIER = 1.0           # Jumlah muatan sama dengan sebelumnya (1:1)
DCA_MIN_TECH_CONFIDENCE = 0.65   # Hanya tambah muatan jika probabilitas teknikal > 65% (dari 55%) — filter lebih ketat

# --- Execution Offsets ---
BUY_MAKER_OFFSET = 0.002 # (Tidak terpakai di limit order EMA-20)
SELL_MAKER_OFFSET = 0.0002 # Antri jual 0.02% di atas market (TP)
SELL_TAKER_OFFSET = 0.0080 # [CRITICAL FIX] Jual 0.8% di bawah market (was 5.0%!)

# --- Deadman Switch (Safety) ---
DEADMAN_SWITCH_ENABLED = True
DEADMAN_INTERVAL_SEC = 30
DEADMAN_TIMEOUT_MS = 120000 # 120 seconds

# --- Indodax IDR Market PRO Mode Fee Structure ---
# Taker-taker round-trip: Buy 0.2111% + Sell 0.4211% = 0.6322%
# SMART_BREAK_EVEN: 0.6322% fee + 0.21% slippage buffer = 0.85% gross threshold.
# Any trade yielding > 0.85% gross is considered REAL_PROFIT.
FEE_BUY_MAKER  = 0.001111  # 0.1111% (0.1% Service + 0.0111% CFX)
FEE_BUY_TAKER  = 0.002111  # 0.2111% (0.2% Service + 0.0111% CFX)
FEE_SELL_MAKER = 0.003211  # 0.3211% (0.1% Service + 0.21% Tax + 0.0111% CFX)
FEE_SELL_TAKER = 0.004211  # 0.4211% (0.2% Service + 0.21% Tax + 0.0111% CFX)
FEE_SLIPPAGE_BUFFER = 0.0021 # 0.21% slippage buffer for market impact
# Taker-taker round-trip: 0.2111% buy + 0.4211% sell = 0.6322%
ROUND_TRIP_FEE_PCT = 0.006322
# Break-even threshold: ROUND_TRIP_FEE_PCT + FEE_SLIPPAGE_BUFFER = 0.85%
SMART_BREAK_EVEN_PCT = 0.0085  # 0.85% gross → REAL_PROFIT zone
# Manual exit via dashboard only allowed when Net PnL is below this threshold (< 0.1%).
MANUAL_EXIT_MAX_NET_PCT = 0.001  # 0.1%

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
    "btcidr": "L1", "ethidr": "L1", "solidr": "L1", "bnbidr": "L1", "xrpidr": "L1", "adaidr": "L1", "dotidr": "L1", "avaxidr": "L1", "nearidr": "L1",
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
            val = db_val.lower() in ("true", "1")
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
            return db_val.lower() in ("true", "1")
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
AUTO_TRAIN_INTERVAL_HOURS  = 1      # Retrain setiap 6 jam
AUTO_TRAIN_MIN_SAMPLES     = 300    # Minimal closed trades sebelum training diizinkan
AUTO_TRAIN_MIN_ACCURACY    = 0.55   # Floor accuracy — model baru tidak di-deploy jika di bawah ini
AUTO_TRAIN_MIN_DELTA       = 0.01   # Model baru harus >= model lama + 1% agar menggantikan
AUTO_TRAIN_RF_MODEL_PATH   = "models/scalper_v2.joblib"
AUTO_TRAIN_RF_MODEL_TMP    = "models/scalper_v2.joblib.tmp"
AUTO_TRAIN_LOG_PATH        = "data/logs/auto_trainer.log"
