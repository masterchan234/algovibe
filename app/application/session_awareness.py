"""
app/application/session_awareness.py — Unified Bot Session Awareness Engine (v1.0)

Mengkonsolidasikan semua kondisi bot menjadi SATU state aktif dengan parameter
strategi yang tepat. Dipanggil sekali per scan cycle (~2s) dari brain_inference.

Priority ladder (paling defensif → paling agresif):
  HALT → RECOVERY_CRISIS → RECOVERY_DAILY → RECOVERY_ALLTIME → CAUTIOUS → NORMAL → MOMENTUM_CHASE

Kalibrasi oleh senior crypto trader:
- Modal $1000 USDT, fee ~0.5% round-trip, TP target 0.5-0.8%
- Daily loss limit 3% = $30/hari
- Position size default 12% modal = $120/trade

Filosofi utama:
- Saat drawdown → KECILKAN size, NAIKKAN threshold. Jangan martingale.
- Saat bullish + profitable → tekan edge secara MODERAT (bukan rakus).
- Konsistensi > agresivitas. Scalping menang lewat frekuensi, bukan jackpot.
"""

import os
import json
import time
import logging

import redis

from app.infrastructure.database import db
from utils.logger import log_message
import config

logger = logging.getLogger(__name__)

# ── Strategy Definitions ──────────────────────────────────────────────────────
# Setiap state mendefinisikan parameter overlay ke adaptive_cfg.
# parameter yang tidak ada di sini TIDAK di-override (gunakan default config.py).
#
# Rasionalisasi:
# - tech_threshold   : probabilitas teknikal minimum (predictor output 0.0–1.0)
#                      Makin tinggi = makin selektif = lebih sedikit trade = lebih aman
# - buy_score_threshold: skor momentum minimum (0–10). Default 6.5.
#                      7.0 = butuh 2 indikator kuat (velocity+volume atau velocity+OB)
#                      8.0 = butuh hampir semua indikator setuju (sangat jarang)
# - position_size_factor: multiplier ke CAPITAL_PER_TRADE_PCT (1.0 = normal $120)
#                      0.25 = $30/trade — batasi kerugian maksimum
#                      1.15 = $138/trade — modest press saat kondisi mendukung
# - max_new_positions : maks posisi BARU yang bisa dibuka per scan cycle
# - focus_top_n       : hanya scan top-N pair dari ranking skor (lebih sedikit = lebih selektif)

STRATEGIES = {
    "HALT": {
        "tech_threshold":       0.99,   # effectively unreachable
        "buy_score_threshold":  10.0,   # effectively unreachable
        "position_size_factor": 0.0,    # no entry
        "max_new_positions":    0,
        "focus_top_n":          0,
        "description": (
            "Consecutive loss limit reached. All entries blocked. "
            "Manual reset required or wait for cooldown to expire."
        ),
    },
    "RECOVERY_CRISIS": {
        # Drawdown > 3x daily loss limit (>$90 all-time realized loss).
        # Strategi: micro-size + ultra-strict confluence. Cegah kerusakan lebih lanjut.
        # Hanya masuk jika ketiga indikator (velocity 3.5 + volume 3.5 + OB min 1.0) setuju.
        "tech_threshold":       0.80,
        "buy_score_threshold":  8.0,
        "position_size_factor": 0.25,   # $30/trade
        "max_new_positions":    1,
        "focus_top_n":          3,
        "description": (
            "Deep drawdown (>3x daily limit). Micro-size trades only. "
            "Near-perfect confluence required. Objective: stop the bleeding."
        ),
    },
    "RECOVERY_DAILY": {
        # Daily loss limit sudah terlampaui hari ini ($30 hari ini sudah habis).
        # Strategi: satu kesempatan recovery per cycle dengan bar sangat tinggi.
        # Masih aktif agar tidak melewatkan momentum nyata.
        "tech_threshold":       0.78,
        "buy_score_threshold":  7.5,
        "position_size_factor": 0.40,   # $48/trade
        "max_new_positions":    1,
        "focus_top_n":          5,
        "description": (
            "Daily loss limit hit. Max 1 new position, size -60%. "
            "Only enter strongest momentum setups. Objective: partial recovery."
        ),
    },
    "RECOVERY_ALLTIME": {
        # All-time realized PnL negatif (dalam drawdown), tapi daily masih aman.
        # Strategi: methodical recovery. JANGAN naikkan size untuk 'kejar' kerugian.
        # Banyak trader amatir melakukan martingale di sini — itu jalan kehancuran.
        # Konsisten dengan size lebih kecil + threshold lebih tinggi → equity curve naik perlahan tapi pasti.
        "tech_threshold":       0.65,
        "buy_score_threshold":  7.0,
        "position_size_factor": 0.65,   # $78/trade
        "max_new_positions":    2,
        "focus_top_n":          5,
        "description": (
            "Account in all-time drawdown. Methodical recovery: size -35%, "
            "higher thresholds. Consistency > aggression."
        ),
    },
    "CAUTIOUS": {
        # Market BEARISH/CHOPPY/CRASH, ATAU daily loss sudah >50% dari limit.
        # Momentum palsu sangat sering di kondisi ini → tighten entry, kurangi exposure.
        # Masih bisa profit dari pullback + volume spike yang valid.
        "tech_threshold":       0.58,
        "buy_score_threshold":  6.8,
        "position_size_factor": 0.80,   # $96/trade
        "max_new_positions":    3,
        "focus_top_n":          7,
        "description": (
            "Suboptimal conditions (choppy/bearish or daily loss >50%). "
            "Reduced exposure, slightly tighter thresholds."
        ),
    },
    "NORMAL": {
        # Kondisi standar: market ranging/unknown, PnL netral atau positif hari ini.
        # Gunakan parameter default penuh.
        "tech_threshold":       0.52,
        "buy_score_threshold":  6.5,
        "position_size_factor": 1.00,   # $120/trade
        "max_new_positions":    5,
        "focus_top_n":          10,
        "description": (
            "Standard operation. Full capital deployment. "
            "Scanning top 10 pairs with default thresholds."
        ),
    },
    "MOMENTUM_CHASE": {
        # Market BULLISH + all-time PnL positif + daily PnL positif hari ini.
        # Kondisi ideal untuk tekan edge. Tapi tetap disiplin: +15% size, bukan 2x.
        # Makin banyak pair dipantau → lebih banyak peluang valid masuk radar.
        "tech_threshold":       0.46,
        "buy_score_threshold":  6.2,
        "position_size_factor": 1.15,   # $138/trade (+15% dari normal)
        "max_new_positions":    6,
        "focus_top_n":          12,
        "description": (
            "Bull market + account profitable. Moderately pressing the edge: "
            "+15% size, wider scan range. Stay disciplined, not greedy."
        ),
    },
}

