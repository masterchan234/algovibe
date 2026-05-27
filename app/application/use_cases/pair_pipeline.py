"""
pair_pipeline.py

Candidate-pair pipeline orchestrator:
  1. build_high_speed_candidates — selects and prioritises pairs to evaluate
  2. process_high_speed_candidates — drives each pair through the enrichment →
     gates → targeting → dispatch sub-use-cases
"""

import os
import time

from config import (
    MAX_TRACKED_PAIRS,
    MAX_TRADES_PER_DAY,
    MIN_TRACKING_VOLATILITY,
    HARVEST_MOMENTUM_REENTRY_ENABLED,
    HARVEST_MOMENTUM_MAX_NEW_POSITIONS,
    HARVEST_MOMENTUM_MIN_CONFIDENCE,
    HARVEST_MOMENTUM_MIN_TQ_PROB,
    HARVEST_MOMENTUM_MIN_SCORE,
    HARVEST_MOMENTUM_MIN_5M_PCT,
    HARVEST_MOMENTUM_MIN_15M_PCT,
    HARVEST_MOMENTUM_MIN_TP_PCT,
    HARVEST_MOMENTUM_MIN_RR,
)
import config
from app.infrastructure.database import db
from utils.logger import log_message, send_telegram_message
from app.application.session_awareness import compute_session_awareness

from .candidate_enrichment import enrich_pair
from .entry_dispatch import dispatch_entry
from .entry_gates import check_entry_gates
from .entry_targeting import calculate_entry_target

_BREAK = "BREAK"


def build_high_speed_candidates(bot, ranked, tickers):
    _exch = os.getenv("EXCHANGE", "binance").lower()
    _default_watch = 'btcusdt' if _exch == 'binance' else 'btcidr'
    open_positions = bot.exit_manager.get_open_positions()
    watch_pair = db.get_state('current_watch_pair', _default_watch)

    priority_pairs = list(open_positions.keys())
    if watch_pair not in priority_pairs:
        priority_pairs.append(watch_pair)

    high_speed_tickers = []
    seen_pairs = set()

    for pair in priority_pairs:
        ticker = next((p for p in ranked if p['pair'] == pair), None)
        if ticker:
            high_speed_tickers.append(ticker)
            seen_pairs.add(pair)

    for p in ranked:
        if len(high_speed_tickers) >= MAX_TRACKED_PAIRS:
            break

        relative_momentum = p['score'] / max(0.1, p['volatility'])

        if p['pair'] not in seen_pairs:
            if relative_momentum > 0.2 or p['volatility'] >= MIN_TRACKING_VOLATILITY:
                high_speed_tickers.append(p)
                seen_pairs.add(p['pair'])

    high_speed = high_speed_tickers

    _exchange = os.getenv("EXCHANGE", "binance").lower()
    _btc_ticker_key  = 'btcusdt' if _exchange == 'binance' else 'btcidr'
    _btc_history_key = 'btcusdt' if _exchange == 'binance' else 'btcidr'

    btc_data = tickers.get(_btc_ticker_key)
    if btc_data:
        btc_last = float(btc_data['last'])
    else:
        _btc_fb = db.get_ticker_history(_btc_history_key, limit=1)
        btc_last = _btc_fb[0]['last'] if _btc_fb else 0
        if btc_last == 0:
            log_message("[WARN] Data BTC tidak tersedia di WS/DB. Melewati filter regime.")
            btc_last = 999_999_999

    btc_history = db.get_ticker_history(_btc_history_key, limit=20)
    if btc_history and len(btc_history) >= 20 and not config.DRY_RUN_LOOSE_GATES:
        btc_prices = [h['last'] for h in btc_history]
        btc_sma = sum(btc_prices) / 20
        if btc_last < btc_sma:
            log_message(f"[WARN] [BTC Filter] BTC sedang bearish ({btc_last:.2f} < SMA-20 {btc_sma:.2f}). Bot stand-by.")
            time.sleep(30)
            return None

    open_positions = db.get_open_positions()
    held_pairs = list(open_positions.keys()) if open_positions else []

    current_track_pairs = [p['pair'] for p in high_speed]
    for hp in held_pairs:
        if hp not in current_track_pairs:
            try:
                tickers_snapshot = bot.market_ws.get_tickers()
                ticker_data = tickers_snapshot.get(hp)
                if ticker_data:
                    last_price = float(ticker_data.get('last', 0))
                    high = float(ticker_data.get('high', last_price))
                    low = float(ticker_data.get('low', last_price))
                    volatility = ((high - low) / last_price) * 100 if last_price > 0 else 0.0

                    p_dict = {
                        "pair": hp,
                        "last": last_price,
                        "volatility": volatility,
                        "score": 0.0,
                        "imbalance": 1.0,
                        "change": float(ticker_data.get('change_24h', 0)),
                        "vol_idr": float(ticker_data.get('vol_idr', 0)),
                    }
                    high_speed.append(p_dict)
            except Exception as e:
                log_message(f"[WARN] Warning: Gagal sinkronisasi tracking untuk {hp}: {e}")

    return high_speed


