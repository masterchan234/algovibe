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
            taker=target['use_taker'],
        )
        time.sleep(1)
        return bool(traded)
    except Exception as e:
        err_msg = str(e).lower()
        if "toomanyrequests" in err_msg:
            log_message(
                "🛑 [Rate Limit] Terlalu banyak permintaan! Hibernasi 60 detik agar IP kembali bersih..."
            )
            time.sleep(60)
        else:
            log_message(f"⚠️ Trade Error [{pair}]: {e}")
        return False
