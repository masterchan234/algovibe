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
# Master switch: enforce pure technical decision path.
# When enabled, any LLM-based analysis/veto/insight pipeline is bypassed.
_TECHNICAL_ONLY_INTERNAL = os.getenv("TECHNICAL_ONLY_MODE", "true").lower() in ("1", "true", "yes", "on")

#INITIAL_CAPITAL        = 10000000
INITIAL_CAPITAL        = 100000000
DAILY_LOSS_LIMIT_PCT   = 0.03
MAX_CONSECUTIVE_LOSSES = 3
MAX_TRACKED_PAIRS      = 200
MIN_VOLUME_FILTER      = 10000000  # IDR per 24h (Global Minimum)
LIQUIDITY_MULTIPLIER   = 8.0   # [Agresif] Memungkinkan trading di koin dengan volume sedang 15x besar modal per trade

# ==========================================
# --- [Risk & Sizing] ---
MAX_TRADES_PER_DAY = 9999       # [UNLIMITED] Scalp non-stop 24/7
CAPITAL_PER_TRADE_PCT = 0.12    # Modal 100k: default sizing ~12k/trade (tetap tunduk pada MIN_TRADE_IDR)
MAX_OPEN_POSITIONS = 9999          # Modal 100k: batasi eksposur agar tidak over-fragmented
MIN_BALANCE_THRESHOLD = 12000   # Jangan trading jika saldo < 12rb
MIN_TRADE_IDR = int(os.getenv("MIN_TRADE_IDR", 12000)) # Minimum order Indodax (12rb)

# Small-account sizing clamp (configurable via env)
# Allows runtime tuning without editing strategy code.
SMALL_ACCOUNT_CLAMP_MIN_TQ_PROB = float(os.getenv("SMALL_ACCOUNT_CLAMP_MIN_TQ_PROB", 0.45))
SMALL_ACCOUNT_CLAMP_MIN_BAL_MULT = float(os.getenv("SMALL_ACCOUNT_CLAMP_MIN_BAL_MULT", 1.05))
SMALL_ACCOUNT_CLAMP_TIER1_BALANCE_MAX = int(os.getenv("SMALL_ACCOUNT_CLAMP_TIER1_BALANCE_MAX", 30000))
SMALL_ACCOUNT_CLAMP_TIER2_BALANCE_MAX = int(os.getenv("SMALL_ACCOUNT_CLAMP_TIER2_BALANCE_MAX", 60000))
SMALL_ACCOUNT_CLAMP_TIER1_PCT = float(os.getenv("SMALL_ACCOUNT_CLAMP_TIER1_PCT", 1.00))
SMALL_ACCOUNT_CLAMP_TIER2_PCT = float(os.getenv("SMALL_ACCOUNT_CLAMP_TIER2_PCT", 1.02))
SMALL_ACCOUNT_CLAMP_TIER3_PCT = float(os.getenv("SMALL_ACCOUNT_CLAMP_TIER3_PCT", 1.05))

# Reserve strategy: keep a capital/slot buffer for fast high-opportunity entries.
CAPITAL_RESERVE_FAST_TRADE_PCT = float(os.getenv("CAPITAL_RESERVE_FAST_TRADE_PCT", 0.20))
POSITION_RESERVE_PCT = float(os.getenv("POSITION_RESERVE_PCT", 0.20))
HIGH_OPPORTUNITY_TQ_PROB = float(os.getenv("HIGH_OPPORTUNITY_TQ_PROB", 0.75))


# --- [Scalping Targets] ---
TP_PCT = 0.035                  # Target TP 3.5% (net profit = 3.5% - 0.6322% fee = 2.87% viable profit)
SL_PCT = -0.035                 # [SL/DCA SYNC] Dilebarkan ke -3.5% agar DCA Sniper bisa berjalan di -1.5% sebelum likuidasi
AUTO_SL_ENABLED = False         # [MANUAL FOCUS] Disable auto SL entirely as requested by user
MAX_HOLD_MINUTES = 10           # [ANTI-ZOMBIE] Bunuh posisi mandek lebih cepat (dari 15 → 10 menit)

# Dynamic tiered profit thresholds for exit_manager
TIER2_PROFIT_PCT = float(os.getenv("TIER2_PROFIT_PCT", 0.015))  # 1.5% default
TIER3_PROFIT_PCT = float(os.getenv("TIER3_PROFIT_PCT", 0.045))  # 4.5% default
TIER2_PROFIT_IDR_PCT = float(os.getenv("TIER2_PROFIT_IDR_PCT", 0.01))  # 1% of MIN_NET_PROFIT_IDR or amount_idr (lowered from 10% for faster exits on profitable positions)
TIER3_PROFIT_IDR_PCT = float(os.getenv("TIER3_PROFIT_IDR_PCT", 0.05))  # 5% of MIN_NET_PROFIT_IDR or amount_idr (lowered from 20%)

