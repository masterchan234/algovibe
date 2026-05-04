"""
entry_gates.py

All pre-entry gate checks for a candidate pair:
  - open position / blacklist guard
  - cooldown guard
  - trend / breakout requirement
  - multi-confluence technical gate
  - technical probability threshold (pure, no AI bias)
  - K-NN pattern scoring and confidence threshold

All AI/LLM influence has been removed from the decision path.
Every gate is based exclusively on price-derived technical indicators.

Returns a gate result dict when all gates pass, None to skip the pair,
or the string "BREAK" to abort the entire candidate loop (consecutive-loss halt).
"""

import time

import numpy as np

from config import (
    MAX_CONSECUTIVE_LOSSES,
    STRONG_IMBALANCE_RATIO,
)
import config
from app.infrastructure.database import db
from app.domain.technicals import (
    calculate_rsi, derive_trade_signal, get_technical_summary,
    calculate_macd, calculate_bollinger_bands, calculate_stochastic_rsi, calculate_ema,
    _MACD_NORM_FACTOR,
)
from utils.logger import log_message

_BREAK = "BREAK"  # sentinel value returned to caller


def _derive_radar_recommendation(p, enriched, adaptive_cfg):
    """Satu sumber rekomendasi untuk Radar + gate entry bot."""
    signal_v = str(enriched.get('signal_verdict', 'HOLD') or 'HOLD').upper()
    trend = str(enriched.get('trend', 'N/A') or 'N/A').upper()
    score = float(p.get('score', 0) or 0)
    tq_prob = float(enriched.get('tq_prob', 0) or 0)
    imbalance_ratio = float(enriched.get('imbalance_ratio', 1.0) or 1.0)
    tech_threshold = float(adaptive_cfg.get('tech_threshold', 0.6) or 0.6)

    if signal_v == 'SELL' and (score <= 45 or trend != 'UP'):
        return "SELL", f"signal={signal_v}, score={score:.0f}, trend={trend}"

    buy_ready = (
        signal_v == 'BUY' and (
            tq_prob >= max(0.35, tech_threshold * 0.9)
            or score >= 60
            or (trend == 'UP' and imbalance_ratio >= 1.2)
        )
    )
    if buy_ready:
        return "BUY", (
            f"signal={signal_v}, tq_prob={tq_prob * 100:.0f}%, score={score:.0f}, "
            f"imbalance={imbalance_ratio:.2f}x"
        )

    return "HOLD", f"signal={signal_v}, tq_prob={tq_prob * 100:.0f}%, score={score:.0f}"


