import json
import os
import time
from datetime import datetime

from config import TP_PCT, SL_PCT, PENDING_BUY_TIMEOUT_MINUTES, PENDING_BUY_MAX_SPREAD_PCT
from engines.telegram_bot import send_telegram_message
from engines.database import db

class ExitManager:
    def __init__(self, exchange_client, dry_run: bool = False):
        self.exchange = exchange_client
        self.dry_run  = dry_run

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
            from config import FEE_PER_SIDE, MIN_NET_PROFIT_IDR
            
            # Base TP calculation from percentage
            base_tp_price = entry_price * (1 + TP_PCT)
            
            # Dynamic TP calculation for exact Net Profit (300 IDR)
            # Rumus: (Target_IDR + Entry_Value * (1 + Fee)) / (Amount * (1 - Fee))
            exact_tp_price = (MIN_NET_PROFIT_IDR + (entry_price * amount * (1 + FEE_PER_SIDE))) / (amount * (1 - FEE_PER_SIDE))
            
            # Gunakan harga TP tertinggi antara persentase baseline (1.5%) atau target minimum 300 IDR
            tp_price = max(base_tp_price, exact_tp_price)
            
            sl_price = entry_price * (1 + SL_PCT)

            # 1. Cek Take Profit
            if current_price >= tp_price:
                msg = f"🎯 *TAKE PROFIT* 🎯\nPair: `{pair}`\nHarga Jual: Rp {current_price:,.2f}"
                print(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
                
                order_id = self.execute_exit(pair, amount, current_price, "TAKE_PROFIT",
                                  entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                if order_id:
                    positions[pair]["status"] = "EXITING"
                    positions[pair]["exit_order_id"] = order_id

            # 2. Cek Stop Loss
            elif current_price <= sl_price:
                hard_sl_price = entry_price * (1 + (SL_PCT * 2))  # Hard SL: 2x SL_PCT
                
                # Jaring Pengaman Akhir (Hard Stop Loss)
                if current_price <= hard_sl_price:
                    msg = f"🚨 *HARD STOP LOSS* 🚨\nPair: `{pair}`\nHarga Jual: Rp {current_price:,.2f}\nAction: FORCED SELL"
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, "HARD_STOP_LOSS",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0))
                    if order_id:
                        positions[pair]["status"] = "EXITING"
                        positions[pair]["exit_order_id"] = order_id
                    continue
                
                # Predictive SL Override (Coba deteksi pantulan/fake dump)
                override_sl = False
                if get_prediction_fn and price_history and pair in price_history:
                    history = price_history[pair]
                    if len(history) >= 15: # Pastikan sampel cukup untuk AI
                        tq_prob = get_prediction_fn(history)
                        if tq_prob > 0.70:
                            override_sl = True
                            msg = f"🛡 *SL OVERRIDE* 🛡\nPair: `{pair}`\nProbabilitas Bounce: {tq_prob*100:.0f}%\nAction: HOLDING POSITION"
                            print(msg.replace('*', '').replace('\n', ' | '))
                            send_telegram_message(msg)
                
                if override_sl:
                    continue # Lewati eksekusi SL, tahan posisi
                    
                msg = f"⚠️ *STOP LOSS* ⚠️\nPair: `{pair}`\nHarga Jual: Rp {current_price:,.2f}"
                print(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
                
                order_id = self.execute_exit(pair, amount, current_price, "STOP_LOSS",
                                  entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                if order_id:
                    positions[pair]["status"] = "EXITING"
                    positions[pair]["exit_order_id"] = order_id

        self.save_positions(positions)

    def execute_exit(self, pair, amount, price, reason, entry_price=None, amount_idr=0, position_data=None):
        try:
            from config import FEE_PER_SIDE
            if reason in ("STOP_LOSS", "HARD_STOP_LOSS"):
                exit_price = price * 0.9500  # SL: Limit order set 5% BELOW market to instantly act as a Taker/Market order
            else:
                exit_price = price * 1.0005  # TP: Maker Sell offset +0.05%

            if self.dry_run:
                # ── Simulasi Exit ─────────────────────────────────────────────
                order = {
                    "id":     f"DRY-{int(time.time())}",
                    "symbol": pair,
                    "side":   "sell",
                    "price":  exit_price,
                    "amount": amount,
                    "status": "closed",
                    "dry_run": True,
                }
                print(
                    f"DRY RUN: Virtual Exit ({reason}) | {pair} | "
                    f"Price: {exit_price:,.2f} | Crypto: {amount:.6f}"
                )
            else:
                params = {'post_only': True} if reason == "TAKE_PROFIT" else {}
                order = self.exchange.create_limit_sell_order(pair, amount, exit_price, params)

            order_status = order.get('status', 'closed')
            if order_status in ['closed', 'canceled', 'done', 'fill']:
                db.delete_position(pair)
            else:
                db.update_position_status(pair, "EXITING", exit_order_id=order.get('id'))
            
            # Log to Database Audit
            db.log_order(pair, "SELL", exit_price, amount, order.get('status'), order)
            
            gross_pnl = (price - entry_price) * amount
            fee_buy = amount_idr * FEE_PER_SIDE
            sell_value = amount * price
            fee_sell = sell_value * FEE_PER_SIDE
            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
            
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

            except Exception as db_err:
                print(f"Failed to save trade history/state for {pair}: {db_err}")
                
            return order.get("id", f"DRY-{int(time.time())}")

        except Exception as e:
            print(f"Exit Error ({reason}): {e}")
            return None