def process_high_speed_candidates(bot, high_speed, tickers, regime, adaptive_cfg):
    open_positions = db.get_open_positions()

    #  [SESSION AWARENESS GATE] 
    # Evaluasi kondisi bot secara menyeluruh: all-time PnL, daily PnL, regime, consecutive losses.
    # Hasilkan strategy params yang tepat untuk kondisi saat ini.
    _regime_pp = getattr(bot.oracle, '_last_regime_str', 'RANGING') if hasattr(bot.oracle, '_last_regime_str') else str(getattr(bot.oracle, 'last_regime', 'RANGING') or 'RANGING')
    _cons_losses = int(db.get_state("consecutive_losses") or 0)
    awareness = compute_session_awareness(bot.oracle, _regime_pp, _cons_losses)

    # HALT: tidak boleh ada entry sama sekali
    if awareness["state"] == "HALT":
        log_message(f"[FAST] [AWARENESS] {awareness['state']}: {awareness['reason']}")
        return

    # Inject awareness params ke adaptive_cfg
    adaptive_cfg["tech_threshold"]      = awareness["tech_threshold"]
    adaptive_cfg["buy_score_threshold"] = awareness["buy_score_threshold"]

    # Terapkan size factor ke bot agar trade_execution bisa membacanya
    bot._recovery_size_factor = awareness["position_size_factor"]

    recovery_mode = awareness["state"] in ("RECOVERY_CRISIS", "RECOVERY_DAILY", "RECOVERY_ALLTIME")
    _max_new_positions = awareness["max_new_positions"]

    # Counter posisi baru per siklus ini
    recovery_new_positions = 0
    harvest_reentry_new_positions = 0

    #  [DAILY TARGET & LOSS LIMIT GATE] 
    # Cek status progress target profit harian sebelum membuka posisi baru.
    target_status = bot.oracle.get_daily_target_status()
    target_idr = target_status["target_idr"]
    current_pnl = target_status["current_pnl"]
    progress_pct = target_status["progress_pct"]

    # Evaluate Smart Trailing & STOP BUY protocol
    # Snapshot session mode BEFORE evaluation so we can detect the HARVEST_ONLY transition.
    _pre_eval_mode = target_status.get("session_mode", "NORMAL")
    session_info = bot.oracle.evaluate_and_update_session_mode(target_status)
    harvest_reentry_mode = False
    if not session_info["buy_enabled"]:
        # HARVEST_ONLY override: allow BUY only for strict high-quality momentum setups.
        if session_info.get("session_mode") == "HARVEST_ONLY" and HARVEST_MOMENTUM_REENTRY_ENABLED:
            harvest_reentry_mode = True
            if _pre_eval_mode != "HARVEST_ONLY":
                log_message(
                    f" [HARVEST_ONLY] BUY normal diblok. "
                    f"Selective re-entry aktif (momentum high-quality only)."
                )
        else:
            # Only log once — at the exact moment the session transitions into HARVEST_ONLY.
            # If already latched, proceed silently to avoid spamming every 2-second cycle.
            if _pre_eval_mode != "HARVEST_ONLY":
                log_message(
                    f" [{session_info['session_mode']}] "
                    f"Target Rp {target_idr:,.0f} sudah tercapai "
                    f"(Net PnL hari ini: Rp {current_pnl:,.0f}). "
                    f"{session_info['message']}"
                )
            return

    # Jika sudah 80%+ menuju target, naikkan threshold lebih jauh di atas awareness threshold
    if target_status["near"]:
        _near_thresh = min(0.90, awareness["tech_threshold"] + 0.12)
        adaptive_cfg["tech_threshold"] = _near_thresh
        log_message(
            f"[WARN] [NEAR TARGET] Progress {progress_pct:.0f}% (Rp {current_pnl:,.0f} / Rp {target_idr:,.0f}). "
            f"Threshold entry dinaikkan → {_near_thresh*100:.0f}% untuk menjaga cuan."
        )
    #  END GATE 

    for p in high_speed:
        pair = p.get('pair', 'UNKNOWN')
        try:
            enriched = enrich_pair(bot, p, tickers)

            gate = check_entry_gates(bot, p, enriched, regime, adaptive_cfg, open_positions)
            if gate is None:
                continue
            if gate == _BREAK:
                break

            # [AWARENESS GATE] Recovery mode: confidence ≥75% + batas posisi baru dari awareness
            if recovery_mode:
                total_conf = float(gate.get("total_confidence", 0))
                if total_conf < 75.0:
                    log_message(
                        f"[AWARENESS] [RECOVERY GATE] {pair} ditolak — "
                        f"butuh confidence ≥75%, dapat: {total_conf:.0f}%"
                    )
                    continue
                if recovery_new_positions >= _max_new_positions:
                    log_message(
                        f"[AWARENESS] [RECOVERY GATE] {pair} ditolak — "
                        f"sudah {recovery_new_positions}/{_max_new_positions} posisi baru di sesi ini."
                    )
                    continue

            target = calculate_entry_target(bot, p, enriched, gate, adaptive_cfg)

            if harvest_reentry_mode:
                if harvest_reentry_new_positions >= HARVEST_MOMENTUM_MAX_NEW_POSITIONS:
                    continue

                total_conf = float(gate.get("total_confidence", 0) or 0.0)
                tq_prob = float(enriched.get("tq_prob", 0) or 0.0)
                score = float(p.get("score", 0) or 0.0)
                ch_5m = float(enriched.get("ch_5m", 0) or 0.0)
                ch_15m = float(enriched.get("ch_15m", 0) or 0.0)
                trend_up = str(enriched.get("trend", "") or "").upper() == "UP"
                breakout_ok = bool(enriched.get("breakout"))

                tp = float(target.get("final_dyn_tp", 0) or 0.0)
                sl_abs = abs(float(target.get("final_dyn_sl", 0) or 0.0))
                rr_ratio = (tp / sl_abs) if sl_abs > 0 else 0.0

                momentum_quality_ok = (
                    trend_up
                    and breakout_ok
                    and total_conf >= HARVEST_MOMENTUM_MIN_CONFIDENCE
                    and tq_prob >= HARVEST_MOMENTUM_MIN_TQ_PROB
                    and score >= HARVEST_MOMENTUM_MIN_SCORE
                    and ch_5m >= HARVEST_MOMENTUM_MIN_5M_PCT
                    and ch_15m >= HARVEST_MOMENTUM_MIN_15M_PCT
                    and tp >= HARVEST_MOMENTUM_MIN_TP_PCT
                    and rr_ratio >= HARVEST_MOMENTUM_MIN_RR
                )

                if not momentum_quality_ok:
                    log_message(
                        f" [HARVEST RE-ENTRY BLOCK] {pair} — "
                        f"conf={total_conf:.1f}% tq={tq_prob*100:.0f}% score={score:.0f} "
                        f"mom5={ch_5m:.2f}% mom15={ch_15m:.2f}% tp={tp*100:.2f}% rr={rr_ratio:.2f}"
                    )
                    continue

                log_message(
                    f"[OK] [HARVEST RE-ENTRY ALLOW] {pair} — momentum high-quality "
                    f"(conf={total_conf:.1f}% tq={tq_prob*100:.0f}% score={score:.0f} "
                    f"tp={tp*100:.2f}% rr={rr_ratio:.2f})"
                )

            # [Senior Fix] Shadow Recovery Path
            if isinstance(gate, dict) and gate.get('mode') == "SHADOW":
                bot.execute_shadow_trade(pair, p['last'], gate, target)
                continue

            if bot.daily_trades >= MAX_TRADES_PER_DAY:
                continue

            if dispatch_entry(bot, p, enriched, gate, target):
                bot.daily_trades += 1
                db.save_state("daily_trades", bot.daily_trades)
                if recovery_mode:
                    recovery_new_positions += 1
                if harvest_reentry_mode:
                    harvest_reentry_new_positions += 1

        except Exception as e:
            log_message(f"[WARN] [Pair Loop Error] {pair}: {e}")
            continue

    # Bersihkan size factor agar tidak bocor ke siklus berikutnya
    if hasattr(bot, '_recovery_size_factor'):
        del bot._recovery_size_factor
