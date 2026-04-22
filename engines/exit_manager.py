import json
import os
import time

from config import POSITION_FILE, EXIT_LOG, TP_PCT, SL_PCT

class ExitManager:
    def __init__(self, exchange_client, dry_run: bool = False):
        self.exchange = exchange_client
        self.dry_run  = dry_run

    def get_open_positions(self):
        if os.path.exists(POSITION_FILE):
            with open(POSITION_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_positions(self, positions):
        with open(POSITION_FILE, 'w') as f:
            json.dump(positions, f)

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
            amount = data['amount']
            
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
                print(f"!!! TARGET REACHED: {pair} | Price: {current_price} | Action: SELL (TP)")
                order_id = self.execute_exit(pair, amount, current_price, "TAKE_PROFIT",
                                  entry_price=entry_price, amount_idr=data.get('amount_idr', 0))
                if order_id:
                    positions[pair]["status"] = "EXITING"
                    positions[pair]["exit_order_id"] = order_id

            # 2. Cek Stop Loss
            elif current_price <= sl_price:
                hard_sl_price = entry_price * (1 + (SL_PCT * 2))  # Hard SL: 2x SL_PCT
                
                # Jaring Pengaman Akhir (Hard Stop Loss)
                if current_price <= hard_sl_price:
                    print(f"!!! HARD STOP LOSS REACHED: {pair} | Price: {current_price} | Action: FORCED SELL")
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
                            print(f"[{pair}] SL OVERRIDE: Holding position. Bounce probability: {tq_prob*100:.0f}%")
                
                if override_sl:
                    continue # Lewati eksekusi SL, tahan posisi
                    
                print(f"!!! STOP LOSS REACHED: {pair} | Price: {current_price} | Action: SELL (SL)")
                order_id = self.execute_exit(pair, amount, current_price, "STOP_LOSS",
                                  entry_price=entry_price, amount_idr=data.get('amount_idr', 0))
                if order_id:
                    positions[pair]["status"] = "EXITING"
                    positions[pair]["exit_order_id"] = order_id

        self.save_positions(positions)

    def execute_exit(self, pair, amount, price, reason, entry_price=None, amount_idr=0):
        try:
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
                # ── Real Exit ────────────────────────────────────────────────
                params = {'post_only': True} if reason == "TAKE_PROFIT" else {}
                order = self.exchange.create_limit_sell_order(
                    pair, amount, exit_price, params
                )

            # Log transaksi (berlaku untuk dry run maupun live)
            log_entry = {
                "timestamp":   time.time(),
                "pair":        pair,
                "reason":      reason,
                "entry_price": entry_price,
                "exit_price":  exit_price,
                "amount_idr":  amount_idr,
                "order":       order,
                "dry_run":     self.dry_run,
            }
            with open(EXIT_LOG, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
                
            return order.get("id", f"DRY-{int(time.time())}")

        except Exception as e:
            print(f"Exit Error ({reason}): {e}")
            return None