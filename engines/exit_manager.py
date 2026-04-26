import json
import os
import time
from datetime import datetime

from config import TP_PCT, SL_PCT, PENDING_BUY_TIMEOUT_MINUTES, PENDING_BUY_MAX_SPREAD_PCT
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

    def save_positions(self, positions):
        # Fallback method if needed, but we should use db.upsert_position instead
        pass

    def monitor_and_execute(self, current_tickers, price_history=None, get_prediction_fn=None):
        positions = self.get_open_positions()
        if not positions:
            return

        for pair, data in list(positions.items()):
            # Jangan eksekusi jika sedang dalam proses jual (menunggu konfirmasi WS)
            if data.get('status') == "EXITING":
                continue

            # Ambil harga terakhir dari ticker terbaru
            if pair not in current_tickers:
                continue
                
            current_price = float(current_tickers[pair]['last'])
            entry_price = data['entry_price']
            amount = data['amount_crypto']
            
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
                        self.exchange.cancel_order(order_id, pair)
                        # Hapus lokal sebagai fallback jika PrivateWS miss event
                        db.delete_position(pair)
                        
                        msg = f"⚠️ *AUTO-CANCEL BUY* ⚠️\nPair: `{pair}`\nAlasan: {reason}\nHarga Antri: Rp {entry_price:,.2f}"
                        print(msg.replace('*', '').replace('\n', ' | '))
                        send_telegram_message(msg)
                    except Exception as e:
                        print(f"Failed to auto-cancel {pair}: {e}")
                    
                    # Jangan teruskan eksekusi TP/SL untuk pesanan yang masih pending
                continue
            # --- END AUTO-CANCEL ---

            # Import the required variables dynamically
            from config import (
                FEE_BUY_MAKER, FEE_SELL_MAKER, MIN_NET_PROFIT_IDR,
                TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT
            )
            
            # Base TP calculation from percentage
            dynamic_tp_pct = data.get('target_tp_pct') if data.get('target_tp_pct') is not None else TP_PCT
            base_tp_price = entry_price * (1 + dynamic_tp_pct)
            
            # Rumus: (Target_IDR + Entry_Value * (1 + Buy_Fee)) / (Amount * (1 - Sell_Fee))
            exact_tp_price = (MIN_NET_PROFIT_IDR + (entry_price * amount * (1 + FEE_BUY_MAKER))) / (amount * (1 - FEE_SELL_MAKER))
            
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
                
                # [Senior Sync] Update DB agar state trailing tidak hilang saat restart
                db.upsert_position(pair, positions[pair])
            
            active_sl_price = max(initial_sl_price, trailing_sl_price)

            # --- [Defensive] Break-Even Stop Loss ---
            # Jika profit sudah menyentuh 1.0%, pindahkan SL ke harga entry + fee (risk-free trade)
            from config import FEE_BUY_TAKER, FEE_SELL_TAKER
            round_trip_fee = FEE_BUY_TAKER + FEE_SELL_TAKER
            break_even_price = entry_price * (1 + round_trip_fee + 0.001) # +0.1% extra buffer
            
            if current_profit_pct >= 0.01 and active_sl_price < break_even_price:
                active_sl_price = break_even_price
                if positions[pair].get('trailing_sl_price', 0) < break_even_price:
                    positions[pair]['trailing_sl_price'] = break_even_price
                    db.upsert_position(pair, positions[pair])
                    log_message(f"🛡️ [Break-Even] {pair}: SL dipindahkan ke Entry + Fee (Rp {active_sl_price:,.2f})")

            # --- [Aggressive] Rapid Take Profit (Scalp Focus) ---
            # Jika sudah untung bersih dan RSI mulai jenuh (>70), jangan serakah, EXIT SEKARANG.
            current_rsi = data.get('rsi', 50)
            if current_profit_pct > (round_trip_fee + 0.002) and current_rsi > 70:
                log_msg = f"⚡ [Rapid TP] {pair}: Locked profit +{current_profit_pct*100:.2f}% (RSI: {current_rsi:.0f})"
                log_message(log_msg)
                db.log_system_event(pair, 'RAPID_TP', log_msg, 'SUCCESS')
                
                order_id = self.execute_exit(pair, amount, current_price, "RAPID_TP", 
                                          entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
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

            # 2. Take Profit (Hard TP) - Opsional, bisa dihapus jika murni trailing, tapi tetap dipakai sbg target final.
            if current_price >= tp_price:
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                entry_time = datetime.fromisoformat(data['timestamp'])
                duration = datetime.now() - entry_time
                duration_str = f"{int(duration.total_seconds() // 60)}m {int(duration.total_seconds() % 60)}s"

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
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                entry_time = datetime.fromisoformat(data['timestamp'])
                duration = datetime.now() - entry_time
                duration_str = f"{int(duration.total_seconds() // 60)}m {int(duration.total_seconds() % 60)}s"

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

        self.save_positions(positions)

    def execute_exit(self, pair, amount, price, reason, entry_price=None, amount_idr=0, position_data=None):
        try:
            from config import FEE_SELL_MAKER, FEE_SELL_TAKER, SELL_TAKER_OFFSET, SELL_MAKER_OFFSET
            
            # Tentukan apakah ini exit darurat (harus instant taker) atau target profit (maker)
            is_emergency = reason in ("STOP_LOSS", "HARD_STOP_LOSS", "AI_VETO_EXIT", "TRAILING_STOP", "BREAK_EVEN")
            
            if is_emergency:
                # Gunakan SELL_TAKER_OFFSET (5%) untuk memaksa market sell instan
                exit_price = price * (1 - SELL_TAKER_OFFSET)
                fee_sell_rate = FEE_SELL_TAKER
            else:
                # Maker mode untuk Take Profit standar
                exit_price = price * (1 + SELL_MAKER_OFFSET)
                fee_sell_rate = FEE_SELL_MAKER

            if self.dry_run:
                order = {
                    "id": f"DRY-{int(time.time())}",
                    "status": "closed",
                    "price": exit_price,
                    "amount": amount
                }
                print(f"DRY RUN: Virtual Exit ({reason}) | {pair} | Price: {exit_price:,.2f}")
            else:
                params = {'post_only': True} if reason == "TAKE_PROFIT" else {}
                order = self.exchange.create_limit_sell_order(pair, amount, exit_price, params)

            # Audit log
            db.log_order(pair, "SELL", exit_price, amount, order.get('status'), order)

            order_status = order.get('status', 'closed')
            from config import (
                FEE_BUY_MAKER, FEE_BUY_TAKER, FEE_SELL_MAKER, FEE_SELL_TAKER,
                MIN_NET_PROFIT_IDR, AI_MIN_HISTORY_POINTS, SELL_TAKER_OFFSET, SELL_MAKER_OFFSET
            )
            
            # Tentukan fee jual berdasarkan strategi exit
            is_taker = reason in ("STOP_LOSS", "HARD_STOP_LOSS")
            fee_sell_rate = FEE_SELL_TAKER if is_taker else FEE_SELL_MAKER
            
            # Entry fee selalu Maker (karena bot antri beli)
            fee_buy = amount_idr * FEE_BUY_MAKER
            
            sell_value = amount * price
            fee_sell = sell_value * fee_sell_rate
            
            gross_pnl = (price - entry_price) * amount
            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
            
            if order_status in ['closed', 'canceled', 'done', 'fill']:
                db.delete_position(pair)
            else:
                db.update_position_status(pair, "EXITING", exit_order_id=order.get('id'))
            
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
            
            return order.get('id')
        except Exception as e:
            print(f"Critical error in execute_exit for {pair}: {e}")
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
            # (Sudah di-handle oleh main.py saat create_order, tapi bisa di-sync di sini jika perlu)
            
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
                
                # Jika di DB ada posisi tapi di bursa koinnya tidak ada (sudah terjual manual/error)
                if amount_on_exchange < (pos['amount'] * 0.9): # Toleransi 10% untuk pembulatan
                    log_message(f"🚨 Reality Desync Detected! {pair} not found on exchange. Marking as GHOST.")
                    db.delete_position(pair)
                    if pair in self.jaring_orders:
                        del self.jaring_orders[pair]
                        db.delete_shadow_order(pair)
            
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
            from config import JARING_ALLOCATION_PCT, JARING_DEPTH_PCT, INITIAL_CAPITAL
            
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