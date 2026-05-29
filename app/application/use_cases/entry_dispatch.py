"""
entry_dispatch.py

Final trade execution step: calls execute_trade with all computed parameters,
handles rate-limit errors, and applies post-trade pacing delay.
"""

import time

from utils.logger import log_message


def dispatch_entry(bot, p, enriched, gate, target):
    """
    Triggers execute_trade with fully computed entry parameters.

    Returns True if a trade was placed (even in DRY_RUN), False otherwise.
    Handles Indodax TooManyRequests errors with a 60-second hibernation.
    """
    pair = p.get('pair', 'UNKNOWN')

    # 5-second Wick Delay and retrace abort filter on breakout momentum
    velocity = float(gate.get("velocity", 0.0) if isinstance(gate, dict) else 0.0)
    if velocity > 0.3:
        breakout_price = float(p['last'])
        baseline_price = float(gate.get("baseline_price", breakout_price * 0.99))
        pump_height = max(1e-8, breakout_price - baseline_price)

        log_message(
            f" [WICK DELAY] Velocity spike ({velocity:.2f}%) detected on {pair}. "
            f"Waiting 5 seconds... Breakout price: {breakout_price:.4f}"
        )
        time.sleep(5)

        # Get latest ticker price from WebSocket stream cache in O(1)
        tickers = bot.market_ws.get_tickers()
        latest_price = breakout_price
        if tickers and pair in tickers:
            latest_price = float(tickers[pair].get('last', breakout_price))

        retrace = breakout_price - latest_price
        retrace_pct = (retrace / pump_height) * 100.0 if pump_height > 0 else 0.0

        if retrace_pct > 30.0:
            log_message(
                f" [WICK ABORT] {pair} aborted: Retraced {retrace_pct:.1f}% "
                f"(> 30% limit) of breakout pump. Current price: {latest_price:.4f}"
            )
            return False

        log_message(
            f" [WICK OK] Wick delay passed on {pair} (retraced only {retrace_pct:.1f}%). "
            f"Proceeding with order entry."
        )

        # Update entry price to the improved post-delay level
        p['last'] = latest_price

    try:
        traded = bot.execute_trade(
            pair,
            p['last'],
            trend=enriched['trend'],
            tq_prob=enriched['tq_prob'],
            whale_boost=enriched['whale_boost'],
            sector_boost=enriched['sector_boost'],
            imbalance=enriched['imbalance_ratio'],
            volatility=p['volatility'],
            dynamic_tp=target['final_dyn_tp'],
            dynamic_sl=target['final_dyn_sl'],
            entry_size_factor=float(gate.get('entry_size_factor', 1.0) if isinstance(gate, dict) else 1.0),
            taker=target['use_taker'],
        )
        time.sleep(1)
        return bool(traded)
    except Exception as e:
        err_msg = str(e).lower()
        if "toomanyrequests" in err_msg:
            log_message(
                " [Rate Limit] Terlalu banyak permintaan! Hibernasi 60 detik agar IP kembali bersih..."
            )
            time.sleep(60)
        else:
            log_message(f"[WARN] Trade Error [{pair}]: {e}")
        return False
