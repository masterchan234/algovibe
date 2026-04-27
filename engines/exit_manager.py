import json
import os
import time
from datetime import datetime

from config import (
    TP_PCT, SL_PCT, PENDING_BUY_TIMEOUT_MINUTES, PENDING_BUY_MAX_SPREAD_PCT,
    FEE_BUY_MAKER, FEE_SELL_MAKER, FEE_BUY_TAKER, FEE_SELL_TAKER,
    MIN_NET_PROFIT_IDR, TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT,
    SELL_TAKER_OFFSET, SELL_MAKER_OFFSET, JARING_ALLOCATION_PCT, JARING_DEPTH_PCT,
    INITIAL_CAPITAL, AI_MIN_HISTORY_POINTS
)
from engines.database import db
from utils.logger import log_message, send_telegram_message

class ExitManager:
    def __init__(self, exchange_client, dry_run: bool = False, oracle=None):
        self.exchange = exchange_client
        self.dry_run  = dry_run
        self.oracle = oracle
        self.jaring_orders = {} # {pair: {id, price, amount}}
        self.last_jaring_update = {} # {pair: timestamp}
        self._sync_jaring_from_db()

    def get_open_positions(self):
        return db.get_open_positions()

    def save_positions(self, positions, modified_pairs):
        for pair in modified_pairs:
            db.upsert_position(pair, positions[pair])

    def monitor_and_execute(self, current_tickers, price_history=None, get_prediction_fn=None):
        positions = self.get_open_positions()
        # log_message(f"[ExitManager] Monitoring {len(positions)} positions...")
        if not positions:
            return

        modified_pairs = set()

        # --- [PORTFOLIO BASKET EXIT / CROSS-ASSET HEDGING] ---
        # Menutup serentak semua posisi ketika profit gabungan cukup untuk meng-cover posisi yang rugi
        try:
            open_pairs_for_basket = []
            total_net_pnl = 0
            has_losers = False
            
            for pair, data in positions.items():
                if data.get('status') == "OPEN" and pair in current_tickers:
                    current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                    entry_price = data.get('entry_price', 0)
                    amount = data.get('amount_crypto', 0)
                    amount_idr = data.get('amount_idr', 0)
                    
                    sell_value = amount * current_price
                    fee_sell = sell_value * FEE_SELL_TAKER
                    fee_buy = amount_idr * FEE_BUY_MAKER
                    
                    gross_pnl = (current_price - entry_price) * amount
                    net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                    
                    if net_pnl_idr < 0:
                        has_losers = True
                        
                    total_net_pnl += net_pnl_idr
                    open_pairs_for_basket.append((pair, data, current_price, amount))
            
            # Eksekusi jika: >1 posisi, ada posisi yang rugi, dan total profit bersih melampaui MIN_NET_PROFIT_IDR
            if len(open_pairs_for_basket) > 1 and total_net_pnl >= MIN_NET_PROFIT_IDR and has_losers:
                msg = (
                    f"🧺 *PORTFOLIO BASKET EXIT* 🧺\n"
                    f"Menutup {len(open_pairs_for_basket)} posisi serentak untuk menutupi kerugian!\n"
                    f"Estimasi Global Net Profit: Rp {total_net_pnl:,.2f}"
                )
                print(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
                
                for pair, data, current_price, amount in open_pairs_for_basket:
                    reason = "BASKET_EXIT"
                    log_msg = f"⚡ [BASKET_EXIT] {pair}: Liquidated as part of portfolio rescue."
                    log_message(log_msg)
                    db.log_system_event(pair, reason, log_msg, 'SUCCESS')
                    
                    order_id = self.execute_exit(
                        pair, amount, current_price, reason,
                        entry_price=data.get('entry_price', 0),
                        amount_idr=data.get('amount_idr', 0),
                        position_data=data
                    )
                    if order_id:
                        positions[pair]["status"] = "EXITING"
                        positions[pair]["exit_order_id"] = order_id
                        modified_pairs.add(pair)
                
                self.save_positions(positions, modified_pairs)
                return # Langsung exit frame evaluasi ini karena semua posisi sedang ditutup
        except Exception as e:
            log_message(f"🚨 Error in Portfolio Basket Evaluation: {e}")
        for pair, data in list(positions.items()):
            try:
                # Jangan eksekusi jika sedang dalam proses jual (menunggu konfirmasi WS)
                if data.get('status') == "EXITING":
                    continue

                # Ambil harga terakhir dari ticker terbaru
                if pair not in current_tickers:
                    # log_message(f"Skipping {pair}: No ticker data")
                    continue
                    
                current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                entry_price = data.get('entry_price', 0)
                amount = data.get('amount_crypto', 0)
                
                # Diagnostic: Log status for open positions
                # log_message(f"Monitoring {pair}: Price={current_price:,.2f}, Entry={entry_price:,.2f}, Status={data.get('status')}")
            
            # --- AUTO-CANCEL LOGIC UNTUK PENDING_BUY ---
                if data.get('status') == "PENDING_BUY":
                    if self.dry_run:
                        # Di mode simulasi, anggap selalu filled seketika (sudah dihandle OPEN di main.py, 
                        # fallback hapus aja kalau somehow nyangkut)
                        continue
    
                    order_id = data.get('entry_order_id')
                    if not order_id:
                        continue
                        
                    entry_time = datetime.fromisoformat(data['timestamp'])
                    time_elapsed = (datetime.now() - entry_time).total_seconds() / 60.0
                    
                    # Cek jarak harga: Jika harga sekarang sudah lebih tinggi X% dari harga antri kita
                    spread_pct = (current_price - entry_price) / entry_price
                    
                    is_timeout = time_elapsed > PENDING_BUY_TIMEOUT_MINUTES
                    is_too_far = spread_pct > PENDING_BUY_MAX_SPREAD_PCT
                    
                    if is_timeout or is_too_far:
                        reason = "Timeout (3m+)" if is_timeout else f"Harga menjauh (+{spread_pct*100:.2f}%)"
                        try:
                            # [Senior Fix] Standard CCXT Symbol for Validation
                            symbol_cancel = f"{pair[:-3].upper()}/IDR"
                            self.exchange.cancel_order(order_id, symbol_cancel, params={'pair': pair.replace("idr", "_idr"), 'side': 'buy'})
                            # Hapus lokal sebagai fallback jika PrivateWS miss event
                            db.delete_position(pair)
                            
                            msg = f"⚠️ *AUTO-CANCEL BUY* ⚠️\nPair: `{pair}`\nAlasan: {reason}\nHarga Antri: Rp {entry_price:,.2f}"
                            print(msg.replace('*', '').replace('\n', ' | '))
                            send_telegram_message(msg)
                        except Exception as e:
                            error_msg = str(e).lower()
                            print(f"Failed to auto-cancel {pair}: {e}")
                            if "order not found" in error_msg or "not found" in error_msg:
                                # Jika order tidak ada di bursa (sudah dicancel manual / filled saat WS mati), 
                                # hapus dari DB agar tidak stuck selamanya.
                                print(f"⚠️ {pair} order {order_id} not found on exchange. Cleaning up DB...")
                                db.delete_position(pair)
                        
                        # Jangan teruskan eksekusi TP/SL untuk pesanan yang masih pending
                    continue
                # --- END AUTO-CANCEL ---
    
                # Base TP calculation from percentage
                dynamic_tp_pct = data.get('target_tp_pct') if data.get('target_tp_pct') is not None else TP_PCT
                base_tp_price = entry_price * (1 + dynamic_tp_pct)
                
                # Rumus TP: (Target_IDR + Entry_Value * (1 + Buy_Fee)) / (Amount * (1 - Sell_Fee))
                # [Senior Fix] Gunakan FEE_SELL_TAKER karena kita ingin eksekusi instan (Taker)
                exact_tp_price = (MIN_NET_PROFIT_IDR + (entry_price * amount * (1 + FEE_BUY_MAKER))) / (amount * (1 - FEE_SELL_TAKER))
                
                # Target TP minimal agar tidak rugi fee
                tp_price = max(base_tp_price, exact_tp_price)
                
                # Default Initial Stop Loss
                dynamic_sl_pct = data.get('target_sl_pct') if data.get('target_sl_pct') is not None else SL_PCT
                initial_sl_price = entry_price * (1 + dynamic_sl_pct)
                
                # --- [Advanced] Adaptive Trailing Stop Logic ---
                highest_price = data.get('highest_price', entry_price)
                trailing_sl_price = data.get('trailing_sl_price', initial_sl_price)
                
                current_profit_pct = (current_price - entry_price) / entry_price
                
                if current_price > highest_price:
                    highest_price = current_price
                    positions[pair]['highest_price'] = highest_price
                    
                    # Jarak trailing adaptif: Semakin untung, semakin ketat
                    # [Optimization] Mengunci profit lebih agresif saat harga melonjak
                    dist_pct = TRAILING_STOP_DISTANCE_PCT # Default 0.5%
                    if current_profit_pct >= 0.04:   # Profit > 4%
                        dist_pct = 0.002            # Jarak sangat ketat (0.2%)
                    elif current_profit_pct >= 0.02: # Profit > 2%
                        dist_pct = 0.0035           # Jarak ketat (0.35%)
    
                    if current_profit_pct >= TRAILING_STOP_ACTIVATION_PCT:
                        new_trailing_sl = highest_price * (1 - dist_pct)
                        if new_trailing_sl > trailing_sl_price:
                            trailing_sl_price = new_trailing_sl
                            positions[pair]['trailing_sl_price'] = trailing_sl_price
                    
                    # [Senior Sync] Debounce DB update to reduce I/O
                    last_saved_highest = data.get('last_saved_highest_price', 0)
                    last_saved_trailing = data.get('last_saved_trailing_sl', 0)
                    if highest_price > last_saved_highest * 1.002 or trailing_sl_price > last_saved_trailing * 1.002:
                        positions[pair]['last_saved_highest_price'] = highest_price
                        positions[pair]['last_saved_trailing_sl'] = trailing_sl_price
                        modified_pairs.add(pair)
                
                active_sl_price = max(initial_sl_price, trailing_sl_price)
    
                # --- [Defensive] Break-Even Stop Loss ---
                # Jika profit sudah menyentuh 1.0%, pindahkan SL ke harga entry + fee (risk-free trade)
                round_trip_fee = FEE_BUY_TAKER + FEE_SELL_TAKER
                break_even_price = entry_price * (1 + round_trip_fee + 0.001) # +0.1% extra buffer
                
                if current_profit_pct >= 0.01 and active_sl_price < break_even_price:
                    active_sl_price = break_even_price
                    if positions[pair].get('trailing_sl_price', 0) < break_even_price:
                        positions[pair]['trailing_sl_price'] = break_even_price
                        modified_pairs.add(pair)
                        log_message(f"🛡️ [Break-Even] {pair}: SL dipindahkan ke Entry + Fee (Rp {active_sl_price:,.2f})")
    
                # --- [Aggressive] Rapid Take Profit (Scalp Focus) ---
                # Menggunakan kalkulasi Net PnL IDR yang sudah memotong semua fee (Taker Exit)
                try:
                    current_rsi = data.get('rsi', 50)
                    
                    sell_value_est = amount * current_price
                    fee_buy_est = data.get('amount_idr', 0) * FEE_BUY_MAKER
                    fee_sell_est = sell_value_est * FEE_SELL_TAKER
                    net_pnl_idr = (current_price - entry_price) * amount - (fee_buy_est + fee_sell_est)

                    # Syarat 1: Profit Bersih sudah melampaui target minimum (Jangan menunggu!)
                    is_profit_secure = net_pnl_idr >= MIN_NET_PROFIT_IDR
                    
                    # Syarat 2: Profit sudah aman (cover fee + buffer) dan RSI mulai jenuh (>60)
                    is_rapid_momentum_fade = net_pnl_idr > 1000 and current_rsi > 60
                    
                    # Hitung umur posisi dengan aman
                    pos_ts = datetime.fromisoformat(data['timestamp'])
                    pos_age_min = (datetime.now() - pos_ts).total_seconds() / 60.0
                    # Syarat 3: Stagnancy Exit (Hold > 20m & Profit sudah cover fee) -> Free up capital
                    is_stagnant_profit = pos_age_min > 20 and net_pnl_idr > 500
    
                    if is_profit_secure or is_rapid_momentum_fade or is_stagnant_profit:
                        reason_tp = "RAPID_TP_SECURE" if is_profit_secure else ("STAGNANT_EXIT" if is_stagnant_profit else "RAPID_TP_MOMENTUM")
                        log_msg = f"⚡ [{reason_tp}] {pair}: Locked profit +{current_profit_pct*100:.2f}% (Age: {pos_age_min:.0f}m)"
                        log_message(log_msg)
                        db.log_system_event(pair, reason_tp, log_msg, 'SUCCESS')
                        
                        order_id = self.execute_exit(pair, amount, current_price, reason_tp, 
                                                  entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                        if order_id:
                            positions[pair]["status"] = "EXITING"
                            positions[pair]["exit_order_id"] = order_id
                        continue
                except Exception as e:
                    log_message(f"⚠️ Error in Rapid TP for {pair}: {e}")
                    if order_id:
                        positions[pair]["status"] = "EXITING"
                        positions[pair]["exit_order_id"] = order_id
                    continue
    
                # --- [AI-Adaptive] Emergency Exit via Oracle Veto ---
                # Jika AI mendeteksi 'Fake Pump' atau tren berbalik jadi 'AVOID' saat kita HOLD, langsung EXIT.
                ai_veto, veto_reason = False, ""
                if self.oracle:
                    ai_veto, veto_reason = self.oracle.check_llm_veto(pair)
                
                # 1. AI Emergency Exit (Highest Priority)
                if ai_veto and current_profit_pct > -0.005: # Hanya exit jika bukan sedang dalam kerugian besar (cut loss AI)
                    msg = (
                        f"🚨 *AI EMERGENCY EXIT* 🚨\n"
                        f"Pair: `{pair}`\n"
                        f"Alasan: `{veto_reason}`\n"
                        f"Profit: {current_profit_pct*100:+.2f}% (Closing now)"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, "AI_VETO_EXIT",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        positions[pair]["status"] = "EXITING"
                        continue
    
                # Prepare PnL and duration strings for potential exit
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                entry_time = datetime.fromisoformat(data['timestamp'])
                duration = datetime.now() - entry_time
                duration_str = f"{int(duration.total_seconds() // 60)}m {int(duration.total_seconds() % 60)}s"

                # 2. Take Profit (Hard TP) - Opsional, bisa dihapus jika murni trailing, tapi tetap dipakai sbg target final.
                if current_price >= tp_price:
                    msg = (
                        f"🎯 *TAKE PROFIT* 🎯\n"
                        f"Pair: `{pair}`\n"
                        f"Entry: Rp {entry_price:,.2f}\n"
                        f"Exit: Rp {current_price:,.2f}\n"
                        f"PnL: {pnl_pct:+.2f}%\n"
                        f"Hold: {duration_str}"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, "TAKE_PROFIT",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        positions[pair]["status"] = "EXITING"
                        positions[pair]["exit_order_id"] = order_id
    
                # 3. Stop Loss (Initial or Trailing)
                elif current_price <= active_sl_price:
                    # Deteksi tipe exit proteksi
                    is_trailing_hit = active_sl_price > break_even_price
                    is_be_hit = active_sl_price == break_even_price
                    
                    if is_be_hit:
                        reason_str = "BREAK-EVEN SL"
                        log_reason = "BREAK_EVEN"
                    elif is_trailing_hit:
                        reason_str = "TRAILING STOP"
                        log_reason = "TRAILING_STOP"
                    else:
                        reason_str = "STOP LOSS"
                        log_reason = "STOP_LOSS"
                    
                    msg = (
                        f"⚠️ *{reason_str}* ⚠️\n"
                        f"Pair: `{pair}`\n"
                        f"Entry: Rp {entry_price:,.2f}\n"
                        f"Exit: Rp {current_price:,.2f}\n"
                        f"PnL: {pnl_pct:+.2f}%\n"
                        f"Hold: {duration_str}"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, log_reason,
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        positions[pair]["status"] = "EXITING"
                        positions[pair]["exit_order_id"] = order_id
            except Exception as e:
                log_message(f"🚨 Critical error in monitoring loop for {pair}: {e}")
                import traceback
                log_message(traceback.format_exc())

        self.save_positions(positions, modified_pairs)

    def execute_exit(self, pair, amount, price, reason, entry_price=None, amount_idr=0, position_data=None):
        try:
            # Tentukan apakah ini exit darurat (harus instant taker) atau target profit (maker)
            is_emergency = reason in ("STOP_LOSS", "HARD_STOP_LOSS", "AI_VETO_EXIT", "TRAILING_STOP", "BREAK_EVEN", "BASKET_EXIT", "RAPID_TP_SECURE", "RAPID_TP_MOMENTUM", "STAGNANT_EXIT")
            
            if is_emergency:
                # Gunakan SELL_TAKER_OFFSET (5%) untuk memaksa market sell instan
                exit_price = price * (1 - SELL_TAKER_OFFSET)
                fee_sell_rate = FEE_SELL_TAKER
            else:
                # Maker mode untuk Take Profit standar
                exit_price = price * (1 + SELL_MAKER_OFFSET)
                fee_sell_rate = FEE_SELL_MAKER

            # --- Piggyback Strategy (Dust Sweeper) ---
            coin = pair.replace('idr', '').upper()
            try:
                if not self.dry_run:
                    balance = self.exchange.fetch_balance()
                    real_balance = float(balance['free'].get(coin, 0))
                    if real_balance > amount:
                        log_message(f"🐷 Piggyback Strategy: Boosting sell amount from {amount} to {real_balance} {coin} to sweep dust!")
                        amount = real_balance
            except Exception as e:
                log_message(f"Warning: Failed to fetch real balance for Piggyback on {pair}: {e}")

            # [Senior Fix] Standard CCXT Symbol for Validation
            symbol = f"{pair[:-3].upper()}/IDR"
            
            if self.dry_run:
                order = {
                    "id": f"DRY-{int(time.time())}",
                    "status": "closed",
                    "price": exit_price,
                    "amount": amount,
                    "symbol": symbol
                }
                print(f"DRY RUN: Virtual Exit ({reason}) | {pair} | Price: {exit_price:,.2f}")
            else:
                params = {
                    'post_only': True if reason == "TAKE_PROFIT" else False,
                    'pair': pair.replace("idr", "_idr") # Forced Internal Indodax Format
                }
                order = self.exchange.create_limit_sell_order(symbol, amount, exit_price, params)

            # Audit log
            db.log_order(pair, "SELL", exit_price, amount, order.get('status'), order)

            order_status = order.get('status', 'closed')
            
            # Tentukan fee jual berdasarkan strategi exit
            fee_sell_rate = FEE_SELL_TAKER if is_emergency else FEE_SELL_MAKER
            
            # Entry fee selalu Maker (karena bot antri beli)
            fee_buy = amount_idr * FEE_BUY_MAKER
            
            sell_value = amount * price
            fee_sell = sell_value * fee_sell_rate
            
            gross_pnl = (price - entry_price) * amount
            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
            
            if order_status in ['closed', 'canceled', 'done', 'fill'] or self.dry_run:
                db.delete_position(pair)
                
                pnl_idr = net_pnl_idr
                pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                
                # Use provided position_data for timestamp, fallback to now
                timestamp_str = position_data.get('timestamp') if position_data else datetime.now().isoformat()
                entry_time = datetime.fromisoformat(timestamp_str)
                duration_sec = (datetime.now() - entry_time).total_seconds()
                
                try:
                    db.insert_trade_history(
                        pair=pair,
                        entry_price=entry_price,
                        exit_price=price,
                        amount_crypto=amount,
                        amount_idr=amount_idr,
                        pnl_idr=pnl_idr,
                        pnl_pct=pnl_pct,
                        reason=reason,
                        duration_seconds=int(duration_sec),
                        dry_run=self.dry_run
                    )
                    
                    # Update Consecutive Losses state
                    current_cl = int(db.get_state("consecutive_losses", 0))
                    if pnl_idr > 0:
                        db.save_state("consecutive_losses", 0)
                    else:
                        db.save_state("consecutive_losses", current_cl + 1)

                    # ── Project Evolution: Learning Loop ─────────────────────────────
                    db.update_trade_outcome(pair, pnl_idr)
                    if self.oracle:
                        self.oracle.learn_from_trade(pair, pnl_idr)

                except Exception as db_err:
                    print(f"Failed to save trade history/state for {pair}: {db_err}")
            else:
                db.update_position_status(pair, "EXITING", exit_order_id=order.get('id'))
            
            return order.get('id')
        except Exception as e:
            err_msg = f"Critical error in execute_exit for {pair}: {e}"
            print(err_msg)
            db.log_system_event(pair, "EXIT_ERROR", err_msg, 'ERROR')
            return None
                
    def handle_order_update(self, order):
        """
        Callback dari PrivateWS. Memproses order yang terisi (Filled).
        """
        try:
            order_id = str(order.get('id', ''))
            pair = order.get('pair', '')
            status = order.get('status', '').lower()
            
            if status not in ['filled', 'closed', 'fill']: return
            
            # 1. Cek apakah ini Jaring yang terisi
            for p, jaring in list(self.jaring_orders.items()):
                if str(jaring['id']) == order_id:
                    log_message(f"🎯 JARING FILLED! {p} bought at Rp {jaring['price']:,.2f} via Liquidity Grab.")
                    
                    # Pindahkan dari Shadow ke Positions
                    data = {
                        "entry_price": jaring['price'],
                        "amount":      jaring['amount'],
                        "amount_idr":  jaring['price'] * jaring['amount'],
                        "timestamp":   datetime.now().isoformat(),
                        "dry_run":     self.dry_run,
                        "status":      "OPEN",
                        "entry_order_id": order_id,
                        "exit_order_id": None
                    }
                    db.upsert_position(p, data)
                    db.delete_shadow_order(p)
                    if p in self.jaring_orders: del self.jaring_orders[p]
                    
                    send_telegram_message(f"🎯 *JARING FILLED* 🎯\nPair: `{p}`\nPrice: Rp {jaring['price']:,.2f}")
                    return

            # 2. Cek apakah ini Buy Order reguler yang baru terisi
            positions = db.get_open_positions()
            for p_pair, p_data in positions.items():
                if p_data.get('entry_order_id') == order_id and p_data.get('status') == "PENDING_BUY":
                    log_message(f"✅ BUY FILLED! {p_pair} order {order_id} is now OPEN.")
                    db.update_position_status(p_pair, "OPEN")
                    send_telegram_message(f"✅ *BUY FILLED* ✅\nPair: `{p_pair}`\nStatus: `OPEN`")
                    return

            # 3. Cek apakah ini Sell Order (Take Profit/Stop Loss) yang terisi
            for p_pair, p_data in positions.items():
                if p_data.get('exit_order_id') == order_id and p_data.get('status') == "EXITING":
                    log_message(f"💰 SELL FILLED! {p_pair} order {order_id} is now CLOSED.")
                    
                    try:
                        exit_price = float(order.get('price', 0))
                        # Fallback
                        if exit_price == 0: exit_price = float(p_data.get('entry_price', 0)) * (1 + p_data.get('target_tp_pct', TP_PCT))
                        
                        amount_crypto = float(order.get('amount', p_data.get('amount_crypto', 0)))
                        entry_price = float(p_data.get('entry_price', 0))
                        amount_idr = float(p_data.get('amount_idr', 0))
                        
                        sell_value = amount_crypto * exit_price
                        fee_sell = sell_value * FEE_SELL_MAKER
                        fee_buy = amount_idr * FEE_BUY_MAKER
                        
                        gross_pnl = (exit_price - entry_price) * amount_crypto
                        net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                        pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                        
                        reason = "TAKE_PROFIT" if net_pnl_idr > 0 else "STOP_LOSS"
                        
                        timestamp_str = p_data.get('timestamp') or datetime.now().isoformat()
                        duration_sec = (datetime.now() - datetime.fromisoformat(timestamp_str)).total_seconds()
                        
                        db.insert_trade_history(
                            pair=p_pair, entry_price=entry_price, exit_price=exit_price,
                            amount_crypto=amount_crypto, amount_idr=amount_idr,
                            pnl_idr=net_pnl_idr, pnl_pct=pnl_pct, reason=reason,
                            duration_seconds=int(duration_sec), dry_run=p_data.get('dry_run')
                        )
                        
                        current_cl = int(db.get_state("consecutive_losses", 0))
                        db.save_state("consecutive_losses", 0 if net_pnl_idr > 0 else current_cl + 1)
                        
                        db.update_trade_outcome(p_pair, net_pnl_idr)
                        if self.oracle: self.oracle.learn_from_trade(p_pair, net_pnl_idr)
                        
                        db.delete_position(p_pair)
                        send_telegram_message(f"💰 *EXIT FILLED* 💰\nPair: `{p_pair}`\nReason: `{reason}`\nPnL: Rp {net_pnl_idr:,.2f}")
                    except Exception as ex:
                        log_message(f"Error calculating final PnL for {p_pair}: {ex}")
                    return
            
        except Exception as e:
            print(f"Error handling order update: {e}")

    def reconcile_with_exchange(self):
        """
        [Anti-Hallucination] 
        Sinkronisasi posisi di Database dengan saldo asli di bursa.
        """
        try:
            log_message("Reality Check: Reconciling positions with Indodax balance...")
            if self.dry_run: return # Dry run tidak punya saldo asli untuk di-cek
            
            balance = self.exchange.fetch_balance()
            open_positions = db.get_open_positions()
            
            for pair, pos in open_positions.items():
                coin = pair.replace('idr', '').upper()
                amount_on_exchange = float(balance['total'].get(coin, 0))
                
                # [Fix] Promosikan PENDING_BUY ke OPEN jika saldo sudah ada di bursa
                if pos.get('status') == "PENDING_BUY":
                    if amount_on_exchange >= (pos['amount_crypto'] * 0.9):
                        log_message(f"🔄 Reality Sync: {pair} was PENDING_BUY but balance exists. Promoting to OPEN and syncing amount.")
                        pos['status'] = "OPEN"
                        pos['amount_crypto'] = amount_on_exchange
                        db.upsert_position(pair, pos)
                        continue
                    else:
                        # Order belum tersentuh/fill, jadi wajar saldonya 0. Skip cek hallucination.
                        continue

                # [Fix] Selalu sync amount dengan bursa jika ada perbedaan tipis (fee/rounding)
                if abs(amount_on_exchange - pos['amount_crypto']) > 0 and amount_on_exchange > 0:
                    if 0.8 < (amount_on_exchange / pos['amount_crypto']) < 1.2:
                        # log_message(f"⚖️ Amount Sync: {pair} amount adjusted {pos['amount_crypto']} -> {amount_on_exchange}")
                        pos['amount_crypto'] = amount_on_exchange
                        db.upsert_position(pair, pos)
                # Gunakan amount_crypto yang konsisten dengan schema DB
                target_amount = pos.get('amount_crypto') or pos.get('amount', 0)
                if amount_on_exchange < (target_amount * 0.8): # Toleransi lebih longgar 20%
                    log_message(f"🚨 Reality Desync Detected! {pair} not found on exchange. Marking as GHOST.")
                    db.delete_position(pair)
                    if pair in self.jaring_orders:
                        del self.jaring_orders[pair]
                        db.delete_shadow_order(pair)
            
            # ── [Auto-Adoption] Sync orphans from wallet to DB ──
            for coin, coin_amount in balance['total'].items():
                if coin == 'IDR' or coin_amount <= 0:
                    continue
                    
                pair = f"{coin.lower()}idr"
                if pair not in open_positions:
                    try:
                        # Gunakan REST API karena ExitManager tidak punya akses ke Market WS
                        ticker_rest = self.exchange.fetch_ticker(pair.upper().replace("IDR", "/IDR"))
                        current_price = float(ticker_rest['last'])
                            
                        value_idr = coin_amount * current_price
                        
                        # Adopsi hanya jika nilainya di atas Rp 10.000 (mengabaikan dust)
                        if value_idr >= 10000:
                            log_message(f"👼 Orphan Adoption: Found {coin_amount} {coin} in wallet (Value: Rp {value_idr:,.0f}). Adopting into Dashboard!")
                            
                            adopt_data = {
                                "entry_price": current_price,
                                "amount":      coin_amount,
                                "amount_crypto": coin_amount,
                                "amount_idr":  value_idr,
                                "timestamp":   datetime.now().isoformat(),
                                "dry_run":     False,
                                "status":      "OPEN",
                                "entry_order_id": f"ADOPT-{int(time.time())}",
                                "exit_order_id": None,
                                "target_tp_pct": TP_PCT,
                                "target_sl_pct": SL_PCT
                            }
                            db.upsert_position(pair, adopt_data)
                            send_telegram_message(f"👼 *ORPHAN ADOPTED* 👼\nPair: `{pair}`\nQty: {coin_amount}\nEst. Value: Rp {value_idr:,.0f}")
                    except Exception as e:
                        log_message(f"Failed to adopt orphan {pair}: {e}")
            
            # ── [Active Order Sync] Sync manual Open Orders to DB ──
            try:
                open_orders_res = self.exchange.privatePostOpenOrders()
                if open_orders_res and str(open_orders_res.get('success')) == '1':
                    orders_dict = open_orders_res.get('return', {}).get('orders', {})
                    
                    # Flatten active order IDs for quick lookup
                    all_active_order_ids = set()
                    if isinstance(orders_dict, dict):
                        for pk, ol in orders_dict.items():
                            for o in ol: all_active_order_ids.add(str(o.get('order_id')))

                    # [Anti-Stuck] Detect and force-sell stagnant EXITING orders
                    for p, pos in list(open_positions.items()):
                        if pos.get('status') == "EXITING":
                            oid = str(pos.get('exit_order_id', ''))
                            if oid in all_active_order_ids:
                                # Order still open on exchange. Check duration since position opened.
                                pos_ts = datetime.fromisoformat(pos['timestamp'])
                                age_sec = (datetime.now() - pos_ts).total_seconds()
                                if age_sec > 300: # 5 minutes
                                    log_message(f"⚠️ [Stuck Exit] {p} order {oid} is stuck for {age_sec:.0f}s. Cancelling and performing Emergency Exit...")
                                    try:
                                        # Format Indodax: aih_idr, Format CCXT: AIH/IDR
                                        ccxt_symbol = p.upper().replace("IDR", "/IDR")
                                        # Indodax cancel_order requires 'type' (buy/sell) and 'pair' (with underscore)
                                        self.exchange.cancel_order(oid, ccxt_symbol, params={'pair': p.replace("idr", "_idr"), 'type': 'sell'})
                                        # Revert to OPEN so the main monitor loop can re-process it (now as Taker due to is_emergency)
                                        pos['status'] = "OPEN"
                                        pos['exit_order_id'] = None
                                        db.upsert_position(p, pos)
                                    except Exception as ce:
                                        log_message(f"Failed to cancel stuck order {oid}: {ce}")

                    if isinstance(orders_dict, dict):
                        for pair_key, order_list in orders_dict.items():
                            pair = pair_key.replace('_', '')
                            pos = open_positions.get(pair)
                            
                            for order in order_list:
                                o_type = order.get('type')
                                o_id = str(order.get('order_id'))
                                o_price = float(order.get('price', 0))
                                
                                if pos is None:
                                    if o_type == 'buy':
                                        remain_idr = float(order.get('remain_idr', 0))
                                        amount_crypto_est = remain_idr / o_price if o_price > 0 else 0
                                        
                                        log_message(f"🔄 Active Order Sync: Found untracked BUY order for {pair}. Adopting as PENDING_BUY.")
                                        adopt_data = {
                                            "entry_price": o_price,
                                            "amount": amount_crypto_est,
                                            "amount_crypto": amount_crypto_est,
                                            "amount_idr": remain_idr,
                                            "timestamp": datetime.now().isoformat(),
                                            "dry_run": False,
                                            "status": "PENDING_BUY",
                                            "entry_order_id": o_id,
                                            "exit_order_id": None,
                                            "target_tp_pct": TP_PCT,
                                            "target_sl_pct": SL_PCT
                                        }
                                        db.upsert_position(pair, adopt_data)
                                        send_telegram_message(f"🔄 *SYNC PENDING BUY* 🔄\nPair: `{pair}`\nPrice: Rp {o_price:,.0f}")
                                        pos = adopt_data
                                        open_positions[pair] = adopt_data
                                        
                                    elif o_type == 'sell':
                                        coin_sym = pair.replace('idr', '')
                                        remain_coin = float(order.get(f'remain_{coin_sym}', 0))
                                        
                                        log_message(f"🔄 Active Order Sync: Found untracked SELL order for {pair}. Adopting as EXITING.")
                                        adopt_data = {
                                            "entry_price": o_price * 0.95,
                                            "amount": remain_coin,
                                            "amount_crypto": remain_coin,
                                            "amount_idr": remain_coin * o_price,
                                            "timestamp": datetime.now().isoformat(),
                                            "dry_run": False,
                                            "status": "EXITING",
                                            "entry_order_id": f"ADOPT-{int(time.time())}",
                                            "exit_order_id": o_id,
                                            "target_tp_pct": TP_PCT,
                                            "target_sl_pct": SL_PCT
                                        }
                                        db.upsert_position(pair, adopt_data)
                                        send_telegram_message(f"🔄 *SYNC EXITING SELL* 🔄\nPair: `{pair}`\nSell Price: Rp {o_price:,.0f}")
                                        pos = adopt_data
                                        open_positions[pair] = adopt_data
                                else:
                                    if o_type == 'sell' and pos.get('status') == 'OPEN':
                                        log_message(f"🔄 Active Order Sync: {pair} was OPEN but has active SELL order. Updating to EXITING.")
                                        pos['status'] = 'EXITING'
                                        pos['exit_order_id'] = o_id
                                        db.upsert_position(pair, pos)
            except Exception as e:
                log_message(f"Failed to sync active orders: {e}")
            
            log_message("Reality Check: Sync complete. No hallucinations found.")
        except Exception as e:
            log_message(f"Reality Check Failed: {e}")
            # [Fix BUG #2] Dead code dihapus — 'order' tidak terdefinisi di scope ini

    # ── Shadow Jaring (Liquidity Grab) ───────────────────────────────────

    def _sync_jaring_from_db(self):
        """Recover active jaring orders from SQLite on startup."""
        saved = db.get_all_shadow_orders()
        for o in saved:
            self.jaring_orders[o['pair']] = o
    def manage_shadow_jaring(self, ranked_pairs, current_tickers):
        """
        Main entry for jaring logic. Called from the main loop.
        """
        try:
            # 1. Trailing: Move existing jaring up if price is too far (protect from missing pumps)
            for pair, o in list(self.jaring_orders.items()):
                if pair in current_tickers:
                    curr_price = current_tickers[pair]['last']
                    
                    # [Fix] Tambah Cooldown 5 Menit agar tidak spam cancel/replace
                    last_update = self.last_jaring_update.get(pair, 0)
                    if time.time() - last_update < 300: continue 

                    # Jika harga pasar sudah naik > 10% dari jaring kita
                    if curr_price > o['price'] * 1.10:
                        log_message(f"Jaring: Price moved up for {pair}. Trailing jaring...")
                        if not self.dry_run: self.exchange.cancel_order(o['id'])
                        db.delete_shadow_order(pair)
                        del self.jaring_orders[pair]
                        self.last_jaring_update[pair] = time.time()

            # 2. Placement: Fill empty slots (Max 3 jaring)
            if len(self.jaring_orders) < 3:
                candidates = [p for p in ranked_pairs if p['pair'] not in self.jaring_orders][:3]
                for c in candidates:
                    pair = c['pair']
                    jaring_price = c['last'] * (1 + JARING_DEPTH_PCT)
                    amount_idr = INITIAL_CAPITAL * JARING_ALLOCATION_PCT
                    amount_crypto = amount_idr / jaring_price
                    self.place_jaring(pair, amount_crypto, jaring_price)
                    
        except Exception as e:
            print(f"Jaring Manager Error: {e}")

    def place_jaring(self, pair, amount, price):
        """Place a deep-limit buy order."""
        try:
            log_message(f"Jaring: Placing Liquidity Grab on {pair} at Rp {price:,.2f}")
            if self.dry_run:
                order_id = f"JARING-{int(time.time())}"
                order = {"id": order_id, "status": "open", "price": price, "amount": amount}
            else:
                order = self.exchange.create_limit_buy_order(pair, amount, price)
                order_id = order['id']
            
            self.jaring_orders[pair] = {"id": order_id, "price": price, "amount": amount}
            db.upsert_shadow_order(pair, order_id, price, amount)
            
        except Exception as e:
            print(f"Failed to place jaring for {pair}: {e}")