# ── In-Memory Cache ───────────────────────────────────────────────────────────
_cache: dict = {"state": None, "ts": 0.0, "params": None}
_CACHE_TTL_S = 5.0  # Evaluasi ulang setiap 5 detik

# ── Redis Client ──────────────────────────────────────────────────────────────
_redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
try:
    _r = redis.from_url(_redis_url, socket_connect_timeout=1)
    _r.ping()
except Exception:
    _r = None


def compute_session_awareness(oracle, regime: str, consecutive_losses: int) -> dict:
    """
    Evaluasi kondisi bot saat ini dan kembalikan strategy params aktif.

    Args:
        oracle           : instance Oracle (untuk get_daily_target_status)
        regime           : string market regime ("BULLISH", "BEARISH", "CHOPPY", "CRASH", "RANGING")
        consecutive_losses: jumlah loss berturut-turut saat ini

    Returns:
        dict dengan semua keys STRATEGIES + "state" + "reason" + "description"
        Keys ini langsung bisa di-inject ke adaptive_cfg.

    Side effects:
        - Menyimpan state ke bot_state DB (key: "session_awareness") untuk dashboard
        - Mempublikasikan ke Redis: "session_awareness_state", "session_size_factor",
          "session_max_positions" (30s TTL) untuk dibaca executive_core
        - Log saat terjadi state transition
    """
    now = time.time()
    # Gunakan cache jika masih fresh DAN ada state sebelumnya
    if now - _cache["ts"] < _CACHE_TTL_S and _cache["state"] is not None:
        return _cache["params"]

    try:
        target_status = oracle.get_daily_target_status()
        daily_pnl     = float(target_status.get("current_pnl", 0.0))
        loss_limit_hit = bool(target_status.get("loss_limit_hit", False))

        # Hitung daily loss limit value aktual (selalu positif sebagai referensi)
        try:
            balance_str = db.get_state("cached_idr_balance")
            effective_balance = float(balance_str) if balance_str else config.INITIAL_CAPITAL
        except Exception:
            effective_balance = config.INITIAL_CAPITAL
        daily_loss_limit = effective_balance * config.DAILY_LOSS_LIMIT_PCT  # e.g. $30

        # All-time realized PnL (dari bot_state, diupdate oleh executive_core)
        try:
            alltime_realized = float(db.get_state("net_pnl_realized") or 0.0)
        except Exception:
            alltime_realized = 0.0

        # Crisis threshold: kehilangan >3x daily limit all-time (e.g. >$90)
        crisis_threshold = -(daily_loss_limit * 3.0)

        # ── State Evaluation (priority: paling defensif ke paling agresif) ──
        if consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            state = "HALT"
            reason = (
                f"Consecutive losses {consecutive_losses}/{config.MAX_CONSECUTIVE_LOSSES} "
                "reached. Trading blocked."
            )

        elif alltime_realized <= crisis_threshold:
            state = "RECOVERY_CRISIS"
            reason = (
                f"All-time realized PnL ${alltime_realized:.2f} exceeded "
                f"crisis threshold ${crisis_threshold:.2f} (3x daily limit)."
            )

        elif loss_limit_hit:
            state = "RECOVERY_DAILY"
            reason = (
                f"Daily loss limit hit. "
                f"Daily PnL ${daily_pnl:.2f} <= -${daily_loss_limit:.2f}."
            )

        elif alltime_realized < -0.01:  # threshold kecil untuk hindari floating point noise
            state = "RECOVERY_ALLTIME"
            reason = (
                f"Account in all-time drawdown: ${alltime_realized:.2f}. "
                "Methodical recovery activated."
            )

        elif (
            regime in ("BEARISH", "CHOPPY", "CRASH")
            or daily_pnl < -(daily_loss_limit * 0.50)   # sudah rugi >50% daily limit hari ini
        ):
            state = "CAUTIOUS"
            reason = (
                f"Suboptimal conditions: regime={regime}, "
                f"daily_pnl=${daily_pnl:.2f}."
            )

        elif (
            regime == "BULLISH"
            and alltime_realized > 0.01
            and daily_pnl > 0.0
        ):
            state = "MOMENTUM_CHASE"
            reason = (
                f"Bull market + profitable: "
                f"all-time=${alltime_realized:.2f}, today=${daily_pnl:.2f}."
            )

        else:
            state = "NORMAL"
            reason = (
                f"Standard conditions: regime={regime}, "
                f"all-time=${alltime_realized:.2f}, today=${daily_pnl:.2f}."
            )

        # ── Build Result ───────────────────────────────────────────────────
        params = dict(STRATEGIES[state])
        params["state"]   = state
        params["reason"]  = reason

        # ── Log on Transition ─────────────────────────────────────────────
        prev_state = _cache.get("state")
        if state != prev_state:
            log_message(
                f"[AWARENESS] {prev_state or 'INIT'} → {state} | {reason}"
            )
            # Telegram notif hanya untuk transisi signifikan (ke/dari recovery/halt)
            _critical_states = {"HALT", "RECOVERY_CRISIS", "RECOVERY_DAILY"}
            if state in _critical_states or (prev_state in _critical_states):
                try:
                    from utils.logger import send_telegram_message
                    send_telegram_message(
                        f"⚡ Bot Awareness: {prev_state or 'START'} → *{state}*\n"
                        f"_{reason}_\n"
                        f"Strategy: {params['description']}"
                    )
                except Exception:
                    pass

        # ── Persist to DB (dashboard visibility) ──────────────────────────
        _awareness_payload = {
            "state":       state,
            "reason":      reason,
            "description": params["description"],
            "params": {
                "tech_threshold":       params["tech_threshold"],
                "buy_score_threshold":  params["buy_score_threshold"],
                "position_size_factor": params["position_size_factor"],
                "max_new_positions":    params["max_new_positions"],
                "focus_top_n":          params["focus_top_n"],
            },
            "context": {
                "regime":            regime,
                "alltime_realized":  round(alltime_realized, 2),
                "daily_pnl":         round(daily_pnl, 2),
                "daily_loss_limit":  round(daily_loss_limit, 2),
                "consecutive_losses": consecutive_losses,
            },
        }
        try:
            db.save_state("session_awareness", json.dumps(_awareness_payload))
        except Exception as _de:
            logger.debug("[AWARENESS] DB save failed: %s", _de)

        # ── Publish to Redis (executive_core, cross-service) ──────────────
        if _r:
            try:
                _r.set("session_awareness_state",  state,                              ex=30)
                _r.set("session_size_factor",       str(params["position_size_factor"]), ex=30)
                _r.set("session_max_positions",     str(params["max_new_positions"]),   ex=30)
                _r.set("session_buy_score_thresh",  str(params["buy_score_threshold"]), ex=30)
            except Exception:
                pass

        # ── Update Cache ───────────────────────────────────────────────────
        _cache.update({"state": state, "ts": now, "params": params})
        return params

    except Exception as e:
        logger.warning("[AWARENESS] compute_session_awareness error: %s", e)
        fallback = dict(STRATEGIES["NORMAL"])
        fallback.update({"state": "NORMAL", "reason": f"error fallback: {e}"})
        return fallback


def get_current_state() -> str:
    """Kembalikan state saat ini dari cache (tanpa re-evaluate). Aman untuk dipanggil kapan saja."""
    return _cache.get("state") or "UNKNOWN"
