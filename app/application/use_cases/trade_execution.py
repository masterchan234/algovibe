import time
from datetime import datetime

import numpy as np

from app.infrastructure.database import db
from utils.logger import log_message, send_telegram_message
from config import (
    DRY_RUN,
    INITIAL_CAPITAL,
    MIN_BALANCE_THRESHOLD,
    MIN_TRADE_IDR,
    CAPITAL_PER_TRADE_PCT,
)


def execute_trade(
    bot,
    pair,
    price,
    trend="N/A",
    tq_prob=0.0,
    whale_boost=1.0,
    sector_boost=1.0,
    imbalance=1.0,
    volatility=0.0,
    dynamic_tp=None,
    dynamic_sl=None,
    taker=False,
    llm_verdict="HOLD",
    llm_confidence=0.0,
    llm_reason="",
):
    try:
        multiplier = bot.oracle.get_sizing_multiplier()
        if multiplier <= 0:
            log_message(f"SKIP: Oracle blocked trade ({bot.oracle.get_rationale(pair, 'SKIP_REGIME')})")
            return False

        if DRY_RUN:
            amount_idr = INITIAL_CAPITAL * CAPITAL_PER_TRADE_PCT * multiplier
        else:
            balance = getattr(bot, 'cached_balance', 0)
            if balance < MIN_BALANCE_THRESHOLD:
                log_message(f"STOP: Balance {balance:.0f} IDR below {MIN_BALANCE_THRESHOLD}")
                return False
            target_amount = INITIAL_CAPITAL * CAPITAL_PER_TRADE_PCT * multiplier
            amount_idr = min(target_amount, balance - MIN_BALANCE_THRESHOLD)

        if amount_idr < MIN_TRADE_IDR:
            log_message(f"SKIP: Capital {amount_idr:.0f} IDR below Min Trade {MIN_TRADE_IDR}")
            return False

        from config import TP_PCT, SL_PCT

        final_tp = dynamic_tp if dynamic_tp is not None else TP_PCT
        final_sl = dynamic_sl if dynamic_sl is not None else SL_PCT

        history = list(bot.price_history[pair])
        prices = np.array(history, dtype=float)

        if taker:
            buy_price = price
            log_message(f"🚀 [Taker Execute] Mengejar momentum di harga Market: Rp {buy_price:,.2f}")
        else:
            alpha = 2 / (9 + 1)
            ema_9 = prices[0]
            for p_val in prices:
                ema_9 = (p_val * alpha) + (ema_9 * (1 - alpha))

            buy_price = ema_9
            log_message(f"🎣 [Maker Entry] Memasang jaring beli di EMA-9: Rp {buy_price:,.2f}")

        symbol = pair.upper().replace("IDR", "/IDR")
        amount_crypto = amount_idr / buy_price

        tp_target = buy_price * (1 + final_tp)
        sl_target = buy_price * (1 + final_sl)

        if DRY_RUN:
            order = {
                'id': f"DRY-{int(time.time())}",
                'status': 'closed',
                'price': buy_price,
                'amount': amount_crypto,
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
            }
            predator_msg = ""
            if whale_boost > 1.0:
                predator_msg += "\n🐳 *Whale Backed*: Tembok raksasa terdeteksi menjaga harga."
            if sector_boost > 1.0:
                predator_msg += "\n🔥 *Sector Driven*: Koin ini mengikuti momentum sektor yang sedang 'hot'."

            msg = (
                f"🟢 *DRY RUN BUY* 🟢\n"
                f"Pair: `{pair}`\n"
                f"Reason: `{trend}` (Prob: {tq_prob*100:.1f}%)\n"
                f"Rationale: _{bot.oracle.get_rationale(pair, 'BUY')}_{predator_msg}\n"
                f"Price: Rp {buy_price:,.2f}\n"
                f"Qty: {amount_crypto:.8f}\n"
                f"Target TP: Rp {tp_target:,.2f}\n"
                f"Target SL: Rp {sl_target:,.2f}"
            )
            log_message(msg.replace('*', '').replace('\n', ' | '))
            send_telegram_message(msg)
        else:
            msg = (
                f"🟢 *LIVE BUY* 🟢\n"
                f"Pair: `{pair}`\n"
                f"Reason: `{trend}` (Prob: {tq_prob*100:.1f}%)\n"
                f"Price: Rp {buy_price:,.2f}\n"
                f"Qty: {amount_crypto:.8f}\n"
                f"Target TP: Rp {tp_target:,.2f}\n"
                f"Target SL: Rp {sl_target:,.2f}"
            )
            log_message(msg.replace('*', '').replace('\n', ' | '))
            send_telegram_message(msg)

            params = {
                'post_only': False if taker else True,
                'pair': pair.replace("idr", "_idr"),
            }
            order = bot.exchange.create_limit_buy_order(symbol, amount_crypto, buy_price, params)

        _rsi_dna = None
        _ema_dist_dna = None
        try:
            if len(prices) >= 14:
                _deltas = np.diff(prices[-15:])
                _gains = np.where(_deltas > 0, _deltas, 0)
                _losses = np.where(_deltas < 0, -_deltas, 0)
                _avg_gain = np.mean(_gains) if np.mean(_gains) > 0 else 1e-9
                _avg_loss = np.mean(_losses) if np.mean(_losses) > 0 else 1e-9
                _rsi_dna = round(100 - (100 / (1 + _avg_gain / _avg_loss)), 2)
            if len(prices) >= 20:
                _ema20 = prices[-20:].mean()
                _ema_dist_dna = round((buy_price - _ema20) / _ema20, 6) if _ema20 > 0 else 0.0
        except Exception:
            pass

        dna_data = {
            "ai_prob": tq_prob * 100,
            "whale_boost": whale_boost,
            "sector_sentiment": bot.oracle.get_sector_sentiment_val(pair),
            "imbalance": imbalance,
            "volatility": volatility,
            "rsi": _rsi_dna,
            "ema_dist": _ema_dist_dna,
            "llm_verdict": llm_verdict,
            "llm_confidence": llm_confidence,
            "llm_reason": llm_reason,
            "dry_run": DRY_RUN,
        }
        db.save_trade_dna(pair, dna_data)

        db.log_order(pair, "BUY", buy_price, amount_crypto, order.get('status'), order)

        data = {
            "entry_price": buy_price,
            "amount": amount_crypto,
            "amount_idr": amount_idr,
            "timestamp": datetime.now().isoformat(),
            "dry_run": DRY_RUN,
            "status": "OPEN" if DRY_RUN else "PENDING_BUY",
            "entry_order_id": order.get('id') if not DRY_RUN else None,
            "exit_order_id": None,
            "target_tp_pct": final_tp,
            "target_sl_pct": final_sl,
            "reason_backup": trend,
        }

        db.upsert_position(pair, data)
        return True
    except Exception as e:
        log_message(f"Trade Error: {e}")
        return False
