"""
entry_targeting.py

ATR-based dynamic TP/SL calculation and execution-mode selection (taker vs maker).
Runs only after all entry gates have passed.
"""

from app.domain.technicals import calculate_atr
from utils.logger import log_message


def calculate_entry_target(bot, p, enriched, gate, adaptive_cfg):
    """
    Computes dynamic TP/SL via ATR and decides taker vs maker mode.

    Returns a dict with targeting data, or None if ATR data is unavailable
    (falls back to K-NN targets in that case — also returned as dict).

    The function never returns None in practice; fallback values from knn_decision
    are always available.
    """
    pair = p.get('pair', 'UNKNOWN')
    rsi_val = gate['rsi_val']
    total_confidence = gate['total_confidence']
    imbalance_ratio = enriched['imbalance_ratio']
    tq_prob = enriched['tq_prob']
    current_vol = p['volatility']
    knn_decision = gate['knn_decision']

    try:
        symbol_atr = pair.upper().replace("IDR", "/IDR")
        ohlcv_atr = bot.exchange.fetch_ohlcv(symbol_atr, timeframe='1m', limit=20)
        if ohlcv_atr:
            highs = [c[2] for c in ohlcv_atr]
            lows = [c[3] for c in ohlcv_atr]
            closes = [c[4] for c in ohlcv_atr]
            atr_val = calculate_atr(highs, lows, closes)

            base_sl_pct = (atr_val * 1.5) / p['last']
            rebound_multiplier = 1.20 if rsi_val < 35 else 1.0
            final_sl_val = max(0.015, base_sl_pct * rebound_multiplier)

            raw_tp = (atr_val * 3.0) / p['last']
            final_dyn_tp = min(0.12, max(final_sl_val * 2.0, raw_tp))
            final_dyn_sl = -min(0.04, final_sl_val)

            log_message(
                f"🎯 [ATR Target] {pair}: TP {final_dyn_tp * 100:.1f}%, "
                f"SL {final_dyn_sl * 100:.1f}% (Rebound: {rebound_multiplier}x)"
            )
        else:
            final_dyn_tp = knn_decision['target_tp']
            final_dyn_sl = knn_decision['target_sl']
    except Exception as e:
        if "does not have market symbol" not in str(e):
            log_message(f"⚠️ ATR Calculation fallback: {e}")
        final_dyn_tp = knn_decision['target_tp']
        final_dyn_sl = knn_decision['target_sl']

    use_taker = (
        total_confidence > 85.0
        and imbalance_ratio > 3.0
        and current_vol < 2.2
        and tq_prob >= (adaptive_cfg['ai_threshold'] + 0.08)
    )
    mode_str = "🚀 TAKER (INSTANT)" if use_taker else "🎣 MAKER (LIMIT EMA-9)"
    log_message(f"⚡ [Execution Mode] {pair}: {mode_str}")

    return {
        "final_dyn_tp": final_dyn_tp,
        "final_dyn_sl": final_dyn_sl,
        "use_taker": use_taker,
    }
