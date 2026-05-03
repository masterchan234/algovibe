import os
import threading
import time
from datetime import datetime

from app.application.use_cases.pair_pipeline import (
    build_high_speed_candidates,
    process_high_speed_candidates,
)
from app.domain.scoring import calculate_score
from config import (
    LOG_FILE,
)
import config
from app.infrastructure.database import db
from app.domain.predictor import get_prediction
from app.domain.technicals import get_technical_summary, derive_trade_signal
from utils.logger import log_message, run_performance_report


def run_market_cycle(bot):
    if not bot.oracle.is_healthy():
        log_message(f"ORACLE: {bot.oracle.get_rationale('', 'SKIP_LATENCY')}")
        time.sleep(10)
        return

    regime = bot.oracle.get_market_regime()
    adaptive_cfg = bot.oracle.get_adaptive_config()

    if int(time.time()) % 600 < 10:
        bot.oracle.update_sector_sentiment()

    if int(time.time()) % 1800 < 10:
        bot.exit_manager.reconcile_with_exchange()
        # Refresh AI-driven daily profit target setiap 30 menit
        bot.oracle.refresh_daily_profit_target(balance=getattr(bot, 'cached_balance', 0))

    if db.get_state("trigger_sync_all") == "1":
        db.save_state("trigger_sync_all", "0")
        log_message("📡 MANUAL SYNC: Starting background Mass Account Sync...")
        threading.Thread(target=bot.exit_manager.sync_all_history, daemon=True).start()

    bot.oracle.update_consciousness(regime=regime)

    if int(time.time()) % 3600 < 10:
        bot.oracle.apply_weight_decay()

    current_day = datetime.now().day
    if current_day != bot.start_day:
        run_performance_report(log_fn=log_message)

        if ENABLE_FILE_LOGGING and os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, 'w') as f:
                    f.write(f"--- Log rotated at {datetime.now()} ---\n")
                log_message("📜 LOGS: File log telah dirotasi otomatis (Daily Reset).")
            except Exception as e:
                log_message(f"⚠️ LOGS: Gagal merotasi log: {e}")

        db.prune_old_data(hours=24)

        bot.daily_trades = 0
        bot.consecutive_losses = 0
        bot.start_day = current_day
        db.save_state("daily_trades", 0)
        db.save_state("consecutive_losses", 0)
        db.save_state("start_day", bot.start_day)
        log_message("New day detected. Counters reset & Logs rotated.")

    tickers = bot.get_tickers()
    if not tickers:
        log_message("Market WS: no data yet, waiting…")
        time.sleep(5)
        return

    ranked = [r for r in bot.pool.map(calculate_score, [(item, adaptive_cfg, regime) for item in tickers.items()]) if r]
    ranked.sort(key=lambda x: x['score'], reverse=True)

    bot.exit_manager.monitor_and_execute(
        current_tickers=tickers,
        price_history=bot.price_history,
        get_prediction_fn=get_prediction,
    )

    bot.oracle.request_periodic_analysis(ranked)

    for r in ranked[:5]:
        pair = r['pair']
        change = r['change']
        bot.oracle.detect_fake_pump(pair, change)
        if abs(change) > 3.0:
            bot.oracle.request_pump_analysis(pair, change)

    ranked_with_signals = []
    for r in ranked[:20]:
        try:
            h = list(bot.price_history.get(r['pair'], []))
            t = get_technical_summary(h)
            s = derive_trade_signal(t)
            r_copy = dict(r)
            r_copy['tech_verdict'] = s['verdict']
            r_copy['tech_confidence'] = s['confidence']
            ranked_with_signals.append(r_copy)
        except Exception:
            pass
    bot.oracle.auto_queue_buy_candidates(ranked_with_signals)

    if not config.DRY_RUN:
        try:
            bal_data = bot.exchange.fetch_balance()
            bot.cached_balance = bal_data['free'].get('IDR', 0)
        except Exception as e:
            log_message(f"⚠️ Balance Fetch Error: {e}")
            if "toomanyrequests" in str(e).lower():
                time.sleep(10)

    bot.exit_manager.manage_shadow_jaring(ranked, tickers, balance=getattr(bot, 'cached_balance', 0))

    # Tampilkan progress target profit harian di log utama
    try:
        target_status = bot.oracle.get_daily_target_status()
        target_str = (
            f"Target: Rp{target_status['target_idr']/1000:.0f}k | "
            f"PnL: Rp{target_status['current_pnl']/1000:+.0f}k ({target_status['progress_pct']:.0f}%)"
        ) if target_status['target_idr'] > 0 else "Target: N/A"
    except Exception:
        target_str = "Target: N/A"

    log_message(
        f"Oracle Loop | Regime: {regime:8} | "
        f"AI_Thresh: {adaptive_cfg['ai_threshold']*100:.0f}% | "
        f"MinVol: {adaptive_cfg['min_vol']/1_000_000:.0f}M | "
        f"{target_str}"
    )

    db.save_state("heartbeat", time.time())

    high_speed = build_high_speed_candidates(bot, ranked, tickers)
    if high_speed is None:
        return

    process_high_speed_candidates(bot, high_speed, tickers, regime, adaptive_cfg)
