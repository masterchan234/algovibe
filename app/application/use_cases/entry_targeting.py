"""
entry_targeting.py

ATR-based dynamic TP/SL calculation and execution-mode selection (taker vs maker).
Runs only after all entry gates have passed.
"""

import config
from app.domain.technicals import calculate_atr
from utils.logger import log_message
from .trade_execution import _to_ccxt_symbol


_ATR_CANDLES = 20


def _resolve_atr_candles(bot, pair, enriched):
    """Return (highs, lows, closes) 1m candle untuk ATR.

    Urutan sumber:
      1. Cache candle in-memory milik service (brain-inference: `_ohlc_highs/_ohlc_lows`
         + history close) — tanpa network call di hot path sinyal.
      2. REST `fetch_ohlcv` bila bot punya client exchange (monolith / executive-core).
    Mengembalikan tiga list kosong bila tidak ada sumber yang layak, sehingga caller
    jatuh ke target K-NN.
    """
    highs_map = getattr(bot, 'ohlc_highs', None) or getattr(bot, '_ohlc_highs', None) or {}
    lows_map = getattr(bot, 'ohlc_lows', None) or getattr(bot, '_ohlc_lows', None) or {}
    closes = [float(x) for x in list(enriched.get('history') or [])]
    highs = [float(x) for x in list(highs_map.get(pair, []))]
    lows = [float(x) for x in list(lows_map.get(pair, []))]
    # Ketiga deret harus sejajar per-candle; kalau tidak, ATR-nya tidak bermakna.
    if len(closes) >= 15 and len(highs) == len(closes) and len(lows) == len(closes):
        return highs[-_ATR_CANDLES:], lows[-_ATR_CANDLES:], closes[-_ATR_CANDLES:]

    exchange = getattr(bot, 'exchange', None)
    if exchange is not None:
        ohlcv = exchange.fetch_ohlcv(
            _to_ccxt_symbol(pair), timeframe=config.SIGNAL_CANDLE_TIMEFRAME, limit=_ATR_CANDLES
        )
        if ohlcv:
            return [c[2] for c in ohlcv], [c[3] for c in ohlcv], [c[4] for c in ohlcv]
    return [], [], []


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
    tp_mult = float(gate.get('tp_multiplier', 1.0) or 1.0)
    sl_mult = float(gate.get('sl_multiplier', 1.0) or 1.0)

    try:
        highs, lows, closes = _resolve_atr_candles(bot, pair, enriched)
        if closes:
            atr_val = calculate_atr(highs, lows, closes)

            # --- [Multi-Factor Intelligence Scaling] ---
            # 1. Base Volatility Component (ATR)
            # base_risk represents the natural breathing room of the coin
            base_risk_pct = (atr_val * 1.5) / p['last']
            
            # 2. Conviction Scaling (pure technical)
            # Menggunakan tq_prob (technical probability) dan K-NN total_confidence
            # sebagai dua sumber keyakinan independen — tidak ada LLM di sini.
            knn_conf_norm = gate['total_confidence'] / 100.0
            conviction_factor = (tq_prob + knn_conf_norm) / 2.0
            # Stretch multiplier: 0.8x to 1.3x based on conviction
            stretch_mult = 0.8 + (conviction_factor * 0.5)
            
            # 3. Exhaustion & Pressure Factor (RSI & Imbalance)
            # If RSI is high (>65), we are entering exhaustion, tighten TP
            # If Imbalance is high (>2.0), there is strong buying pressure, allow wider TP
            exhaustion_mult = 1.0
            if rsi_val > 65: exhaustion_mult = 0.85
            elif rsi_val < 35: exhaustion_mult = 1.15 # Rebound potential
            
            pressure_mult = 1.0 + (min(2.0, imbalance_ratio) - 1.0) * 0.1 # +/- 10% based on book pressure

            # --- [Final Calculation] ---
            # Stop Loss: Focused on protection, adjusted by volatility and pressure
            # We tighten SL if pressure is against us
            final_sl_val = base_risk_pct * (1.1 if imbalance_ratio < 0.8 else 1.0)
            final_sl_val = max(0.007, min(0.035, final_sl_val)) # Capped for safety
            
            # Take Profit: Focused on alpha, adjusted by conviction and exhaustion
            # TP = ATR_Base * 3.0 * Conviction * Exhaustion * Pressure
            final_dyn_tp = (atr_val * 3.0 / p['last']) * stretch_mult * exhaustion_mult * pressure_mult
            
            # Ensure a healthy Risk-to-Reward (Min 1.2, Max 4.0)
            final_dyn_tp = max(final_sl_val * 1.2, min(0.15, final_dyn_tp))
            final_dyn_sl = -final_sl_val

            log_message(
                f"[BRAIN] [INTELLIGENT TARGET] {pair}: TP {final_dyn_tp * 100:.2f}%, "
                f"SL {final_dyn_sl * 100:.2f}% | Conviction: {conviction_factor:.2f} "
                f"(TQ={tq_prob:.2f}, KNN={knn_conf_norm:.2f}), RSI: {rsi_val:.1f}"
            )
        else:
            final_dyn_tp = knn_decision['target_tp']
            final_dyn_sl = knn_decision['target_sl']
    except Exception as e:
        if "does not have market symbol" not in str(e):
            log_message(f"[WARN] ATR Calculation fallback: {e}")
        final_dyn_tp = knn_decision['target_tp']
        final_dyn_sl = knn_decision['target_sl']

    # Regime-aware overlay (TRENDING/SIDEWAYS/VOLATILE)
    final_dyn_tp = max(0.006, min(0.20, float(final_dyn_tp) * tp_mult))
    final_dyn_sl = -max(0.005, min(0.050, abs(float(final_dyn_sl)) * sl_mult))

    # [Scalp Optimization] Selalu gunakan TAKER (Market Order) untuk kecepatan eksekusi
    use_taker = True
    mode_str = "[START] TAKER (INSTANT MARKET BUY)"
    log_message(f"[FAST] [Execution Mode] {pair}: {mode_str}")

    return {
        "final_dyn_tp": final_dyn_tp,
        "final_dyn_sl": final_dyn_sl,
        "use_taker": use_taker,
    }
