import sys

path = '/home/chan/projects/algovibe/app/domain/technicals.py'
with open(path, 'r') as f:
    content = f.read()

# 1. Update weights in docstring and variables
content = content.replace("10. Volume Ratio (w=1.5)", "10. Volume Ratio (w=3.0)")
content = content.replace("9. Order Book Pressure (w=2.0)", "9. Order Book Pressure (w=3.0)")
content = content.replace("BUY_THRESHOLD  = float(_at.get('buy_score_threshold', 6.0))", "BUY_THRESHOLD  = float(_at.get('buy_score_threshold', 5.0))")

# 2. Add Velocity logic
velocity_init = """    obv_sl  = float(tech.get('obv_slope', 0.0))     # OBV Slope % per 5 candle
    velocity = float(tech.get('velocity', 0.0))     # Price Velocity (% change last 3 candles)
"""
content = content.replace("    obv_sl  = float(tech.get('obv_slope', 0.0))     # OBV Slope (% per 5 candle)", velocity_init)

velocity_scoring = """    #  12. Price Velocity (Bobot 2.5 × sector_weight)
    # Deteksi ledakan harga instan. Ini adalah indikator 'Speed' untuk momentum.
    _w_vel = 2.5 * sw.get('vol', 1.0)
    if velocity > 0.3:
        buy_score += 2.5 * _w_vel
        signals.append(f"Velocity positive({velocity:.2f}%)")
    elif velocity > 0.1:
        buy_score += 1.0 * _w_vel
        signals.append(f"Velocity mild({velocity:.2f}%)")
    elif velocity < -0.3:
        sell_score += 2.5 * _w_vel
        signals.append(f"Velocity negative({velocity:.2f}%)")

    #  13. EMA-9 Extension Guard (Bobot -2.0 fixed penalty)
"""
# Replace old EMA-9 guard with new logic that considers velocity
old_ema_guard = """    #  12. EMA-9 Extension Guard (Bobot -2.0 fixed penalty) 
    # Jika harga melambung terlalu jauh di atas EMA-9 tanpa pullback,
    # risiko koreksi (mean-reversion) sangat tinggi.
    _ema_dist_pct = (last_price - ema9) / ema9 * 100.0 if ema9 > 0 else 0
    if _ema_dist_pct > 2.5:  # Harga > 2.5% di atas EMA-9
        buy_score -= 2.0
        signals.append(f"Vertical spike({_ema_dist_pct:.1f}% > EMA9) [PENALTY]")
    elif _ema_dist_pct < -2.5:
        sell_score -= 2.0
        signals.append(f"Vertical crash({_ema_dist_pct:.1f}% < EMA9) [PENALTY]")
"""

new_ema_guard = """    #  13. EMA-9 Extension Guard (Bobot -2.0 fixed penalty)
    # Jika harga melambung terlalu jauh di atas EMA-9 tanpa pullback,
    # risiko koreksi (mean-reversion) sangat tinggi.
    # [Momentum Fix] Jika velocity kencang (>0.5%), kurangi penalti agar tidak ketinggalan trend.
    _ema_dist_pct = (last_price - ema9) / ema9 * 100.0 if ema9 > 0 else 0
    if _ema_dist_pct > 2.5:
        penalty = 2.0 if velocity < 0.5 else 0.5
        buy_score -= penalty
        signals.append(f"Vertical spike({_ema_dist_pct:.1f}% > EMA9) [PENALTY={penalty}]")
    elif _ema_dist_pct < -2.5:
        sell_score -= 2.0
        signals.append(f"Vertical crash({_ema_dist_pct:.1f}% < EMA9) [PENALTY]")
"""

content = content.replace(old_ema_guard, velocity_scoring + new_ema_guard)

# Update Volume Ratio scoring weight
content = content.replace("buy_score += 1.5 * _w_vol", "buy_score += 3.0 * _w_vol")
content = content.replace("sell_score += 1.5 * _w_vol", "sell_score += 3.0 * _w_vol")

# Update Order Book scoring weight
content = content.replace("buy_score += 2.0 * _w_ob", "buy_score += 3.0 * _w_ob")
content = content.replace("sell_score += 2.0 * _w_ob", "sell_score += 3.0 * _w_ob")

with open(path, 'w') as f:
    f.writelines(content)
print("SUCCESS: derive_trade_signal updated.")
