import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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

        # Position sizing: velocity_score and confidence both derived from pure technical probability.
        multiplier = bot.oracle.get_sizing_multiplier(
            pair=pair, 
            velocity_score=tq_prob,
            confidence=tq_prob,   # pure technical, no AI bias
        )
        
        if multiplier <= 0:
            log_message(f"SKIP: Oracle blocked trade ({bot.oracle.get_rationale(pair, 'SKIP_REGIME')})")
            return False

        if config.DRY_RUN:
            # Derive simulated balance from INITIAL_CAPITAL adjusted by today's realised
            # PnL and currently open DRY positions so sizing decays correctly with losses
            # (parity requirement: must mirror the LIVE balance-based sizing path).
            try:
                dry_stats = db.get_daily_stats()
                realized_pnl_dry = float(dry_stats.get("total_pnl_idr") or 0.0)
                open_pos = db.get_open_positions()
                pos_iter = open_pos.values() if isinstance(open_pos, dict) else open_pos
                used_idr = sum(
                    float(p.get("amount_idr", 0))
                    for p in pos_iter
                    if int(p.get("dry_run", 0)) == 1
                )
                dry_balance = INITIAL_CAPITAL + realized_pnl_dry - used_idr
            except Exception:
                dry_balance = INITIAL_CAPITAL
            if dry_balance < MIN_BALANCE_THRESHOLD:
                log_message(f"STOP DRY: Simulated balance {dry_balance:.0f} IDR below {MIN_BALANCE_THRESHOLD}")
                return False
            target_amount = dry_balance * CAPITAL_PER_TRADE_PCT * multiplier
            amount_idr = min(target_amount, dry_balance - MIN_BALANCE_THRESHOLD)
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
                # Only elevate if technical probability is solid (no AI bias)
                if tq_prob >= 0.45 and balance >= MIN_TRADE_IDR * 1.15:
                    log_message(f"🧠 [Intelligent Sizing] Elevating {pair} to Min Trade (Rp {MIN_TRADE_IDR:,.0f}) for execution.")
                    target_amount = MIN_TRADE_IDR * 1.05 # Add 5% buffer for market volatility
                    
            amount_idr = min(target_amount, balance - MIN_BALANCE_THRESHOLD)

        # [Recovery Mode] Kurangi ukuran posisi 50% saat bot dalam mode pemulihan setelah loss limit
        recovery_factor = getattr(bot, '_recovery_size_factor', 1.0)
        if recovery_factor < 1.0:
            amount_idr *= recovery_factor
            log_message(
                f"🔻 [RECOVERY SIZING] {pair}: Ukuran posisi dikurangi menjadi "
                f"Rp {amount_idr:,.0f} ({recovery_factor*100:.0f}% dari normal)"
            )

        # 2. Dynamic check (Participation Rate)
        # Kita tidak ingin trade kita > 1/Multiplier (e.g. 1/50 = 2%) dari volume harian
        required_vol = amount_idr * LIQUIDITY_MULTIPLIER
        if vol_24h < required_vol:
            log_message(f"⚠️ SKIP {pair}: Insufficient Liquidity. Need Rp {required_vol:,.0f} vol, but only has Rp {vol_24h:,.0f}")
            return False

        if amount_idr < MIN_TRADE_IDR:
            log_message(f"SKIP: Capital {amount_idr:.0f} IDR below Min Trade {MIN_TRADE_IDR}")
            return False

        from config import TP_PCT, SL_PCT, ROUND_TRIP_FEE_PCT

        final_tp = dynamic_tp if dynamic_tp is not None else TP_PCT
        final_sl = dynamic_sl if dynamic_sl is not None else SL_PCT

        # [Fee-Viability Guard] Gunakan ROUND_TRIP_FEE_PCT (0.6322%) sebagai baseline.
        # Contoh: TP 1.0% → net profit 1.0% - 0.63% = +0.37% → VALID.
        _fee_rt = ROUND_TRIP_FEE_PCT
        _max_net_profit = amount_idr * (final_tp - _fee_rt)
        _min_viable_net = max(300.0, amount_idr * 0.005)  # Minimal Rp 300 atau 0.5% dari trade
        if _max_net_profit < _min_viable_net:
            log_message(
                f"⚠️ SKIP {pair}: Fee-trap! Max net profit Rp {_max_net_profit:.0f} "
                f"< Rp {_min_viable_net:.0f} (trade=Rp{amount_idr:.0f}, fee={_fee_rt*100:.4f}%, TP={final_tp*100:.1f}%). "
                f"Perlu trade lebih besar atau TP lebih tinggi."
            )
            return False

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
            # Apply the same slippage_buffer the live adapter models (0.21%) so
            # the simulated entry price is not unrealistically tight.
            from config import ROUND_TRIP_FEE_PCT
            _sim_slippage = 0.0021  # mirrors IndodaxAdapter.fee_structure['slippage_buffer']
            sim_fill_price = buy_price * (1 + _sim_slippage)
            buy_price = sim_fill_price   # update buy_price so PnL calc uses realistic entry
            amount_crypto = amount_idr / buy_price  # recalculate amount at simulated fill
            order = {
                'id': f"DRY-{int(time.time())}",
                'status': 'closed',
                'price': buy_price,   # [SIMULATION] pre-computed price + 0.21% slippage
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
            "ai_prob": tq_prob,           # technical probability stored as ai_prob
            "tq_prob": tq_prob * 100,
            "whale_boost": whale_boost,
            "sector_sentiment": sector_boost,
            "imbalance": imbalance,
            "volatility": volatility,
            "rsi": _rsi_dna,
            "ema_dist": _ema_dist_dna,
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
            "amount_crypto": amount_crypto,
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


def execute_basket_exit_live(exchange_client, positions: list) -> dict:
    """Issue simultaneous market sell orders for all given live positions.

    Each position dict must contain at least: pair, amount_crypto, entry_price, amount_idr.
    Orders are placed in parallel via a ThreadPoolExecutor.  After a successful fill
    the position is deleted from the DB and the order is recorded in order_book.

    Returns a summary dict: {filled: [...], failed: [...]}.
    """
    if not positions:
        return {"filled": [], "failed": []}

    filled = []
    failed = []

    def _sell_one(pos):
        pair = pos.get("pair", "")
        amount_crypto = float(pos.get("amount_crypto") or 0)
        entry_price   = float(pos.get("entry_price") or 0)
        amount_idr    = float(pos.get("amount_idr") or 0)

        if amount_crypto <= 0 or not pair:
            return {"pair": pair, "ok": False, "error": "invalid position data"}

        symbol = pair.upper().replace("IDR", "/IDR")
        # Build Indodax param: ensure suffix is exactly '_idr', not mid-string
        _pair_param = pair.lower()
        if _pair_param.endswith("idr"):
            _pair_param = _pair_param[:-3] + "_idr"
        try:
            order = exchange_client.create_order(
                symbol, "market", "sell", amount_crypto, None,
                {"pair": _pair_param}
            )
            import random as _random
            order_id = order.get("id") or (
                f"LIVE-SELL-{pair}-{int(time.time())}-{_random.randint(1000, 9999)}"
            )
            fill_price = float(order.get("price") or entry_price)

            # Record in order_book
            db.upsert_order_book(
                order_id=order_id,
                pair=pair,
                side="SELL",
                price=fill_price,
                amount=amount_crypto,
                status=order.get("status", "filled"),
                raw_response=order,
            )
            # Delete position from DB atomically
            db.delete_position(pair)

            pnl = (fill_price - entry_price) * amount_crypto
            log_message(
                f"⚡ [LIVE BASKET EXIT] {pair}: SOLD {amount_crypto:.6f} @ Rp {fill_price:,.0f} "
                f"| PnL ≈ Rp {pnl:,.0f}"
            )
            return {"pair": pair, "ok": True, "order_id": order_id, "price": fill_price}
        except Exception as exc:
            log_message(f"❌ [LIVE BASKET EXIT] {pair} sell failed: {exc}")
            return {"pair": pair, "ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=min(8, len(positions))) as pool:
        futures = {pool.submit(_sell_one, p): p for p in positions}
        for future in as_completed(futures):
            result = future.result()
            if result.get("ok"):
                filled.append(result)
            else:
                failed.append(result)

    log_message(
        f"🧺 [LIVE BASKET EXIT] Complete — filled: {len(filled)}, failed: {len(failed)}"
    )
    return {"filled": filled, "failed": failed}

