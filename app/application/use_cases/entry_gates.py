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

    def persist_scorecard(decision: str, score: float, threshold: float, notes: list,
                          total_conf: float = 0.0, aggressive: bool = False,
                          regime_lbl: str = "UNKNOWN", tech_ctx: dict = None):
        """Persist entry score telemetry for institutional audit trail."""
        try:
            safe_notes = [str(n).strip() for n in (notes or []) if str(n).strip()]
            payload = {
                "pair": pair,
                "decision": decision,
                "entry_score": round(float(score), 2),
                "entry_threshold": round(float(threshold), 2),
                "aggressive": bool(aggressive),
                "market_regime": regime_lbl,
                "total_confidence": round(float(total_conf), 2),
                "notes": safe_notes[:32],
                "ts": datetime.now().isoformat(),
            }
            db.save_state(f"entry_scorecard_{pair_lower}", json.dumps(payload))

            _tech = tech_ctx or {}
            # Keep more gate evidence in the audit row so dashboard gate cards
            # can map statuses from the latest decision without losing late-stage factors.
            score_notes_compact = "|".join(safe_notes)[:1800]
            db.insert_signal_log(
                pair=pair_lower,
                exchange=os.getenv("EXCHANGE", "binance").lower(),
                signal=f"ENTRY_GATE_{decision}",
                buy_score=float(score),
                sell_score=float(threshold),
                confidence=float(total_conf if total_conf > 0 else score),
                price=float(p.get('last', 0.0) or 0.0),
                volume_ratio=float(_tech.get('volume_ratio', 0.0) or 0.0),
                rsi=float(_tech.get('rsi', 50.0) or 50.0),
                regime=str(regime_lbl),
                executed=False,
                trade_id=f"scorecard:{score_notes_compact}",
                dry_run=bool(config.DRY_RUN),
            )
        except Exception:
            pass

    def reject_with_scorecard(msg: str, note: str, score: float = 0.0,
                              threshold: float = 0.0, regime_lbl: str = "UNKNOWN",
                              total_conf: float = 0.0, tech_ctx: dict = None):
        log_message(msg)
        save_gate_state(msg)
        persist_scorecard(
            decision="REJECT",
            score=score,
            threshold=threshold,
            notes=[note],
            total_conf=total_conf,
            aggressive=False,
            regime_lbl=regime_lbl,
            tech_ctx=tech_ctx,
        )
        return None

    trend = str(enriched['trend']).upper()
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
        return reject_with_scorecard(
            msg=f"[GATE] {pair} REJECTED: already open or blacklisted",
            note="POSITION_OR_BLACKLIST:-20",
        )

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
        return reject_with_scorecard(
            msg=f"[GATE] {pair} REJECTED: Cooldown active until {states['cooldown_until']}",
            note="COOLDOWN_ACTIVE:-18",
        )

    # Regime routing untuk profile entry institusional
    market_regime = "SIDEWAYS"
    if current_vol >= 2.2 or states.get('atr_pct', 0.0) >= 2.8:
        market_regime = "VOLATILE"
    elif trend == "UP" and breakout:
        market_regime = "TRENDING"

    regime_profile = {
        "TRENDING": {
            "entry_threshold": 60.0,
            "aggressive_threshold": 80.0,
            "tp_mult": 1.10,
            "sl_mult": 1.00,
            "cooldown_sec": 30,
            "size_mult": 1.00,
        },
        "SIDEWAYS": {
            "entry_threshold": 62.0,
            "aggressive_threshold": 82.0,
            "tp_mult": 0.90,
            "sl_mult": 0.92,
            "cooldown_sec": 90,
            "size_mult": 0.90,
        },
        "VOLATILE": {
            "entry_threshold": 68.0,
            "aggressive_threshold": 86.0,
            "tp_mult": 0.82,
            "sl_mult": 0.86,
            "cooldown_sec": 150,
            "size_mult": 0.78,
        },
    }[market_regime]

    # CHOPPY handling: tidak membunuh entry, tapi perketat manajemen risiko.
    regime_upper = str(regime or "RANGING").upper()
    if regime_upper == "CHOPPY":
        regime_profile["size_mult"] *= 0.80
        regime_profile["tp_mult"] *= 0.85
        regime_profile["sl_mult"] *= 0.90

    if config.DRY_RUN_LOOSE_GATES:
        regime_profile["entry_threshold"] -= 8.0
        regime_profile["aggressive_threshold"] -= 8.0

    # Cooldown beda per-regime agar re-entry cadence terkontrol.
    _last_entry_ts = float(db.get_state(f"entry_last_ts_{pair_lower}", 0.0) or 0.0)
    _elapsed = time.time() - _last_entry_ts
    if _elapsed < regime_profile["cooldown_sec"]:
        wait_left = int(regime_profile["cooldown_sec"] - _elapsed)
        msg = f"[GATE] {pair} REJECTED: Regime cooldown {wait_left}s ({market_regime})"
        return reject_with_scorecard(
            msg=msg,
            note="REGIME_COOLDOWN:-15",
            threshold=float(regime_profile['entry_threshold']),
            regime_lbl=market_regime,
        )

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
        return reject_with_scorecard(
            msg=f"[GATE] {pair} REJECTED: SELL BLOCK (score={tech_sig.get('sell_score',0):.1f})",
            note=f"SELL_BLOCK:{-abs(float(tech_sig.get('sell_score', 0) or 0)):.0f}",
            threshold=float(regime_profile['entry_threshold']),
            regime_lbl=market_regime,
            tech_ctx=tech,
        )

    #  4c. EMA-50 Trend & MTFA Alignment (O(1) Trigger Monitor via State Cache)
    is_explosion_setup = False
    if len(history) >= 14:
        # Bypass trend guards jika extreme Mean Reversion (Bottom-Explosion)
        is_explosion_setup = (
            (tech.get('rsi', 50.0) < 26.0 and tech.get('buy_pressure', 50.0) >= 66.0 and tech.get('volume_ratio', 1.0) >= 2.1)
            or tech.get('velocity', 0.0) > 0.2
        )

    # ML fast-track juga bypass penalti trend score.
    bypass_trend_check = is_explosion_setup or ml_fast_track

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
    
    _dist_ema9 = 0.0
    _dist_limit = 4.0 if config.DRY_RUN_LOOSE_GATES else 2.0
    if _ema9 > 0:
        _dist_ema9 = (_last - _ema9) / _ema9 * 100.0

    #  4e. Resistance-Guard (Double-Top Prevention) (O(1) Trigger Monitor)
    is_near_resistance = False
    if len(history) >= 20:
        _recent_high = max(history)
        _threshold_high = _recent_high * 0.993
        _stoch_k = tech.get('stoch_k', 50.0)
        is_near_resistance = (
            _last >= _threshold_high and (_rsi_now > 60.0 or _stoch_k > 70.0) and not breakout and _velocity < 0.2
        )

    #  5. Technical probability & CVD imbalance threshold (O(1) Trigger Monitor via Redis)
    if tq_prob < 0:
        return reject_with_scorecard(
            msg="[GATE] WARMUP: Insufficient data for TQ_Prob",
            note="TQ_WARMUP:-12",
            threshold=float(regime_profile['entry_threshold']),
            regime_lbl=market_regime,
            tech_ctx=tech,
        )

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

    # PRIORITAS 1: model scoring institusional (mengganti mayoritas hard gate).
    entry_score = 50.0
    score_notes = []

    def _add_score(points: float, reason: str):
        nonlocal entry_score
        entry_score += points
        score_notes.append(f"{reason}:{points:+.0f}")

    # EMA50 / MTFA (satu keluarga trend filter)
    if states.get('ema_50_ok'):
        _add_score(10.0, "EMA50_ALIGN")
    else:
        _add_score(-8.0, "EMA50_MISS")

    if states.get('mtfa_ok'):
        _add_score(8.0, "MTFA_ALIGN")
    else:
        _add_score(-6.0, "MTFA_MISS")

    # Orderflow / imbalance
    if has_strong_imb:
        _add_score(15.0, "STRONG_IMBALANCE")
    elif imbalance_ratio >= 1.05:
        _add_score(6.0, "IMBALANCE_OK")
    else:
        _add_score(-6.0, "IMBALANCE_WEAK")

    # Breakout / trend support
    if breakout:
        _add_score(20.0, "BREAKOUT")
    elif trend == "UP":
        _add_score(8.0, "TREND_UP")
    elif trend == "SIDEWAYS":
        _add_score(-2.0, "SIDEWAYS")
    else:
        _add_score(-10.0, "TREND_AGAINST")

    # TQ probability
    if tech_ready:
        _add_score(12.0, "TECH_PROB_OK")
    elif tq_prob >= max(0.40, tech_gate_threshold - 0.05):
        _add_score(3.0, "TECH_PROB_NEAR")
    else:
        _add_score(-12.0, "TECH_PROB_LOW")

    # RSI / vertical spike quality
    if 45.0 <= _rsi_now <= 68.0:
        _add_score(5.0, "RSI_HEALTHY")
    elif _rsi_now > _rsi_ceiling and not _is_pump_momentum:
        _add_score(-8.0, "RSI_CEILING")

    if _ema9 > 0 and _dist_ema9 > _dist_limit:
        _add_score(-8.0, "VERTICAL_SPIKE")

    if is_near_resistance:
        _add_score(-10.0, "RESISTANCE_NEAR")

    # Volatility quality
    if states['atr_ok']:
        _add_score(4.0, "ATR_OK")
    else:
        _add_score(-8.0, "ATR_HIGH")

    # Radar hanya informational score (bukan hard gate)
    if radar_rec == "BUY":
        _add_score(6.0, "RADAR_BUY")
    elif radar_rec == "SELL":
        _add_score(-6.0, "RADAR_SELL")

    # CHOPPY tidak dipenalti dua kali: no hard block/no extra score penalty.
    if bypass_trend_check:
        _add_score(6.0, "BYPASS_SIGNAL")

    entry_score = max(0.0, min(100.0, entry_score))

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
        return reject_with_scorecard(
            msg=f"{msg} [{pair}]",
            note="GLOBAL_PAUSE:-25",
            threshold=float(regime_profile['entry_threshold']),
            regime_lbl=market_regime,
            tech_ctx=tech,
        )

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
        return reject_with_scorecard(
            msg=f"{msg} [{pair}]",
            note="KNN_AVOID:-18",
            score=entry_score,
            threshold=float(regime_profile['entry_threshold']),
            regime_lbl=market_regime,
            tech_ctx=tech,
        )

    base_confidence = float(knn_decision.get('confidence', 0.0))
    min_base_conf = 50.0 if current_vol <= 2.0 else 55.0
    if config.DRY_RUN_LOOSE_GATES:
        min_base_conf = 35.0 if current_vol <= 2.0 else 40.0

    if base_confidence < min_base_conf:
        msg = f"SKIP: base_conf {base_confidence:.1f}% < floor {min_base_conf:.1f}%"
        print(f"[DEBUG-GATE] {pair} REJECTED: Base Confidence {base_confidence:.1f}% < Floor {min_base_conf:.1f}%", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        persist_scorecard(
            decision="REJECT",
            score=entry_score,
            threshold=float(regime_profile['entry_threshold']),
            notes=score_notes + [f"BASE_CONF:{base_confidence:.1f}"],
            total_conf=base_confidence,
            aggressive=False,
            regime_lbl=market_regime,
            tech_ctx=tech,
        )
        return None

    # Final score gate
    entry_threshold = float(regime_profile['entry_threshold'])
    aggressive_threshold = float(regime_profile['aggressive_threshold'])
    if ml_fast_track:
        entry_threshold = max(50.0, entry_threshold - 6.0)
        aggressive_threshold = max(70.0, aggressive_threshold - 4.0)

    if entry_score < entry_threshold:
        msg = (
            f"[GATE] {pair} REJECTED: score {entry_score:.1f} < {entry_threshold:.1f} "
            f"({market_regime}) [{', '.join(score_notes[:6])}]"
        )
        log_message(msg)
        save_gate_state(msg)
        persist_scorecard(
            decision="REJECT",
            score=entry_score,
            threshold=entry_threshold,
            notes=score_notes,
            total_conf=base_confidence,
            aggressive=False,
            regime_lbl=market_regime,
            tech_ctx=tech,
        )
        return None

    #  13. Final Confidence threshold
    boost_credit = min(scalper_boost, 6.0)
    total_confidence = base_confidence + boost_credit

    aggressive_entry = entry_score >= aggressive_threshold
    entry_size_factor = float(regime_profile['size_mult'])
    if aggressive_entry:
        entry_size_factor *= 1.20

    db.save_state(f"entry_last_ts_{pair_lower}", str(time.time()))
    persist_scorecard(
        decision="PASS",
        score=entry_score,
        threshold=entry_threshold,
        notes=score_notes,
        total_conf=total_confidence,
        aggressive=aggressive_entry,
        regime_lbl=market_regime,
        tech_ctx=tech,
    )

    res = {
        "mode": "SHADOW" if (states['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES) else "LIVE",
        "radar_rec": radar_rec,
        "radar_reason": radar_reason,
        "rsi_val": rsi_val,
        "total_confidence": total_confidence,
        "entry_score": float(entry_score),
        "entry_threshold": entry_threshold,
        "aggressive_threshold": aggressive_threshold,
        "aggressive_entry": aggressive_entry,
        "entry_size_factor": entry_size_factor,
        "market_regime": market_regime,
        "regime_cooldown_sec": int(regime_profile['cooldown_sec']),
        "tp_multiplier": float(regime_profile['tp_mult']),
        "sl_multiplier": float(regime_profile['sl_mult']),
        "dynamic_threshold": dynamic_threshold,
        "knn_decision": knn_decision,
        "velocity": float(_velocity),
        "baseline_price": float(min(history[-5:])) if len(history) >= 5 else float(p.get('last', 1.0)) * 0.99,
    }
    print(f"[DEBUG-GATE] {pair} PASSED ALL GATES! Result: {res}", flush=True)

    return res
