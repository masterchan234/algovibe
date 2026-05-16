import sys

path = '/home/chan/projects/algovibe/app/domain/technicals.py'
with open(path, 'r') as f:
    lines = f.readlines()

# 1. Update get_technical_summary
get_tech_summary_update = """    # Price Velocity (Momentum Impulse)
    # Kecepatan harga dalam 3 candle terakhir untuk mendeteksi 'ledakan' harga.
    velocity = 0.0
    if n >= 4:
        velocity = (prices_list[-1] - prices_list[-4]) / prices_list[-4] * 100

    return {
        "rsi":          round(rsi, 1),
        "volatility":   round(vol, 2),
        "slope":        round(slope, 4),
        "velocity":     round(velocity, 4),
        "signal":       signal,
        "macd_line":    round(macd_line, 6),
        "macd_sig":     round(macd_sig, 6),
        "macd_hist":    round(macd_hist, 6),
        "bb_pct":       round(bb_pct, 4),
        "stoch_k":      round(stoch_k, 1),
        "stoch_d":      round(stoch_d, 1),
        "ema9":         round(ema9, 6),
        "ema21":        round(ema21, 6),
        "ema_cross":    ema_cross,
        "last_price":   round(float(prices_list[-1]), 6),
        "williams_r":   round(williams_r, 1),
        "roc":          round(roc, 4),
        "volume_ratio": round(volume_ratio, 3),
        "obv_slope":    round(obv_slope, 4),
    }
"""

# Find return block in get_technical_summary
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if "return {" in line and i > 370 and i < 480:
        start_idx = i
    if "}" in line and start_idx != -1 and i < 480:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    lines[start_idx:end_idx+1] = [get_tech_summary_update]
    print("SUCCESS: get_technical_summary updated.")
else:
    print("ERROR: get_technical_summary return block not found.")

with open(path, 'w') as f:
    f.writelines(lines)