# DUST Recovery Policy (quantitative controls)
# Dust positions are only topped-up when signal quality is high and
# runtime budget/cooldown constraints are satisfied.
DUST_RECOVERY_ENABLED = os.getenv("DUST_RECOVERY_ENABLED", "true").lower() in ("1", "true", "yes", "on")
DUST_RECOVERY_MIN_CONFIDENCE = float(os.getenv("DUST_RECOVERY_MIN_CONFIDENCE", 0.70))
DUST_RECOVERY_COOLDOWN_SEC = int(os.getenv("DUST_RECOVERY_COOLDOWN_SEC", 600))
DUST_RECOVERY_MIN_VOL_IDR = float(os.getenv("DUST_RECOVERY_MIN_VOL_IDR", 10000000))
DUST_RECOVERY_MAX_TOPUPS_PER_CYCLE = int(os.getenv("DUST_RECOVERY_MAX_TOPUPS_PER_CYCLE", 2))
DUST_RECOVERY_MAX_BUDGET_IDR_PER_CYCLE = float(os.getenv("DUST_RECOVERY_MAX_BUDGET_IDR_PER_CYCLE", 20000))

# Default MIN_NET_PROFIT_IDR based on INITIAL_CAPITAL (fallback only)
MIN_NET_PROFIT_IDR = int(max(50, INITIAL_CAPITAL * 0.0005))

def get_dynamic_min_net_profit(current_balance: float = None) -> int:
    """Calculate MIN_NET_PROFIT_IDR dynamically based on current account balance.
    
    Tiers adapts as capital grows — no manual reconfiguration needed:
    - Balance ≤ 250k: 0.05% modal (minimum 50 IDR)
    - Balance 250k-1M: 0.1% modal (minimum 200 IDR)  
    - Balance > 1M: 0.2% modal (minimum 500 IDR)
    
    If current_balance is None, reads from database (live balance).
    """
    if current_balance is None:
        try:
            from app.infrastructure.database import db
            # Get actual balance from DB — reflects current account state
            stats = db.get_daily_stats()
            opening_balance = float(stats.get('opening_balance', INITIAL_CAPITAL))
            current_balance = opening_balance + float(stats.get('total_pnl_idr', 0))
        except Exception:
            current_balance = INITIAL_CAPITAL
    
    current_balance = max(current_balance, INITIAL_CAPITAL)  # Never go below initial
    
    if current_balance <= 100_000:
         return int(max(5, current_balance * 0.0001))    # 0.01% modal
    elif current_balance <= 250_000:
        return int(max(50, current_balance * 0.0005))    # 0.05% modal
    elif current_balance <= 1_000_000:
        return int(max(200, current_balance * 0.001))    # 0.1% modal
    else:
        return int(max(500, current_balance * 0.002))    # 0.2% modal

def get_dynamic_tp_pct(current_balance: float = None) -> float:
    """Calculate TP_PCT dynamically based on current account balance.
    
    Small accounts need faster TP to compound gains without waiting for full scalp:
    - Balance ≤ 100k: 1.5% TP (fast 15-min scalp)
    - Balance 100k-250k: 2.0% TP
    - Balance 250k-1M: 2.5% TP
    - Balance > 1M: 3.5% TP (original aggressive scalp)
    
    If current_balance is None, reads from database (live balance).
    """
    if current_balance is None:
        try:
            from app.infrastructure.database import db
            stats = db.get_daily_stats()
            opening_balance = float(stats.get('opening_balance', INITIAL_CAPITAL))
            current_balance = opening_balance + float(stats.get('total_pnl_idr', 0))
        except Exception:
            current_balance = INITIAL_CAPITAL
    
    current_balance = max(current_balance, INITIAL_CAPITAL)
    
    if current_balance <= 100_000:
        return 0.015  # 1.5% untuk small account
    elif current_balance <= 250_000:
        return 0.020  # 2.0% untuk medium-small account
    elif current_balance <= 1_000_000:
        return 0.025  # 2.5% untuk medium account
    else:
        return 0.035  # 3.5% untuk large account (original)

# --- [Basket Exit: Guaranteed Profit Harvester] ---
# Trigger basket harvest segera ketika combined net PnL mencapai target ini,
# bahkan jika semua posisi masih profit (tidak perlu tunggu ada yang rugi).
# Set ke 0 untuk nonaktifkan.
BASKET_HARVEST_TARGET_IDR = int(max(3000, INITIAL_CAPITAL * 0.05))  # ~5% modal (min 3k)

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
MOMENTUM_TARGET_PNL_IDR = BASKET_HARVEST_TARGET_IDR  # mengikuti target harvest aktif

