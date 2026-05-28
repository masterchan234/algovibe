"""
app/application/use_cases/entry_gates.py

All pre-entry gate checks for a candidate pair:
  - open position / blacklist guard
  - cooldown guard
  - trend / breakout requirement
  - multi-confluence technical gate
  - technical probability threshold (pure, no AI bias)
  - K-NN pattern scoring and confidence threshold

Refactored for HFT Low-Latency:
  - Event-Driven State Monitors (ATR, EMA-50, Cooldowns, Global Pause) cached in-memory once per minute.
  - Multi-Timeframe Alignment (MTFA) checking price against 5m and 15m EMAs.
  - Redis O(1) CVD volume imbalance ratio integration.
  - Redis O(1) K-NN Isolated worker verdict retrieval.
"""

import time
import os
import json
import threading
import numpy as np
import redis

from config import (
    MAX_CONSECUTIVE_LOSSES,
    STRONG_IMBALANCE_RATIO,
)
import config
from app.infrastructure.database import db
from app.domain.technicals import (
    calculate_rsi, derive_trade_signal, get_technical_summary,
    calculate_macd, calculate_bollinger_bands, calculate_stochastic_rsi, calculate_ema,
    calculate_atr, compute_adaptive_thresholds,
    _MACD_NORM_FACTOR,
)
from utils.logger import log_message

_BREAK = "BREAK"  # sentinel value returned to caller

# Thread-safe in-memory cache for State Monitors
_STATE_CACHE = {}
_STATE_LOCK = threading.Lock()

# Initialize Redis client for fast O(1) lookups
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
if "redis://" not in redis_url:
    redis_url = "redis://redis:6379/0"
try:
    r_client = redis.from_url(redis_url)
except Exception:
    r_client = None


