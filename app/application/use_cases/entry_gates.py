"""
entry_gates.py

All pre-entry gate checks for a candidate pair:
  - open position / blacklist guard
  - cooldown guard
  - AI probability and imbalance threshold
  - trend / breakout requirement
  - LLM verdict queue and policy
  - K-NN pattern scoring and confidence threshold

Returns a gate result dict when all gates pass, None to skip the pair,
or the string "BREAK" to abort the entire candidate loop (consecutive-loss halt).
"""

import time

import numpy as np

from config import (
    MAX_CONSECUTIVE_LOSSES,
    STRONG_IMBALANCE_RATIO,
    DRY_RUN_LOOSE_GATES,
)
from app.infrastructure.database import db
from app.domain.technicals import calculate_rsi, derive_trade_signal, get_technical_summary
from utils.logger import log_message

_BREAK = "BREAK"  # sentinel value returned to caller


def check_entry_gates(bot, p, enriched, regime, adaptive_cfg, open_positions):
    """
    Runs all pre-entry gate checks in sequence.

    Returns:
        dict   — all gates passed; contains scoring & LLM data for downstream use
        None   — pair should be skipped (continue)
        "BREAK"— consecutive-loss halt; caller should break the pair loop
    """
    pair = p.get('pair', 'UNKNOWN')
    trend = enriched['trend']
    breakout = enriched['breakout']
    history = enriched['history']
    tq_prob = enriched['tq_prob']
    whale_boost = enriched['whale_boost']
    sector_boost = enriched['sector_boost']
    imbalance_ratio = enriched['imbalance_ratio']

    # ── 1. Skip pairs already held or blacklisted ─────────────────────────
    if pair in open_positions or pair in bot.blacklist:
        return None

    # ── 2. Cooldown guard ─────────────────────────────────────────────────
    cooldown_until_raw = db.get_state(f"pair_cooldown_until_{pair}", "0")
    try:
        cooldown_until = float(cooldown_until_raw or 0)
    except Exception:
        cooldown_until = 0.0
    now_ts = time.time()
    if cooldown_until > now_ts:
        remaining_min = int(max(1, (cooldown_until - now_ts) / 60))
        log_message(f"SKIP [{pair}]: pair cooldown aktif ({remaining_min}m lagi).")
        return None

    # ── 3. Learning-adjusted score + tracking log ──────────────────────────
    features = {
        "ai_prob": tq_prob,
        "whale_boost": whale_boost - 1.0,
        "sector_sentiment": bot.oracle.get_sector_sentiment_val(pair),
        "imbalance": imbalance_ratio,
    }
    final_score = bot.oracle.apply_learning_to_score(p['score'], features)

    prob_display = f"{tq_prob * 100:.0f}%" if tq_prob >= 0 else "WARMUP"
    boost_str = ""
    if whale_boost > 1.0:
        boost_str += " 🐳"
    if sector_boost > 1.0:
        boost_str += " 🔥"
    if final_score > p['score']:
        boost_str += " 📈"
    if final_score < p['score']:
        boost_str += " 📉"

    log_message(
        f"Track: {pair:10} | Regime: {regime:8} | Trend: {trend:9} | "
        f"PB: {str(breakout):5} | TQ_Prob: {prob_display}{boost_str}"
    )

    # ── 4. Overbought / sell-signal block ──────────────────────────────────
    tech = get_technical_summary(history)
    depth_data = db.get_market_depth(pair)
    if depth_data['bids'] and depth_data['asks']:
        total_bid = sum(b['vol'] for b in depth_data['bids'])
        total_ask = sum(a['vol'] for a in depth_data['asks'])
        total_depth = total_bid + total_ask
        if total_depth > 0:
            tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)
    tech_sig = derive_trade_signal(tech)

    if tech_sig['verdict'] == 'SELL' and tech.get('rsi', 50) > 72:
        log_message(f"⛔ OVERBOUGHT BLOCK [{pair}]: RSI={tech.get('rsi'):.0f} — {tech_sig['reason']}")
        return None
    elif tech_sig['verdict'] == 'SELL':
        log_message(f"⚠️  SELL-WARN [{pair}]: {tech_sig['reason']} — tetap evaluasi K-NN.")

    # ── 5. AI probability + imbalance threshold ────────────────────────────
    if tq_prob < 0:
        log_message(f"WARMUP: {pair} belum cukup data untuk prediksi AI.")
        return None

    ai_ready = tq_prob >= adaptive_cfg['ai_threshold']
    has_strong_imb = imbalance_ratio >= STRONG_IMBALANCE_RATIO
    if not DRY_RUN_LOOSE_GATES and not ai_ready and not has_strong_imb:
        log_message(
            f"SKIP [{pair}]: AI prob {tq_prob * 100:.0f}% < threshold "
            f"{adaptive_cfg['ai_threshold'] * 100:.0f}% "
            f"& imbalance {imbalance_ratio:.2f}x < {STRONG_IMBALANCE_RATIO}x"
        )
        return None

    # ── 6. Trend / breakout requirement ───────────────────────────────────
    if not DRY_RUN_LOOSE_GATES and not breakout and trend != "UP":
        log_message(f"SKIP [{pair}]: Tidak ada pullback & trend bukan UP (trend={trend})")
        return None

    # ── 7. LLM verdict — queue if missing, block until ready ──────────────
    # [Scalp Optimization] Jika AI prob sangat tinggi (>55%), bypass LLM untuk entry instan.
    is_fast_track = tq_prob >= 0.55
    
    llm_verdict = bot.oracle.get_pair_trade_verdict(pair, max_age_minutes=15)
    if not llm_verdict:
        if DRY_RUN_LOOSE_GATES or is_fast_track:
            # Pakai default BUY agar tidak stuck
            llm_verdict = {"verdict": "BUY", "confidence": 60, "reason": "FAST_TRACK_SCALP" if is_fast_track else "DRY_RUN_LOOSE"}
        else:
            queued = bot.oracle.request_trade_verdict(
                pair=pair,
                history=history,
                tech=tech,
                imbalance_ratio=imbalance_ratio,
                tq_prob=tq_prob,
            )
            if queued:
                log_message(f"⏳ LLM verdict queued [{pair}] — menunggu keputusan AI sebelum entry.")
            return None

    llm_v = llm_verdict.get('verdict', 'HOLD')
    llm_c = float(llm_verdict.get('confidence', 0))
    llm_r = llm_verdict.get('reason', '')

    # ── 8. Dynamic threshold based on volatility + trend ──────────────────
    current_vol = p['volatility']
    dynamic_threshold = 50.0 # [SCALP] Lowered from 57.0
    if current_vol > 2.0:
        dynamic_threshold = 60.0 # [SCALP] Lowered from 68.0
    elif current_vol < 0.6:
        dynamic_threshold = 45.0 # [SCALP] Lowered from 54.0
    if trend != "UP":
        dynamic_threshold += 4.0

    entry_policy = bot.oracle.get_entry_policy(regime, dynamic_threshold)
    dynamic_threshold = entry_policy['dynamic_threshold']

    # ── 9. LLM verdict policy gate ────────────────────────────────────────
    if llm_v == 'AVOID' and llm_c >= entry_policy['llm_avoid_block_conf']:
        log_message(f"🚫 LLM BLOCK [{pair}] {llm_c:.0f}%: {llm_r}")
        return None
    if llm_v == 'HOLD' and llm_c >= entry_policy['llm_hold_block_conf']:
        log_message(f"🕒 LLM HOLD [{pair}] {llm_c:.0f}%: {llm_r}")
        return None

    llm_bonus = 0.0
    if llm_v == 'BUY' and llm_c >= entry_policy['llm_buy_min_conf']:
        llm_bonus = min(8.0, (llm_c - 55.0) * 0.2)

    # ── 10. Consecutive-loss halt ─────────────────────────────────────────
    bot.consecutive_losses = int(db.get_state("consecutive_losses", 0))
    if bot.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
        log_message(f"PAUSE: Hit max losses ({MAX_CONSECUTIVE_LOSSES}).")
        time.sleep(60)
        return _BREAK

    # ── 11. K-NN pattern scoring ──────────────────────────────────────────
    rsi_val = calculate_rsi(history)

    if len(history) >= 20:
        prices = np.array(history, dtype=float)
        alpha = 2 / (20 + 1)
        ema_20 = prices[0]
        for price_v in prices[1:]:
            ema_20 = (price_v * alpha) + (ema_20 * (1 - alpha))
        ema_dist = ((ema_20 - p['last']) / ema_20) * 100
    else:
        ema_dist = 0.0

    current_dna = {
        "ai_prob": tq_prob,
        "whale_boost": whale_boost,
        "sector_sentiment": sector_boost,
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
        log_message(f"🚫 [K-NN Avoid] {pair}: pola historis buruk, skip.")
        return None

    base_confidence = float(knn_decision.get('confidence', 0.0))
    min_base_conf = 35.0 if current_vol <= 2.0 else 40.0 # [SCALP] Lowered from 45/50
    if DRY_RUN_LOOSE_GATES:
        min_base_conf = 0.0  # bypass K-NN floor saat testing
    boost_credit = min(scalper_boost, 6.0)
    total_confidence = base_confidence + boost_credit + llm_bonus

    if base_confidence < min_base_conf:
        log_message(
            f"SKIP [{pair}]: base_conf {base_confidence:.1f}% < floor {min_base_conf:.1f}% "
            f"(vol={current_vol:.2f}%, boost_raw={scalper_boost:.1f}%)"
        )
        return None

    # ── 12. Confidence threshold ──────────────────────────────────────────
    effective_threshold = 0.0 if DRY_RUN_LOOSE_GATES else dynamic_threshold
    if total_confidence < effective_threshold:
        if knn_decision['verdict'] == "AVOID":
            log_message(f"🚫 [K-NN Avoid] {pair}: Pola identik dengan Falling Knife masa lalu. Skip.")
        return None

    log_message(
        f"🚀 [Scalper Boost] {pair}: Base {base_confidence:.1f}% + BoostCredit {boost_credit:.1f}% "
        f"+ LLM {llm_bonus:.1f}% ({llm_v} {llm_c:.0f}%) = {total_confidence:.1f}% "
        f"(Thresh: {dynamic_threshold:.1f}%)"
    )

    return {
        "llm_v": llm_v,
        "llm_c": llm_c,
        "llm_r": llm_r,
        "llm_bonus": llm_bonus,
        "rsi_val": rsi_val,
        "total_confidence": total_confidence,
        "dynamic_threshold": dynamic_threshold,
        "knn_decision": knn_decision,
    }