# Buffer above the floor that defines the "Safety Zone" range
# [MOMENTUM_TARGET_PNL_IDR, MOMENTUM_TARGET_PNL_IDR + MOMENTUM_SAFETY_BUFFER_IDR].
# When portfolio PnL drops into this range from above, momentum-gated logic activates.
MOMENTUM_SAFETY_BUFFER_IDR = int(max(500, MOMENTUM_TARGET_PNL_IDR * 0.15))  # 15% buffer (min 500)

# Velocity threshold (IDR / second). Positive velocity exceeding this value means
# the portfolio is recovering → enable Tight Trailing Stop instead of immediate exit.
MOMENTUM_RECOVERY_THRESHOLD = 15    # Modal 100k: threshold recovery lebih realistis

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
SMALL_ACCOUNT_MODE = INITIAL_CAPITAL <= 250_000
HARVEST_MOMENTUM_MIN_CONFIDENCE = 78.0 if SMALL_ACCOUNT_MODE else 82.0   # total gate confidence (%).
HARVEST_MOMENTUM_MIN_TQ_PROB = 0.68 if SMALL_ACCOUNT_MODE else 0.72       # technical probability (0..1).
HARVEST_MOMENTUM_MIN_SCORE = 58.0 if SMALL_ACCOUNT_MODE else 65.0         # ranked momentum score.
HARVEST_MOMENTUM_MIN_5M_PCT = 0.15 if SMALL_ACCOUNT_MODE else 0.25        # short momentum (%).
HARVEST_MOMENTUM_MIN_15M_PCT = 0.35 if SMALL_ACCOUNT_MODE else 0.60       # follow-through momentum (%).
HARVEST_MOMENTUM_MIN_TP_PCT = 0.032 if SMALL_ACCOUNT_MODE else 0.035      # minimum expected TP (aligned with TP_PCT).
HARVEST_MOMENTUM_MIN_RR = 1.5 if SMALL_ACCOUNT_MODE else 1.8              # minimum reward-to-risk ratio.

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
NET_ZERO_IMMEDIATE_VELOCITY = -120.0  # IDR/s  (profil modal kecil)

# Minimum combined Net PnL (realized + unrealized) the portfolio must reach before
# the guard even considers firing.  Prevents triggering on Rp -1 rounding noise or
# a single position dipping -0.3% while heading to its +3% TP.
# Rule of thumb: set to -(3 × BASKET_HARVEST_TARGET_IDR) so the guard only fires
# when the session is in genuine loss territory — not just temporarily underwater.
NET_ZERO_PNL_FLOOR_IDR = -int(max(15000, INITIAL_CAPITAL * 0.15))  # 15% modal (min -15k)

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
# Default ideal profile (Loose Gates OFF): lebih ketat untuk kualitas entry.
TECH_CONFIDENCE_THRESHOLD = 0.45
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

# --- Adaptive Indicator Settings ---
# Volume reliability guard: coin dengan vol 24h < threshold akan mendapatkan
# bobot OBV Slope dan Volume Ratio yang diturunkan (×0.3) di derive_trade_signal().
# Karena data volume tipis = sinyal OBV/VolumeRatio noise tinggi.
VOLUME_RELIABILITY_THRESHOLD = int(os.getenv("VOLUME_RELIABILITY_THRESHOLD", 200_000_000))  # 200M IDR

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
_TECH_MODE_CACHE = {"value": None, "last_check": 0}

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

def get_dynamic_technical_only_mode():
    import time
    now = time.time()
    # Cache for 2 seconds to avoid slamming DB on high-frequency loops
    if _TECH_MODE_CACHE["value"] is not None and (now - _TECH_MODE_CACHE["last_check"]) < 2:
        return _TECH_MODE_CACHE["value"]

    try:
        from app.infrastructure.database import db
        db_val = db.get_state("technical_only_mode")
        if db_val is not None:
            val = str(db_val).lower() in ("true", "1")
            _TECH_MODE_CACHE["value"] = val
            _TECH_MODE_CACHE["last_check"] = now
            return val
    except Exception:
        pass

    _TECH_MODE_CACHE["value"] = _TECHNICAL_ONLY_INTERNAL
    _TECH_MODE_CACHE["last_check"] = now
    return _TECHNICAL_ONLY_INTERNAL

def clear_dry_run_cache():
    _DRY_RUN_CACHE["value"] = None
    _DRY_RUN_CACHE["last_check"] = 0
    _TECH_MODE_CACHE["value"] = None
    _TECH_MODE_CACHE["last_check"] = 0

def __getattr__(name):
    if name == "DRY_RUN":
        return get_dynamic_dry_run()
    if name == "DRY_RUN_LOOSE_GATES":
        return get_dynamic_loose_gates()
    if name == "TECHNICAL_ONLY_MODE":
        return get_dynamic_technical_only_mode()
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