def get_state_monitors(pair: str, history: list, bot, p: dict) -> dict:
    """Calculates and caches heavy State Monitors once per minute."""
    now_min = int(time.time() // 60)
    pair_lower = pair.lower()

    with _STATE_LOCK:
        cached = _STATE_CACHE.get(pair_lower)
        if cached and cached.get('minute') == now_min:
            return cached

        # 1. Cooldown Guard — read from pair_runtime_state (normalized table)
        try:
            prs = db.get_pair_runtime_state(pair_lower)
            cooldown_until = float(prs.get('cooldown_until') or 0)
        except Exception:
            cooldown_until = 0.0
        now_ts = time.time()
        cooldown_ok = now_ts >= cooldown_until

        # 2. ATR Volatility Filter (Calculated once per minute)
        atr_ok = True
        atr_pct = 0.0
        if len(history) >= 15:
            try:
                _prices_arr = history[-15:]
                _raw_highs = list(getattr(bot, 'ohlc_highs', {}).get(pair, []))
                _raw_lows  = list(getattr(bot, 'ohlc_lows',  {}).get(pair, []))
                if len(_raw_highs) >= 15 and len(_raw_lows) >= 15:
                    _atr = calculate_atr(_raw_highs[-15:], _raw_lows[-15:], _prices_arr, period=14)
                else:
                    _atr = calculate_atr(_prices_arr, _prices_arr, _prices_arr, period=14)
                _last = float(p.get('last', 1.0)) or 1.0
                atr_pct = (_atr / _last) * 100.0
                _atr_limit = 4.5 if config.DRY_RUN_LOOSE_GATES else 3.0
                atr_ok = atr_pct <= _atr_limit
            except Exception:
                pass

        # 3. EMA-50 Trend & MTFA Alignment (Calculated once per minute)
        ema_50_ok = True
        _last_price = float(p.get('last', 0.0))
        
        # MTFA: Price must reside above 5m EMA-50 and 15m EMA-50
        # In a 1m history list, 5m closes can be approximated by sampling every 5th element
        closes_5m = history[-1::-5][::-1]
        closes_15m = history[-1::-15][::-1]
        
        ema_5m = calculate_ema(closes_5m, 10) if len(closes_5m) >= 5 else _last_price
        ema_15m = calculate_ema(closes_15m, 3) if len(closes_15m) >= 2 else _last_price
        
        # Strictly enforce above both 5m and 15m EMAs unless overridden by loose gates
        if config.DRY_RUN_LOOSE_GATES:
            mtfa_ok = True
        else:
            mtfa_ok = _last_price >= ema_5m and _last_price >= ema_15m

        if len(history) >= 50:
            try:
                _ema50 = calculate_ema(history, 50)
                if _last_price > 0 and _last_price < _ema50:
                    ema_50_ok = False
            except Exception:
                pass

        # 4. Adaptive Config (Cached once per minute)
        adaptive_eg = db.get_coin_indicator_setup(pair) or (compute_adaptive_thresholds(history) if len(history) >= 30 else None)

        # 5. Consecutive Losses Halt (Query Postgres db once per minute)
        conlosses = int(db.get_state("consecutive_losses", 0))
        global_pause_ok = conlosses < MAX_CONSECUTIVE_LOSSES

        state_data = {
            'minute': now_min,
            'cooldown_ok': cooldown_ok,
            'cooldown_until': cooldown_until,
            'atr_ok': atr_ok,
            'atr_pct': atr_pct,
            'ema_50_ok': ema_50_ok,
            'mtfa_ok': mtfa_ok,
            'adaptive_eg': adaptive_eg,
            'global_pause_ok': global_pause_ok,
            'consecutive_losses': conlosses,
        }
        _STATE_CACHE[pair_lower] = state_data
        return state_data


def _derive_radar_recommendation(p, enriched, adaptive_cfg):
    """Satu sumber rekomendasi untuk Radar + gate entry bot (Strict Mode)."""
    signal_v = str(enriched.get('signal_verdict', 'HOLD') or 'HOLD').upper()
    trend = str(enriched.get('trend', 'N/A') or 'N/A').upper()
    breakout = enriched.get('breakout', False)
    score = float(p.get('score', 0) or 0)
    tq_prob = float(enriched.get('tq_prob', 0) or 0)
    current_vol = float(p.get('volatility', 0) or 0)
    
    is_loose = bool(config.DRY_RUN_LOOSE_GATES)
    is_trend_ok = breakout or trend == "UP" or (is_loose and trend == "SIDEWAYS")
    
    base_thresh = 55.0
    if current_vol > 2.0:
        base_thresh = 65.0
    elif current_vol < 0.6:
        base_thresh = 50.0
    if trend != "UP":
        base_thresh += 4.0
    if is_loose:
        base_thresh -= 8.0
    base_thresh = max(35.0, min(75.0, base_thresh))
    
    is_prob_ok = (tq_prob * 100) >= base_thresh

    if signal_v == 'BUY' and is_trend_ok and is_prob_ok:
        return "BUY", (
            f"signal={signal_v}, tq_prob={tq_prob * 100:.0f}%, trend={trend}, "
            f"vol={current_vol:.2f}%"
        )

    reason = "HOLD"
    if not is_trend_ok:
        reason = f"Trend {trend} (no breakout)"
    elif not is_prob_ok:
        reason = f"Prob {tq_prob*100:.0f}% < Thresh {base_thresh:.0f}%"

    return "HOLD", f"signal={signal_v} | {reason}"


def check_entry_gates(bot, p, enriched, regime, adaptive_cfg, open_positions):
    """
    Runs all HFT-optimized pre-entry gate checks in sequence.
    """
    from datetime import datetime
    
    pair = p.get('pair', 'UNKNOWN')
    pair_lower = pair.lower()

    def save_gate_state(msg):
        # Write to normalized pair_runtime_state (replaces KV bot_state entries)
        db.upsert_pair_runtime_state(
            pair_lower,
            gate_skip_reason=msg,
            gate_ts=datetime.now(),
        )

    trend = enriched['trend']
    breakout = enriched['breakout']
    history = enriched['history']
    tq_prob = enriched['tq_prob']
    current_vol = p['volatility']

    # ML Fast-Track: jika model per-pair sangat yakin (>= 0.68), bypass gate 4c/5/6.
    # Model sudah belajar dari 90 hari histori pair ini — lebih tahu dari threshold statis.
    ml_prob       = enriched.get('ml_prob')  # None jika tidak ada model
    ml_fast_track = ml_prob is not None and ml_prob >= 0.68

    #  1. Skip pairs already held or blacklisted (O(1) Trigger Monitor)
    if pair in open_positions or pair in bot.blacklist:
        return None

    # Load State Cache (Heavy calculations, DB lookups, Cooldowns, ATR once per minute)
    states = get_state_monitors(pair, history, bot, p)

    # Write tq_prob dynamically to Redis so isolated knn_worker can read it
    if r_client:
        try:
            r_client.set(f"tq_prob:{pair_lower}", str(tq_prob), ex=60)
        except Exception:
            pass

    #  2. Cooldown Guard (O(1) Trigger Monitor via State Cache)
    if not states['cooldown_ok']:
        log_message(f"[GATE] {pair} REJECTED: Cooldown active until {states['cooldown_until']}")
        return None

    #  3. Radar recommendation + tracking log (O(1) Trigger Monitor)
    radar_rec, radar_reason = _derive_radar_recommendation(p, enriched, adaptive_cfg)
    db.save_state(f"radar_reco_{pair}", radar_rec)
    db.save_state(f"radar_reco_reason_{pair}", radar_reason)

    log_message(
        f"[GATE] [TRACKING] {pair}: recommendation={radar_rec} | reason={radar_reason}"
    )

    #  4. Multi-Confluence Technical Gate (O(1) Trigger Monitor)
    _candle_vols = list(getattr(bot, 'ohlc_vols', {}).get(pair, []))
    tech = get_technical_summary(history, _candle_vols if len(_candle_vols) == len(history) else None)
    
    depth_data = db.get_market_depth(pair)
    if depth_data['bids'] and depth_data['asks']:
        total_bid = sum(b['vol'] for b in depth_data['bids'])
        total_ask = sum(a['vol'] for a in depth_data['asks'])
        total_depth = total_bid + total_ask
        if total_depth > 0:
            tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)

    _sector_eg = config.SECTOR_MAP.get(pair_lower)
    _vol_idr_eg = float((p or {}).get('vol_idr') or 0) or None
    tech_sig = derive_trade_signal(tech, sector=_sector_eg, adaptive_thresholds=states['adaptive_eg'], vol_idr_24h=_vol_idr_eg)

    if not config.DRY_RUN_LOOSE_GATES and tech_sig['verdict'] == 'SELL' and tech_sig.get('sell_score', 0) >= 5.0:
        msg = (
            f"[GATE] {pair} REJECTED: SELL BLOCK (score={tech_sig.get('sell_score',0):.1f})"
        )
        log_message(msg)
        save_gate_state(msg)
        return None

    #  4b. ATR Volatility Filter (O(1) Trigger Monitor via State Cache)
    if not states['atr_ok']:
        msg = f"[GATE] {pair} REJECTED: ATR Volatility {states['atr_pct']:.2f}% > limit"
        log_message(msg)
        save_gate_state(msg)
        return None

    #  4c. EMA-50 Trend & MTFA Alignment (O(1) Trigger Monitor via State Cache)
    is_explosion_setup = False
    if len(history) >= 14:
        # Bypass trend guards jika extreme Mean Reversion (Bottom-Explosion)
        is_explosion_setup = (
            (tech.get('rsi', 50.0) < 26.0 and tech.get('buy_pressure', 50.0) >= 66.0 and tech.get('volume_ratio', 1.0) >= 2.1)
            or tech.get('velocity', 0.0) > 0.2
        )

    # ML fast-track juga bypass EMA-50/MTFA (model sudah belajar trend dari 90 hari data)
    bypass_trend_check = is_explosion_setup or ml_fast_track

    if not config.DRY_RUN_LOOSE_GATES and not bypass_trend_check:
        # Verify 1m EMA-50
        if not states['ema_50_ok']:
            _last_price = float(p.get('last', 0.0))
            msg = f"[GATE] {pair} REJECTED: Price {_last_price:.4f} < 1m EMA-50"
            log_message(msg)
            save_gate_state(msg)
            return None

        # Verify 5m & 15m Multi-Timeframe Alignment (MTFA)
        if not states['mtfa_ok']:
            msg = f"[GATE] {pair} REJECTED: Price fails 5m/15m EMA-50 trend alignment (MTFA)"
            log_message(msg)
            save_gate_state(msg)
            return None

    if is_explosion_setup:
        log_message(
            f"[GATE] {pair} [SPECIAL]: Bottom-Explosion setup (Vel={tech.get('velocity'):.2f}%, "
            f"VR={tech.get('volume_ratio'):.1f}). Bypassing EMA-50/MTFA trend filters."
        )
    elif ml_fast_track:
        log_message(
            f"[GATE] {pair} [ML-FAST-TRACK] ml_prob={ml_prob:.3f} >= 0.68 — bypassing EMA-50/MTFA"
        )

    #  4d. Vertical Spike / RSI Ceiling Filter (O(1) Trigger Monitor)
    _rsi_ceiling = 82.0 if config.DRY_RUN_LOOSE_GATES else 75.0
    _rsi_now = tech.get('rsi', 50.0)
    _velocity = tech.get('velocity', 0.0)
    _vol_ratio = tech.get('volume_ratio', 1.0)
    _ema9 = tech.get('ema9', 0.0)
    _last = float(p.get('last', 0.0))
    
    _is_pump_momentum = _velocity > 0.3 and _vol_ratio > 1.6
    
    if _rsi_now > _rsi_ceiling and not _is_pump_momentum:
        msg = f"[GATE] {pair} REJECTED: RSI Ceiling {_rsi_now:.1f} > {_rsi_ceiling:.1f}"
        log_message(msg)
        save_gate_state(msg)
        return None

    if _ema9 > 0:
        _dist_ema9 = (_last - _ema9) / _ema9 * 100.0
        _dist_limit = 4.0 if config.DRY_RUN_LOOSE_GATES else 2.0
        if _dist_ema9 > _dist_limit:
            msg = f"[GATE] {pair} REJECTED: Vertical Spike {_dist_ema9:.2f}% > EMA-9 limit"
            log_message(msg)
            save_gate_state(msg)
            return None

    #  4e. Resistance-Guard (Double-Top Prevention) (O(1) Trigger Monitor)
    if len(history) >= 20:
        _recent_high = max(history)
        _threshold_high = _recent_high * 0.993
        _stoch_k = tech.get('stoch_k', 50.0)
        
        if _last >= _threshold_high and (_rsi_now > 60.0 or _stoch_k > 70.0) and not breakout and _velocity < 0.2:
            msg = f"[GATE] {pair} REJECTED: Resistance-Guard Price {_last:.0f} near High {_recent_high:.0f}"
            log_message(msg)
            save_gate_state(msg)
            return None

    #  5. Technical probability & CVD imbalance threshold (O(1) Trigger Monitor via Redis)
    if tq_prob < 0:
        msg = "[GATE] WARMUP: Insufficient data for TQ_Prob"
        log_message(msg)
        save_gate_state(msg)
        return None

    # Retrieve real-time CVD imbalance ratio from Redis (zero-latency)
    imbalance_ratio = 1.0
    if r_client:
        try:
            imb_raw = r_client.get(f"imbalance:{pair_lower}")
            if imb_raw:
                imbalance_ratio = float(imb_raw)
        except Exception:
            pass

    is_loose = bool(config.DRY_RUN_LOOSE_GATES)
    tech_gate_threshold = float(adaptive_cfg['tech_threshold'])
    imb_gate_threshold = float(STRONG_IMBALANCE_RATIO)
    if is_loose:
        tech_gate_threshold = max(0.25, tech_gate_threshold - 0.10)
        imb_gate_threshold = max(1.10, imb_gate_threshold - 0.25)

    tech_ready = tq_prob >= tech_gate_threshold
    has_strong_imb = imbalance_ratio >= imb_gate_threshold
    if not tech_ready and not has_strong_imb and not ml_fast_track:
        msg = f"[GATE] {pair} REJECTED: Tech prob {tq_prob * 100:.0f}% < {tech_gate_threshold * 100:.0f}%, CVD Imbalance {imbalance_ratio:.2f} < {imb_gate_threshold:.2f}"
        log_message(msg)
        save_gate_state(msg)
        return None

    #  6. Trend / breakout requirement (O(1) Trigger Monitor)
    if not breakout and trend != "UP":
        _velocity = tech.get('velocity', 0.0)
        _vol_ratio = tech.get('volume_ratio', 1.0)
        
        sideway_relaxed_ok = (
            ml_fast_track       # ML sangat yakin — izinkan SIDEWAYS tanpa syarat tambahan
            or is_loose
            or (trend == "SIDEWAYS" and _velocity > 0.15 and _vol_ratio > 1.3
                and tq_prob >= max(tech_gate_threshold, 0.45)
                and imbalance_ratio >= 1.05)
        )
        if not sideway_relaxed_ok:
            msg = f"[GATE] {pair} REJECTED: No trend/breakout support (trend={trend})"
            log_message(msg)
            save_gate_state(msg)
            return None

    #  7. Radar recommendation guard (O(1) Trigger Monitor)
    if radar_rec == "SELL":
        msg = f"[BLOCK] RADAR BLOCK: rekomendasi SELL ({radar_reason})"
        print(f"[DEBUG-GATE] {pair} REJECTED: RADAR SELL ({radar_reason})", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    #  9. Dynamic threshold based on volatility + trend (O(1) Trigger Monitor)
    dynamic_threshold = 55.0
    if current_vol > 2.0:
        dynamic_threshold = 65.0
    elif current_vol < 0.6:
        dynamic_threshold = 50.0
    if trend != "UP":
        dynamic_threshold += 3.0
    if is_loose:
        dynamic_threshold -= 8.0

    entry_policy = bot.oracle.get_entry_policy(regime, dynamic_threshold)
    dynamic_threshold = entry_policy['dynamic_threshold']

    #  10. Consecutive-loss halt (O(1) Trigger Monitor via State Cache)
    if not states['global_pause_ok']:
        msg = f"  GLOBAL PAUSE ({states['consecutive_losses']} losses): Evaluating for SHADOW PROBE."
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    #  11. K-NN Isolated pattern scoring (O(1) Redis Retrieval with Safe Fallback)
    rsi_val = calculate_rsi(history)
    knn_decision = None

    if r_client:
        try:
            knn_raw = r_client.get(f"knn_verdict:{pair_lower}")
            if knn_raw:
                knn_decision = json.loads(knn_raw)
        except Exception:
            pass

    # Safe Fallback to synchronous calculation if Redis value is missing or client failed
    if not knn_decision:
        if len(history) >= 20:
            prices = np.array(history, dtype=float)
            alpha = 2 / (20 + 1)
            ema_20 = prices[0]
            for price_v in prices[1:]:
                ema_20 = (price_v * alpha) + (ema_20 * (1 - alpha))
            ema_dist = round((p['last'] - ema_20) / ema_20, 6)
        else:
            ema_dist = 0.0

        current_dna = {
            "tq_prob": tq_prob,
            "bb_pct": tech.get('bb_pct', 0.5),
            "macd_norm": abs(tech.get('macd_hist', 0)) / max(1.0, float(p['last'])) * _MACD_NORM_FACTOR,
            "imbalance": imbalance_ratio,
            "volatility": current_vol,
            "rsi": rsi_val,
            "ema_dist": ema_dist,
        }
        knn_decision = bot.oracle.find_winning_pattern(pair, current_dna)

    scalper_boost = 0.0
    if imbalance_ratio > 1.8:
        scalper_boost = 15.0
    elif imbalance_ratio > 1.3:
        scalper_boost = 8.0

    if knn_decision.get('verdict') == "AVOID":
        msg = "[BLOCK] [K-NN Avoid]: pola historis buruk, skip."
        print(f"[DEBUG-GATE] {pair} REJECTED: K-NN AVOID", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    base_confidence = float(knn_decision.get('confidence', 0.0))
    min_base_conf = 50.0 if current_vol <= 2.0 else 55.0
    if config.DRY_RUN_LOOSE_GATES:
        min_base_conf = 35.0 if current_vol <= 2.0 else 40.0

    if base_confidence < min_base_conf:
        msg = f"SKIP: base_conf {base_confidence:.1f}% < floor {min_base_conf:.1f}%"
        print(f"[DEBUG-GATE] {pair} REJECTED: Base Confidence {base_confidence:.1f}% < Floor {min_base_conf:.1f}%", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    #  13. Final Confidence threshold
    boost_credit = min(scalper_boost, 6.0)
    total_confidence = base_confidence + boost_credit

    res = {
        "mode": "SHADOW" if (states['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES) else "LIVE",
        "radar_rec": radar_rec,
        "radar_reason": radar_reason,
        "rsi_val": rsi_val,
        "total_confidence": total_confidence,
        "dynamic_threshold": dynamic_threshold,
        "knn_decision": knn_decision,
        "velocity": float(_velocity),
        "baseline_price": float(min(history[-5:])) if len(history) >= 5 else float(p.get('last', 1.0)) * 0.99,
    }
    print(f"[DEBUG-GATE] {pair} PASSED ALL GATES! Result: {res}", flush=True)

    return res
