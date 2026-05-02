import time
from datetime import datetime

import numpy as np

from app.infrastructure.database import db
from utils.logger import log_message, send_telegram_message
import config
from config import (
    INITIAL_CAPITAL,
    MIN_BALANCE_THRESHOLD,
    MIN_TRADE_IDR,
    CAPITAL_PER_TRADE_PCT,
    MAX_OPEN_POSITIONS,
    MIN_VOLUME_FILTER,
    LIQUIDITY_MULTIPLIER,
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
        # --- [Risk Check: Max Positions] ---
        active_count = db.get_active_positions_count(dry_run=1 if config.DRY_RUN else 0)
        
        # [Senior Audit] Adaptive Position Limits based on Equity
        effective_max_pos = MAX_OPEN_POSITIONS
        is_small_account = False
        
        if not config.DRY_RUN:
            balance = getattr(bot, 'cached_balance', 0)
            if balance < 500000: # < 500rb IDR
                is_small_account = True
                effective_max_pos = min(5, MAX_OPEN_POSITIONS)
                
        if active_count >= effective_max_pos:
            log_message(f"⚠️ SKIP {pair}: Max active positions ({effective_max_pos}) reached for account size.")
            return False

        # --- [Risk Check: Dynamic Liquidity Guard] ---
        tickers = bot.market_ws.get_tickers()
        coin_data = tickers.get(pair, {})
        vol_24h = float(coin_data.get('vol_idr', 0))
        
        # 1. Global Minimum check
        if vol_24h < MIN_VOLUME_FILTER:
            log_message(f"⚠️ SKIP {pair}: Volume 24h (Rp {vol_24h:,.0f}) below Min Filter (Rp {MIN_VOLUME_FILTER:,.0f})")
            return False

        # Hitung estimasi modal yang akan dipakai dengan Profit Velocity Sizing
        # tq_prob mewakili kecepatan/kekuatan tren teknikal
        # llm_confidence mewakili keyakinan analisa AI
        multiplier = bot.oracle.get_sizing_multiplier(
            pair=pair, 
            velocity_score=tq_prob, 
            confidence=llm_confidence / 100.0  # Konversi dari 0-100 ke 0-1
        )
        
        if multiplier <= 0:
            log_message(f"SKIP: Oracle blocked trade ({bot.oracle.get_rationale(pair, 'SKIP_REGIME')})")
            return False

        if config.DRY_RUN:
            amount_idr = INITIAL_CAPITAL * CAPITAL_PER_TRADE_PCT * multiplier
        else:
            balance = getattr(bot, 'cached_balance', 0)
            if balance < MIN_BALANCE_THRESHOLD:
                log_message(f"STOP: Balance {balance:.0f} IDR below {MIN_BALANCE_THRESHOLD}")
                return False
            # Use real balance as the basis for sizing in LIVE mode
            target_amount = balance * CAPITAL_PER_TRADE_PCT * multiplier
            
            # [Small Account Optimization]
            # If the calculated size is too small for the exchange but the account can afford it,
            # we 'clamp' to the minimum trade amount to ensure the bot can actually execute.
            if is_small_account and target_amount < MIN_TRADE_IDR:
                # Only elevate if AI confidence or tech probability is solid
                if (tq_prob >= 0.45 or llm_confidence >= 55) and balance >= MIN_TRADE_IDR * 1.15:
                    log_message(f"🧠 [Intelligent Sizing] Elevating {pair} to Min Trade (Rp {MIN_TRADE_IDR:,.0f}) for execution.")
                    target_amount = MIN_TRADE_IDR * 1.05 # Add 5% buffer for market volatility
                    
            amount_idr = min(target_amount, balance - MIN_BALANCE_THRESHOLD)

        # 2. Dynamic check (Participation Rate)
        # Kita tidak ingin trade kita > 1/Multiplier (e.g. 1/50 = 2%) dari volume harian
        required_vol = amount_idr * LIQUIDITY_MULTIPLIER
        if vol_24h < required_vol:
            log_message(f"⚠️ SKIP {pair}: Insufficient Liquidity. Need Rp {required_vol:,.0f} vol, but only has Rp {vol_24h:,.0f}")
            return False

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

        if config.DRY_RUN:
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
                'pair': pair.replace("idr", "_idr"),
            }
            # [Final Fix] Use explicit create_order for market buy to avoid argument count confusion
            order = bot.exchange.create_order(symbol, 'market', 'buy', amount_crypto, buy_price, params)
            # Update buy_price dengan harga eksekusi asli jika tersedia
            buy_price = float(order.get('price') or price)

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

        import threading
        def background_logging(p, d, o, m_label, t_prob, b_p, f_tp, f_sl):
            try:
                db.save_trade_dna(p, d)
                db.log_order(p, "BUY", b_p, d.get("amount_crypto", 0), o.get('status'), o)
                db.log_system_event(
                    p, "BUY",
                    f"{m_label} | Prob:{t_prob*100:.1f}% | Rp {b_p:,.0f} | TP:{f_tp*100:.1f}% SL:{f_sl*100:.1f}%",
                    'INFO'
                )
            except Exception as ex:
                log_message(f"⚠️ Async Logging Error: {ex}")

        dna_data = {
            "amount_crypto": amount_crypto,
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
            "dry_run": config.DRY_RUN,
        }
        
        mode_label = "DRY RUN BUY" if config.DRY_RUN else "LIVE BUY"
        threading.Thread(
            target=background_logging,
            args=(pair, dna_data, order, mode_label, tq_prob, buy_price, final_tp, final_sl),
            daemon=True
        ).start()

        data = {
            "entry_price": buy_price,
            "amount": amount_crypto,
            "amount_idr": amount_idr,
            "timestamp": datetime.now().isoformat(),
            "dry_run": config.DRY_RUN,
            "status": "OPEN" if config.DRY_RUN else "PENDING_BUY",
            "entry_order_id": order.get('id') if not config.DRY_RUN else None,
            "exit_order_id": None,
            "target_tp_pct": final_tp,
            "target_sl_pct": final_sl,
            "reason_backup": trend,
            "ai_confidence": tq_prob * 100,
        }

        db.upsert_position(pair, data)
        return True
    except Exception as e:
        log_message(f"Trade Error: {e}")
        return False
