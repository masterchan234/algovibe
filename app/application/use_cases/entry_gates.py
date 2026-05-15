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
    calculate_atr, compute_adaptive_thresholds,
    _MACD_NORM_FACTOR,
)
from utils.logger import log_message

_BREAK = "BREAK"  # sentinel value returned to caller


def _derive_radar_recommendation(p, enriched, adaptive_cfg):
    """Satu sumber rekomendasi untuk Radar + gate entry bot (Strict Mode)."""
    signal_v = str(enriched.get('signal_verdict', 'HOLD') or 'HOLD').upper()
    trend = str(enriched.get('trend', 'N/A') or 'N/A').upper()
    breakout = enriched.get('breakout', False)
    score = float(p.get('score', 0) or 0)
    tq_prob = float(enriched.get('tq_prob', 0) or 0)
    imbalance_ratio = float(enriched.get('imbalance_ratio', 1.0) or 1.0)
    tech_threshold = float(adaptive_cfg.get('tech_threshold', 0.6) or 0.6)

    # ── 1. Sell Block ──
    if signal_v == 'SELL' and (score <= 45 or trend != 'UP'):
        return "SELL", f"signal={signal_v}, score={score:.0f}, trend={trend}"

    # ── 2. BUY Filter (Sync with check_entry_gates) ──
    # Radar hanya boleh bilang BUY jika kriteria trend dan probabilitas terpenuhi
    is_loose = bool(config.DRY_RUN_LOOSE_GATES)
    is_trend_ok = breakout or trend == "UP" or (is_loose and trend == "SIDEWAYS")
    
    # Mirroring Step 9: Dynamic threshold based on volatility
    current_vol = float(p.get('volatility', 0) or 0)
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
    
    # tq_prob is 0.0-1.0, threshold is 0-100
    is_prob_ok = (tq_prob * 100) >= base_thresh

    if signal_v == 'BUY' and is_trend_ok and is_prob_ok:
        return "BUY", (
            f"signal={signal_v}, tq_prob={tq_prob * 100:.0f}%, trend={trend}, "
            f"vol={current_vol:.2f}%"
        )

    # ── 3. Fallback to HOLD ──
    reason = "HOLD"
    if not is_trend_ok:
        reason = f"Trend {trend} (no breakout)"
    elif not is_prob_ok:
        reason = f"Prob {tq_prob*100:.0f}% < Thresh {base_thresh:.0f}%"

    return "HOLD", f"signal={signal_v} | {reason}"