def check_entry_gates(bot, p, enriched, regime, adaptive_cfg, open_positions):
    """
    Runs all pre-entry gate checks in sequence.

    Returns:
        dict   — all gates passed; contains scoring & LLM data for downstream use
        None   — pair should be skipped (continue)
        "BREAK"— consecutive-loss halt; caller should break the pair loop
    """
    from datetime import datetime
    
    pair = p.get('pair', 'UNKNOWN')

    def save_gate_state(msg):
        db.save_state(f"gate_skip_{pair}", msg)
        db.save_state(f"gate_ts_{pair}", datetime.now().isoformat())

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

    # ── 3. Radar recommendation + tracking log ────────────────────────────
    # Score berdasarkan ranking teknikal murni — tidak ada learning adjustment AI.
    final_score = p['score']

    radar_rec, radar_reason = _derive_radar_recommendation(p, enriched, adaptive_cfg)
    db.save_state(f"radar_reco_{pair}", radar_rec)
    db.save_state(f"radar_reco_reason_{pair}", radar_reason)

    prob_display = f"{tq_prob * 100:.0f}%" if tq_prob >= 0 else "WARMUP"
    boost_str = ""
    if whale_boost > 1.0:
        boost_str += " 🐳"
    if sector_boost > 1.0:
        boost_str += " 🔥"

    log_message(
        f"Track: {pair:10} | Regime: {regime:8} | Trend: {trend:9} | "
        f"PB: {str(breakout):5} | TQ_Prob: {prob_display}{boost_str}"
    )

    # ── 4. Multi-Confluence Technical Gate ────────────────────────────────
    # Kita tidak hanya cek RSI — harus ada konfirmasi multi-indikator.
    tech = get_technical_summary(history)
    depth_data = db.get_market_depth(pair)
    if depth_data['bids'] and depth_data['asks']:
        total_bid = sum(b['vol'] for b in depth_data['bids'])
        total_ask = sum(a['vol'] for a in depth_data['asks'])
        total_depth = total_bid + total_ask
        if total_depth > 0:
            tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)
    tech_sig = derive_trade_signal(tech)

    # Block jika sinyal SELL dengan skor cukup (multi-confluence, bukan hanya RSI).
    # Threshold diturunkan dari 8.0 → 5.0: skor 5/16.5 sudah cukup membuktikan tekanan jual.
    # Sebelumnya sell_score 7.9 hanya memicu warning dan bot tetap masuk; ini sudah diperbaiki.
    if tech_sig['verdict'] == 'SELL' and tech_sig.get('sell_score', 0) >= 5.0:
        msg = (
            f"⛔ SELL BLOCK: score={tech_sig.get('sell_score',0):.1f} "
            f"— {tech_sig['reason'][:80]}"
        )
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None
    elif tech_sig['verdict'] == 'SELL':
        log_message(
            f"⚠️  SELL-WARN [{pair}]: sell_score={tech_sig.get('sell_score',0):.1f} "
            f"buy_score={tech_sig.get('buy_score',0):.1f} — masih evaluasi lanjut."
        )

    # ── 5. Technical probability + imbalance threshold ────────────────────────
    if tq_prob < 0:
        msg = "WARMUP: belum cukup data untuk prediksi teknikal."
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    tech_ready = tq_prob >= adaptive_cfg['tech_threshold']
    has_strong_imb = imbalance_ratio >= STRONG_IMBALANCE_RATIO
    if not config.DRY_RUN_LOOSE_GATES and not tech_ready and not has_strong_imb:
        msg = (
            f"SKIP: Tech prob {tq_prob * 100:.0f}% < threshold "
            f"{adaptive_cfg['tech_threshold'] * 100:.0f}% "
            f"& imbalance {imbalance_ratio:.2f}x < {STRONG_IMBALANCE_RATIO}x"
        )
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 6. Trend / breakout requirement ───────────────────────────────────
    if not config.DRY_RUN_LOOSE_GATES and not breakout and trend != "UP":
        msg = f"SKIP: Tidak ada pullback & trend bukan UP (trend={trend})"
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 7. Radar recommendation guard (sinkron dashboard <-> eksekusi bot) ──
    if radar_rec == "SELL":
        msg = f"🚫 RADAR BLOCK: rekomendasi SELL ({radar_reason})"
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 9. Dynamic threshold based on volatility + trend ──────────────────
    current_vol = p['volatility']
    # Threshold dinaikkan (50→55, 60→65) agar bot hanya masuk dengan confidence tinggi.
    # Dasar: break-even memerlukan win rate ≥ 65%, threshold harus mencerminkan ini.
    dynamic_threshold = 55.0 # [PROFIT-FOCUS] Dinaikkan dari 50.0
    if current_vol > 2.0:
        dynamic_threshold = 65.0 # [PROFIT-FOCUS] Dinaikkan dari 60.0
    elif current_vol < 0.6:
        dynamic_threshold = 50.0 # Volatilitas sangat rendah: threshold moderat
    if trend != "UP":
        dynamic_threshold += 4.0

    entry_policy = bot.oracle.get_entry_policy(regime, dynamic_threshold)
    dynamic_threshold = entry_policy['dynamic_threshold']

    # ── 10. Consecutive-loss halt / Shadow Recovery ─────────────────────────
    bot.consecutive_losses = int(db.get_state("consecutive_losses", 0))
    is_shadow_mode = bot.consecutive_losses >= MAX_CONSECUTIVE_LOSSES
    if is_shadow_mode:
        msg = f"🛰️  GLOBAL PAUSE ({bot.consecutive_losses} losses): Evaluating for SHADOW PROBE."
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)

    # ── 11. K-NN pattern scoring ──────────────────────────────────────────
    rsi_val = calculate_rsi(history)

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
        "tq_prob": tq_prob,             # technical probability (pure)
        "bb_pct": tech.get('bb_pct', 0.5),          # Bollinger %B (posisi harga di band)
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
        msg = "🚫 [K-NN Avoid]: pola historis buruk, skip."
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    base_confidence = float(knn_decision.get('confidence', 0.0))
    # Floor dinaikkan: 35%/40% → 50%/55%.
    # Break-even memerlukan win rate ≥ 65%; floor 50% adalah batas minimum aman.
    min_base_conf = 50.0 if current_vol <= 2.0 else 55.0
    if config.DRY_RUN_LOOSE_GATES:
        min_base_conf = 0.0  

    if base_confidence < min_base_conf:
        msg = f"SKIP: base_conf {base_confidence:.1f}% < floor {min_base_conf:.1f}%"
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 13. Final Confidence threshold ────────────────────────────────────
    boost_credit = min(scalper_boost, 6.0)
    total_confidence = base_confidence + boost_credit

    effective_threshold = 0.0 if config.DRY_RUN_LOOSE_GATES else dynamic_threshold
    if total_confidence < effective_threshold:
        msg = f"SKIP: Confidence {total_confidence:.1f}% < threshold {effective_threshold:.1f}%"
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    log_message(
        f"🚀 [Entry Ready] {pair}: Tech {base_confidence:.1f}% + Boost {boost_credit:.1f}% "
        f"= {total_confidence:.1f}% (Thresh: {dynamic_threshold:.1f}%)"
    )

    return {
        "mode": "SHADOW" if is_shadow_mode else "LIVE",
        "radar_rec": radar_rec,
        "radar_reason": radar_reason,
        "rsi_val": rsi_val,
        "total_confidence": total_confidence,
        "dynamic_threshold": dynamic_threshold,
        "knn_decision": knn_decision,
    }
