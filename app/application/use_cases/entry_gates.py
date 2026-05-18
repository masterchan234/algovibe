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

    #  1. Sell Block 
    if signal_v == 'SELL' and (score <= 45 or trend != 'UP'):
        return "SELL", f"signal={signal_v}, score={score:.0f}, trend={trend}"

    #  2. BUY Filter (Sync with check_entry_gates) 
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

    #  3. Fallback to HOLD 
    reason = "HOLD"
    if not is_trend_ok:
        reason = f"Trend {trend} (no breakout)"
    elif not is_prob_ok:
        reason = f"Prob {tq_prob*100:.0f}% < Thresh {base_thresh:.0f}%"

    return "HOLD", f"signal={signal_v} | {reason}"


# Tabel override untuk koin yang nama basenya berbeda antara Indodax dan Binance.
# Format: {indodax_pair_lower: binance_symbol_lower}
_INDODAX_TO_BINANCE_OVERRIDE = {
    "rndridr":  "renderusdt",   # Render Network: Indodax=RNDR, Binance=RENDER
    "wbtcidr":  "wbtcusdt",     # Wrapped BTC: konsisten
    "kndyidr":  "",             # Hanya listing lokal, tidak ada di Binance
}


def _get_binance_symbol(indodax_pair: str) -> str:
    """Helper to dynamically map Indodax pair to lowercase Binance counterpart.
    
    Mendukung tabel override eksplisit untuk koin yang nama basenya berbeda
    antara Indodax dan Binance (contoh: rndridr → renderusdt).
    Jika pair tidak ada mapping Binance, kembalikan string kosong.
    """
    pair_lower = indodax_pair.lower()
    # Cek override eksplisit lebih dulu
    if pair_lower in _INDODAX_TO_BINANCE_OVERRIDE:
        return _INDODAX_TO_BINANCE_OVERRIDE[pair_lower]
    # Generic: strip akhiran 'idr', tambah 'usdt'
    if pair_lower.endswith("idr"):
        base = pair_lower[:-3]
        return f"{base}usdt"
    return ""


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

    #  1. Skip pairs already held or blacklisted 
    if pair in open_positions or pair in bot.blacklist:
        return None

    #  2. Cooldown guard 
    cooldown_until_raw = db.get_state(f"pair_cooldown_until_{pair}", "0")
    try:
        cooldown_until = float(cooldown_until_raw or 0)
    except Exception:
        cooldown_until = 0.0
    now_ts = time.time()
    if cooldown_until > now_ts:
        log_message(f"[GATE] {pair} REJECTED: Cooldown active until {cooldown_until}")
        return None

    #  3. Radar recommendation + tracking log 
    final_score = p['score']

    radar_rec, radar_reason = _derive_radar_recommendation(p, enriched, adaptive_cfg)
    db.save_state(f"radar_reco_{pair}", radar_rec)
    db.save_state(f"radar_reco_reason_{pair}", radar_reason)

    prob_display = f"{tq_prob * 100:.0f}%" if tq_prob >= 0 else "WARMUP"
    
    log_message(
        f"[GATE] [TRACKING] {pair}: recommendation={radar_rec} | reason={radar_reason}"
    )

    #  Binance Lead-Lag / Latency Arbitrage & Toxic-Flow Guard
    binance_return = 0.0
    try:
        binance_symbol = _get_binance_symbol(pair)

        # Determine dynamic Tier (baca dari config yang sudah diimport di module level)
        pair_lower = pair.lower()

        tier = 3
        if pair_lower in getattr(config, 'TIER_1_PAIRS', set()):
            tier = 1
        elif pair_lower in getattr(config, 'TIER_2_PAIRS', set()):
            tier = 2

        # TIER 3 (Pure Technical / Domestik): Bypass latency gate completely to save resources!
        if tier == 3:
            binance_symbol = ""  # Disables further Binance checks

        if binance_symbol and hasattr(bot, "binance_history") and hasattr(bot, "binance_tickers"):
            b_hist_raw = list(bot.binance_history.get(binance_symbol, []))

            # _binance_history menyimpan (timestamp_ms, price) tuples.
            # Kita hitung return berdasarkan window WAKTU AKTUAL 400ms,
            # bukan jumlah ticks (yang variasinya sangat lebar antar koin).
            if len(b_hist_raw) >= 2:
                now_ms = time.time() * 1000
                b_last_ts, b_last = b_hist_raw[-1]

                # Cari tick paling lama yang masih dalam window 400ms
                window_ms = 400.0
                b_ref_ts, b_ref = b_hist_raw[0]  # fallback ke tick tertua
                for entry_ts, entry_price in b_hist_raw:
                    if now_ms - entry_ts <= window_ms:
                        b_ref_ts, b_ref = entry_ts, entry_price
                        break  # paling lama yang masih dalam window

                # Inisialisasi dulu agar tidak UnboundLocalError di blok bawah
                actual_window_ms = b_last_ts - b_ref_ts
                if b_ref > 0 and actual_window_ms >= 0:
                    binance_return = (b_last - b_ref) / b_ref * 100.0

                # Calculate Indodax last, implied rate, and spread percentage
                indodax_price = float(p.get('last', 0.0) or 0.0)
                if indodax_price > 0 and b_last > 0:
                    implied_rate = indodax_price / b_last
                    # Read actual USDT/IDR price from ticker (live, bukan hardcode)
                    _usdt_fallback = 17630.0
                    usdt_price = _usdt_fallback
                    if hasattr(bot, "_tickers") and "usdtidr" in bot._tickers:
                        usdt_price = float(bot._tickers["usdtidr"].get("last", _usdt_fallback) or _usdt_fallback)
                    elif hasattr(bot, "tickers") and "usdtidr" in bot.tickers:
                        usdt_price = float(bot.tickers["usdtidr"].get("last", _usdt_fallback) or _usdt_fallback)
                    else:
                        log_message(f"[GATE-WARN] usdtidr not in tickers for {pair}, using fallback rate {_usdt_fallback}")

                    spread_pct = (implied_rate - usdt_price) / usdt_price * 100.0

                    # TIER 1 (High-Priority): Print latency arbitrage log on every cycle
                    # TIER 2 (Volatility Opportunist): Print ONLY if there is significant micro-volatility
                    should_log = False
                    if tier == 1:
                        should_log = True
                    elif tier == 2 and abs(binance_return) >= 0.05:
                        should_log = True

                    if should_log:
                        log_message(
                            f"[LATENCY-EVAL] Pair={pair} (Tier {tier}) | Indodax={indodax_price:,.0f} IDR | "
                            f"Binance={b_last:,.2f} USDT | Implied={implied_rate:,.2f} | USDT={usdt_price:,.0f} | "
                            f"Spread={spread_pct:+.2f}% | B-{actual_window_ms:.0f}ms={binance_return:+.3f}%"
                        )

                # 1. Toxic-Flow Guard (Flash Crash Protection)
                if binance_return <= -0.50:
                    # Freeze order entry for this pair for 3 seconds
                    db.save_state(f"toxic_freeze_{pair}", time.time() + 3.0)
                    msg = f"[GATE] {pair} REJECTED: Binance Toxic-Flow Guard active (crashed {binance_return:.2f}% in {actual_window_ms:.0f}ms)"
                    log_message(msg)
                    save_gate_state(msg)
                    return None

                # Check active freeze
                freeze_until = float(db.get_state(f"toxic_freeze_{pair}", "0") or 0.0)
                if time.time() < freeze_until:
                    msg = f"[GATE] {pair} REJECTED: Binance Toxic-Flow Freeze active"
                    log_message(msg)
                    save_gate_state(msg)
                    return None

                # 2. Fast-Track Entry Gate (Breakout Bypass)
                # Tier-specific thresholds — Tier 1 lebih agresif, Tier 2 lebih konservatif
                fast_track_threshold = 0.65 if tier == 1 else 0.80

                if binance_return >= fast_track_threshold:
                    # Guard 1: Indodax spread harus wajar — tolak jika spread terlalu lebar
                    # (spread lebar = kita beli di harga ask yang jauh, langsung rugi)
                    indodax_bid = float(p.get('bid', 0.0) or 0.0)
                    indodax_ask = float(p.get('ask', 0.0) or 0.0)
                    if indodax_ask > 0 and indodax_bid > 0 and indodax_price > 0:
                        indodax_spread_pct = (indodax_ask - indodax_bid) / indodax_price * 100.0
                        max_allowed_spread = getattr(config, f'TIER_{tier}_MAX_SPREAD', 1.5)
                        if indodax_spread_pct > max_allowed_spread:
                            log_message(
                                f"[GATE] {pair} FAST-TRACK BLOCKED: Indodax spread too wide "
                                f"({indodax_spread_pct:.2f}% > {max_allowed_spread:.2f}%)"
                            )
                            # Jangan return None — lanjutkan ke gate teknikal biasa
                        else:
                            # Guard 2: Indodax data tidak boleh stale (> 30 detik tidak ada tick)
                            indodax_ts = float(bot._tickers.get(pair_lower, {}).get('timestamp', 0) or 0) \
                                         if hasattr(bot, '_tickers') else 0.0
                            data_age_sec = (time.time() * 1000 - indodax_ts) / 1000.0 if indodax_ts > 0 else 999.0
                            if data_age_sec > 30.0:
                                log_message(
                                    f"[GATE] {pair} FAST-TRACK BLOCKED: Indodax data stale "
                                    f"({data_age_sec:.1f}s old)"
                                )
                                # Jangan return None — lanjutkan ke gate teknikal biasa
                            else:
                                # SEMUA GUARD LOLOS — Eksekusi Fast-Track!
                                log_message(
                                    f"[GATE] {pair} [SPECIAL]: Binance Fast-Track breakout! "
                                    f"Return={binance_return:+.2f}% in {actual_window_ms:.0f}ms "
                                    f"(Tier {tier}, threshold={fast_track_threshold}%). "
                                    f"Spread OK ({indodax_spread_pct:.2f}%), data fresh ({data_age_sec:.1f}s)."
                                )

                                _candle_vols = list(getattr(bot, 'ohlc_vols', {}).get(pair, []))
                                dummy_tech = get_technical_summary(
                                    history, _candle_vols if len(_candle_vols) == len(history) else None
                                )

                                # Fast-Track confidence: makin besar return, makin tinggi confidence
                                # Clamp antara 75% - 95%
                                ft_confidence = min(95.0, 75.0 + (binance_return - fast_track_threshold) * 20.0)

                                res = {
                                    "mode": "SHADOW" if (int(db.get_state("consecutive_losses", 0)) >= config.MAX_CONSECUTIVE_LOSSES) else "LIVE",
                                    "radar_rec": "BUY",
                                    "radar_reason": f"Binance Fast-Track Breakout ({binance_return:+.2f}% in {actual_window_ms:.0f}ms)",
                                    "rsi_val": dummy_tech.get('rsi', 50.0),
                                    "total_confidence": ft_confidence,
                                    "tq_prob": ft_confidence / 100.0,  # Fix: brain-inference pakai tq_prob untuk sig.confidence
                                    "dynamic_threshold": 50.0,
                                    "knn_decision": {"verdict": "CONFIRM", "confidence": ft_confidence},
                                }
                                log_message(f"[GATE] {pair} FAST-TRACK PASSED! conf={ft_confidence:.1f}%")
                                return res
    except Exception as e:
        log_message(f"[GATE-WARN] Error in Binance Lead-Lag Gate for {pair}: {e}")

    #  4. Multi-Confluence Technical Gate 

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
    _adaptive_eg = db.get_coin_indicator_setup(pair) or (compute_adaptive_thresholds(history) if len(history) >= 30 else None)
    _vol_idr_eg = float((p or {}).get('vol_idr') or 0) or None
    tech_sig = derive_trade_signal(tech, sector=_sector_eg, adaptive_thresholds=_adaptive_eg, vol_idr_24h=_vol_idr_eg)

    if not config.DRY_RUN_LOOSE_GATES and tech_sig['verdict'] == 'SELL' and tech_sig.get('sell_score', 0) >= 5.0:
        msg = (
            f"[GATE] {pair} REJECTED: SELL BLOCK (score={tech_sig.get('sell_score',0):.1f})"
        )
        log_message(msg)
        save_gate_state(msg)
        return None

    #  4b. ATR Volatility Filter 
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
            _atr_pct = (_atr / _last) * 100.0
            _atr_limit = 4.5 if config.DRY_RUN_LOOSE_GATES else 3.0
            if _atr_pct > _atr_limit:
                msg = f"[GATE] {pair} REJECTED: ATR Volatility {_atr_pct:.2f}% > {_atr_limit:.1f}% limit"
                log_message(msg)
                save_gate_state(msg)
                return None
        except Exception:
            pass

    #  4c. EMA-50 Trend Filter (with Momentum / Explosion Exception) 
    # [PROFESSIONAL] Di bawah EMA-50 biasanya downtrend, tapi momentum kencang 
    # seringkali memotong EMA-50 dari bawah. Kita buat pengecualian jika velocity kuat.
    is_explosion_setup = False
    if len(history) >= 14:
        # Pengecualian: RSI sangat rendah + Buy Pressure kuat + Volume meledak
        # ATAU Velocity kencang (percepatan harga nyata)
        is_explosion_setup = (
            (tech.get('rsi', 50.0) < 26.0 and tech.get('buy_pressure', 50.0) >= 66.0 and tech.get('volume_ratio', 1.0) >= 2.1)
            or tech.get('velocity', 0.0) > 0.2  # Momentum percepatan harga (lowered from 0.4)
        )

    if not config.DRY_RUN_LOOSE_GATES and not is_explosion_setup and len(history) >= 50:
        try:
            _ema50 = calculate_ema(history, 50)
            _last_price = float(p.get('last', 0.0))
            if _last_price > 0 and _last_price < _ema50:
                msg = f"[GATE] {pair} REJECTED: Price {_last_price:.4f} < EMA-50 {_ema50:.4f}"
                log_message(msg)
                save_gate_state(msg)
                return None
        except Exception:
            pass

    if is_explosion_setup:
        log_message(
            f"[GATE] {pair} [SPECIAL]: Momentum/Explosion detected (Vel={tech.get('velocity'):.2f}%, "
            f"VR={tech.get('volume_ratio'):.1f}). Bypassing EMA-50."
        )

    #  (Binance Lead-Lag / Toxic-Flow Guard moved to top of check_entry_gates for high-priority evaluation)

    #  4d. Vertical Spike / RSI Ceiling Filter 
    _rsi_ceiling = 82.0 if config.DRY_RUN_LOOSE_GATES else 75.0
    _rsi_now = tech.get('rsi', 50.0)
    _velocity = tech.get('velocity', 0.0)
    _vol_ratio = tech.get('volume_ratio', 1.0)
    _ema9 = tech.get('ema9', 0.0)
    _last = float(p.get('last', 0.0))
    
    # Bypass Ceiling jika momentum murni (Velocity + Volume) sangat kuat
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

    #  4e. Resistance-Guard (Double-Top Prevention) 
    if len(history) >= 20:
        _recent_high = max(history)
        _threshold_high = _recent_high * 0.993
        _stoch_k = tech.get('stoch_k', 50.0)
        _velocity = tech.get('velocity', 0.0)
        
        # Izinkan menembus High jika velocity kencang (Breakout momentum)
        if _last >= _threshold_high and (_rsi_now > 60.0 or _stoch_k > 70.0) and not breakout and _velocity < 0.2:
            msg = f"[GATE] {pair} REJECTED: Resistance-Guard Price {_last:.0f} near High {_recent_high:.0f}"
            log_message(msg)
            save_gate_state(msg)
            return None

    #  5. Technical probability + imbalance threshold 
    if tq_prob < 0:
        msg = "[GATE] WARMUP: Insufficient data for TQ_Prob"
        log_message(msg)
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
        msg = f"[GATE] {pair} REJECTED: Tech prob {tq_prob * 100:.0f}% < {tech_gate_threshold * 100:.0f}%"
        log_message(msg)
        save_gate_state(msg)
        return None

    #  6. Trend / breakout requirement 
    if not breakout and trend != "UP":
        _velocity = tech.get('velocity', 0.0)
        _vol_ratio = tech.get('volume_ratio', 1.0)
        
        # Izinkan SIDEWAYS jika ada tanda-tanda awal ledakan (Velocity + Volume)
        sideway_relaxed_ok = (
            (is_loose or (trend == "SIDEWAYS" and _velocity > 0.15 and _vol_ratio > 1.3))
            and tq_prob >= max(tech_gate_threshold, 0.45)
            and imbalance_ratio >= 1.05
        )
        if not sideway_relaxed_ok:
            msg = f"[GATE] {pair} REJECTED: No trend/breakout support (trend={trend})"
            log_message(msg)
            save_gate_state(msg)
            return None

    #  7. Radar recommendation guard 
    if radar_rec != "BUY":
        msg = f"[BLOCK] RADAR BLOCK: rekomendasi {radar_rec} ({radar_reason})"
        print(f"[DEBUG-GATE] {pair} REJECTED: RADAR {radar_rec} ({radar_reason})", flush=True)
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    #  9. Dynamic threshold based on volatility + trend 
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

    #  10. Consecutive-loss halt / Shadow Recovery 
    bot.consecutive_losses = int(db.get_state("consecutive_losses", 0))
    is_shadow_mode = bot.consecutive_losses >= MAX_CONSECUTIVE_LOSSES
    if is_shadow_mode:
        msg = f"  GLOBAL PAUSE ({bot.consecutive_losses} losses): Evaluating for SHADOW PROBE."
        log_message(f"{msg} [{pair}]")
        save_gate_state(msg)
        return None

    #  11. K-NN pattern scoring 
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
        msg = "[BLOCK] [K-NN Avoid]: pola historis buruk, skip."
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

    #  13. Final Confidence threshold 
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