def check_entry_gates(bot, p, enriched, regime, adaptive_cfg, open_positions):
    """
    Runs all pre-entry gate checks in sequence.

    Returns:
        dict   — all gates passed; contains scoring data for downstream use
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
        print(f"[DEBUG-GATE] {pair} REJECTED: Cooldown aktif ({remaining_min}m)", flush=True)
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

    # [DIAGNOSTIC] Log every tracked pair to stdout
    print(f"[DEBUG-GATE] Evaluating {pair} | Trend: {trend} | Breakout: {breakout} | Prob: {prob_display}", flush=True)

    log_message(
        f"Track: {pair:10} | Regime: {regime:8} | Trend: {trend:9} | "
        f"PB: {str(breakout):5} | TQ_Prob: {prob_display}{boost_str}"
    )

    # ── 4. Multi-Confluence Technical Gate ────────────────────────────────
    # Kita tidak hanya cek RSI — harus ada konfirmasi multi-indikator.
    # Sertakan volume per-candle dari OHLC backfill jika tersedia via BotMock,
    # sehingga OBV Slope (w=1.5) dan Volume Ratio (w=1.5) dapat berfungsi aktif.
    _candle_vols = list(getattr(bot, 'ohlc_vols', {}).get(pair, []))
    tech = get_technical_summary(history, _candle_vols if len(_candle_vols) == len(history) else None)
    depth_data = db.get_market_depth(pair)
    if depth_data['bids'] and depth_data['asks']:
        total_bid = sum(b['vol'] for b in depth_data['bids'])
        total_ask = sum(a['vol'] for a in depth_data['asks'])
        total_depth = total_bid + total_ask
        if total_depth > 0:
            tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)
    _sector_eg = config.SECTOR_MAP.get(pair.lower())
    _adaptive_db = db.get_coin_indicator_setup(pair)
    _adaptive_eg = _adaptive_db or (compute_adaptive_thresholds(history) if len(history) >= 30 else None)
    _vol_idr_eg = float((p or {}).get('vol_idr') or 0) or None
    tech_sig = derive_trade_signal(tech, sector=_sector_eg, adaptive_thresholds=_adaptive_eg, vol_idr_24h=_vol_idr_eg)

    # Block jika sinyal SELL dengan skor cukup (multi-confluence, bukan hanya RSI).
    # Threshold diturunkan dari 8.0 → 5.0: skor 5/16.5 sudah cukup membuktikan tekanan jual.
    # Sebelumnya sell_score 7.9 hanya memicu warning dan bot tetap masuk; ini sudah diperbaiki.
    # [LATENCY-OPTIMIZATION] Short-circuit if SELL verdict and NOT in loose gates mode
    if not config.DRY_RUN_LOOSE_GATES and tech_sig['verdict'] == 'SELL' and tech_sig.get('sell_score', 0) >= 5.0:
        msg = (
            f"⛔ SELL BLOCK: score={tech_sig.get('sell_score',0):.1f} "
            f"— {tech_sig['reason'][:80]}"
        )
        print(f"[DEBUG-GATE] {pair} REJECTED: SELL BLOCK (score={tech_sig.get('sell_score',0):.1f})", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None
    elif tech_sig['verdict'] == 'SELL':
        print(f"[DEBUG-GATE] {pair} WARNING: SELL-WARN but continuing (Loose Gates={config.DRY_RUN_LOOSE_GATES})", flush=True)
        log_message(
            f"⚠️  SELL-WARN [{pair}]: sell_score={tech_sig.get('sell_score',0):.1f} "
            f"buy_score={tech_sig.get('buy_score',0):.1f} — masih evaluasi lanjut."
        )

    # ── 4b. ATR Volatility Filter ─────────────────────────────────────────
    # Skip entry jika ATR > 3% harga — volatilitas terlalu tinggi, spread lebar,
    # slippage besar, TP/SL sering kena sebelum waktunya.
    # Loose gates: batas dinaikkan ke 4.5% agar tetap bisa trading di kondisi moderat.
    # Jika bot memiliki data OHLC H/L nyata (dari backfill), gunakan True ATR.
    # Fallback ke close-only approximation jika H/L tidak tersedia.
    if len(history) >= 15:
        try:
            _prices_arr = history[-15:]
            # Ambil H/L nyata dari backfill jika tersedia via BotMock
            _raw_highs = list(getattr(bot, 'ohlc_highs', {}).get(pair, []))
            _raw_lows  = list(getattr(bot, 'ohlc_lows',  {}).get(pair, []))
            if len(_raw_highs) >= 15 and len(_raw_lows) >= 15:
                _atr = calculate_atr(_raw_highs[-15:], _raw_lows[-15:], _prices_arr, period=14)
            else:
                _atr = calculate_atr(_prices_arr, _prices_arr, _prices_arr, period=14)
            _last = float(p.get('last', 1.0)) or 1.0
            _atr_pct = (_atr / _last) * 100.0
            _atr_limit = 4.5 if config.DRY_RUN_LOOSE_GATES else 3.0
            if _atr_pct > _atr_limit:
                msg = (
                    f"⛔ ATR-FILTER: ATR {_atr_pct:.2f}% > batas {_atr_limit:.1f}% "
                    f"— volatilitas terlalu tinggi, risiko slippage."
                )
                print(f"[DEBUG-GATE] {pair} REJECTED: ATR {_atr_pct:.2f}% > {_atr_limit:.1f}%", flush=True)
                log_message(f"{msg} [{pair}]")
                save_gate_state(msg)
                return None
        except Exception:
            pass  # Jika ATR gagal dihitung, biarkan lewat

    # ── 4c. EMA-50 Trend Filter ───────────────────────────────────────────
    # Hanya masuk jika harga penutupan terakhir > EMA-50 (trend makro naik).
    # Ini mencegah entry di pertengahan downtrend besar hanya karena EMA-9 > EMA-21 sesaat.
    # Jika data < 50 candle: filter dilewati (tidak cukup histori untuk konfirmasi).
    # Loose gates: filter dilewati agar lebih banyak opportunity.
    if not config.DRY_RUN_LOOSE_GATES and len(history) >= 50:
        try:
            _ema50 = calculate_ema(history, 50)
            _last_price = float(p.get('last', 0.0))
            if _last_price > 0 and _last_price < _ema50:
                _gap_pct = (_ema50 - _last_price) / _ema50 * 100.0
                msg = (
                    f"⛔ EMA50-FILTER: harga {_last_price:.4f} < EMA50 {_ema50:.4f} "
                    f"(gap -{_gap_pct:.2f}%) — trend makro masih turun."
                )
                print(f"[DEBUG-GATE] {pair} REJECTED: EMA50 filter (price {_last_price:.4f} < EMA50 {_ema50:.4f})", flush=True)
                log_message(f"{msg} [{pair}]")
                save_gate_state(msg)
                return None
        except Exception:
            pass  # Jika EMA50 gagal dihitung, biarkan lewat

    # ── 5. Technical probability + imbalance threshold ────────────────────────
    if tq_prob < 0:
        msg = "WARMUP: belum cukup data untuk prediksi teknikal."
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    is_loose = bool(config.DRY_RUN_LOOSE_GATES)
    tech_gate_threshold = float(adaptive_cfg['tech_threshold'])
    imb_gate_threshold = float(STRONG_IMBALANCE_RATIO)
    if is_loose:
        tech_gate_threshold = max(0.25, tech_gate_threshold - 0.10)
        imb_gate_threshold = max(1.10, imb_gate_threshold - 0.25)

    tech_ready = tq_prob >= tech_gate_threshold
    has_strong_imb = imbalance_ratio >= imb_gate_threshold
    if not tech_ready and not has_strong_imb:
        msg = (
            f"SKIP: Tech prob {tq_prob * 100:.0f}% < threshold "
            f"{tech_gate_threshold * 100:.0f}% "
            f"& imbalance {imbalance_ratio:.2f}x < {imb_gate_threshold:.2f}x"
        )
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 6. Trend / breakout requirement ───────────────────────────────────
    if not breakout and trend != "UP":
        # Loose mode: boleh SIDEWAYS jika probabilitas + orderbook masih kuat.
        sideway_relaxed_ok = (
            is_loose
            and trend == "SIDEWAYS"
            and tq_prob >= max(tech_gate_threshold, 0.45)
            and imbalance_ratio >= 1.05
        )
        if not sideway_relaxed_ok:
            msg = f"SKIP: Tidak ada pullback & trend bukan UP (trend={trend})"
            print(f"[DEBUG-GATE] {pair} REJECTED: Trend {trend} (no breakout)", flush=True)
            log_message(f"{msg} [{pair}]")
            save_gate_state(msg)
            return None

    # ── 7. Radar recommendation guard (sinkron dashboard <-> eksekusi bot) ──
    if radar_rec == "SELL":
        msg = f"🚫 RADAR BLOCK: rekomendasi SELL ({radar_reason})"
        print(f"[DEBUG-GATE] {pair} REJECTED: RADAR SELL ({radar_reason})", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 9. Dynamic threshold based on volatility + trend ──────────────────
    current_vol = p['volatility']
    is_loose = bool(config.DRY_RUN_LOOSE_GATES)

    # OFF: ideal (lebih ketat), ON: lebih longgar tapi tetap terkontrol.
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
        print(f"[DEBUG-GATE] {pair} REJECTED: K-NN AVOID", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    base_confidence = float(knn_decision.get('confidence', 0.0))
    # OFF: ideal floor; ON: diturunkan tapi tidak nol agar kualitas tetap terjaga.
    min_base_conf = 50.0 if current_vol <= 2.0 else 55.0
    if config.DRY_RUN_LOOSE_GATES:
        min_base_conf = 35.0 if current_vol <= 2.0 else 40.0

    if base_confidence < min_base_conf:
        msg = f"SKIP: base_conf {base_confidence:.1f}% < floor {min_base_conf:.1f}%"
        print(f"[DEBUG-GATE] {pair} REJECTED: Base Confidence {base_confidence:.1f}% < Floor {min_base_conf:.1f}%", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    # ── 13. Final Confidence threshold ────────────────────────────────────
    boost_credit = min(scalper_boost, 6.0)
    total_confidence = base_confidence + boost_credit

    res = {
        "mode": "SHADOW" if is_shadow_mode else "LIVE",
        "radar_rec": radar_rec,
        "radar_reason": radar_reason,
        "rsi_val": rsi_val,
        "total_confidence": total_confidence,
        "dynamic_threshold": dynamic_threshold,
        "knn_decision": knn_decision,
    }
    print(f"[DEBUG-GATE] {pair} PASSED ALL GATES! Result: {res}", flush=True)

    return res
