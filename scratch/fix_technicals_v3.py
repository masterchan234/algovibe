import sys

path = '/home/chan/projects/algovibe/app/domain/technicals.py'
with open(path, 'r') as f:
    lines = f.readlines()

# Reconstruct get_technical_summary correctly
fixed_start = """def get_technical_summary(prices, volumes=None):
    \"\"\"
    Kembalikan rangkuman teknikal lengkap dari semua indikator.
    Data ini dipakai oleh derive_trade_signal() untuk keputusan akhir.

    Indikator:
      RSI, MACD, Bollinger Bands, Stochastic RSI, EMA Crossover,
      EMA Slope, Williams %R, Rate of Change (ROC),
      Volume Ratio (vol sekarang / MA-20), OBV slope.

    Args:
      prices  : list harga penutupan
      volumes : list volume per candle (opsional) — aktifkan volume indicators
    \"\"\"
    if not prices or len(prices) < 2:
        return {
            "rsi": 50, "volatility": 0, "signal": "NEUTRAL", "slope": 0,
            "velocity": 0, "macd_hist": 0, "bb_pct": 0.5, "stoch_k": 50, "stoch_d": 50,
            "ema9": 0, "ema21": 0, "ema_cross": "NEUTRAL",
            "last_price": 0, "williams_r": -50, "roc": 0,
            "volume_ratio": 1.0, "obv_slope": 0.0,
        }

    prices_list = list(prices)
    n = len(prices_list)

    # Price Velocity (Momentum Impulse)
    # Kecepatan harga dalam 3 candle terakhir untuk mendeteksi 'ledakan' harga.
    velocity = 0.0
    if n >= 4:
        velocity = (prices_list[-1] - prices_list[-4]) / prices_list[-4] * 100

    # RSI 14
"""

# Find the start of get_technical_summary and replace until RSI 14
start_line = -1
for i, line in enumerate(lines):
    if "def get_technical_summary(prices, volumes=None):" in line:
        start_line = i
        break

rsi_line = -1
for i, line in enumerate(lines):
    if "# RSI 14" in line and i > start_line:
        rsi_line = i
        break

if start_line != -1 and rsi_line != -1:
    lines[start_line:rsi_line+1] = [fixed_start]
    with open(path, 'w') as f:
        f.writelines(lines)
    print("SUCCESS: Fixed get_technical_summary.")
else:
    print(f"ERROR: Could not find markers. start={start_line}, rsi={rsi_line}")
