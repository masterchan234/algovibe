import sys

path = '/home/chan/projects/algovibe/app/application/exit_manager.py'
with open(path, 'r') as f:
    content = f.read()

# Define TTP Constants (can be moved to config later)
ttp_constants = """
                # --- [TRAILING TAKE PROFIT LOGIC] ---
                # Menggunakan highest_price dan trailing_sl_price dari DB
                current_highest = float(data.get('highest_price', 0) or 0)
                if current_price > current_highest:
                    current_highest = current_price
                    data['highest_price'] = current_highest
                    # Trailing delta: 0.8% dari peak
                    data['trailing_sl_price'] = current_highest * (1 - 0.008)
                    db.upsert_position(pair, data)

                tp_activation_price = entry_price * (1 + dynamic_tp_pct)
                trailing_stop_price = float(data.get('trailing_sl_price', 0) or 0)
                
                # Sinyal Exit TTP: Sudah pernah lewat TP, dan sekarang turun di bawah trailing stop
                is_ttp_exit = (current_highest >= tp_activation_price) and (current_price <= trailing_stop_price)
"""

# Find the TP evaluation block
old_tp_block = """                # 2. Take Profit (Hard TP)
                # Evaluasi menggunakan BID price — market sell fill di sisi bid, bukan 'last' trade.
                # Mencegah false-positive saat 'last' spike di atas entry tapi bid masih di bawah.
                _tp_ticker = current_tickers.get(pair) or {}
                _tp_bid = float(_tp_ticker.get('buy') or 0)
                tp_eval_price = _tp_bid if _tp_bid > 0 else current_price
                log_message(
                    f"[EXIT-STATUS] [{pair}] TP-Status: tp_eval_price={tp_eval_price:.0f} (bid) vs tp_price={tp_price:.0f} | "
                    f"net_pnl_idr={net_pnl_idr:.0f} | profit_pct={current_profit_pct*100:.2f}%"
                )

                if tp_eval_price >= tp_price:"""

new_tp_block = """                # 2. Take Profit (Trailing TP & Hard TP)
                _tp_ticker = current_tickers.get(pair) or {}
                _tp_bid = float(_tp_ticker.get('buy') or 0)
                tp_eval_price = _tp_bid if _tp_bid > 0 else current_price

                # --- [TRAILING TAKE PROFIT LOGIC] ---
                current_highest = float(data.get('highest_price', 0) or 0)
                if tp_eval_price > current_highest:
                    current_highest = tp_eval_price
                    data['highest_price'] = current_highest
                    # Trailing delta: 1.0% dari peak (sedikit longgar agar tidak kena noise)
                    data['trailing_sl_price'] = current_highest * (1 - 0.01)
                    db.upsert_position(pair, data)

                tp_activation_price = entry_price * (1 + dynamic_tp_pct)
                trailing_stop_price = float(data.get('trailing_sl_price', 0) or 0)
                
                # Sinyal Exit TTP: Sudah pernah lewat TP, dan sekarang turun di bawah trailing stop
                is_ttp_exit = (current_highest >= tp_activation_price) and (tp_eval_price <= trailing_stop_price)

                log_message(
                    f"[EXIT-STATUS] [{pair}] PnL:{current_profit_pct*100:.2f}% | "
                    f"Peak:{((current_highest/entry_price)-1)*100:.2f}% | "
                    f"TTP-Stop:{((trailing_stop_price/entry_price)-1)*100:.2f}%"
                )

                if is_ttp_exit:
                    log_message(f"[TRAILING-TP] {pair}: Momentum melandai. Exit secured at {current_profit_pct*100:.2f}% (Peak was {((current_highest/entry_price)-1)*100:.2f}%)")
                
                # Hard TP fallback jika tidak pakai TTP
                if is_ttp_exit or (tp_eval_price >= tp_price and current_highest < tp_activation_price):"""

content = content.replace(old_tp_block, new_tp_block)

with open(path, 'w') as f:
    f.writelines(content)
print("SUCCESS: Trailing TP implemented in exit_manager.py")
