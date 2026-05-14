import os
import sys
import time
import pandas as pd
from datetime import datetime, timedelta

# Set PYTHONPATH
sys.path.insert(0, os.path.abspath('.'))

from services.backtester.main import _get_signal_history, _run_backtest_pair

def run_profiling():
    print("Generating fake data...")
    # Generate 130,000 candles (approx 90 days of 1m)
    now = datetime.utcnow()
    candles = []
    base_price = 50000.0
    for i in range(130000):
        ts = now - timedelta(minutes=130000 - i)
        base_price += (hash(str(i)) % 100 - 50) / 10.0
        candles.append({
            'ts': ts,
            'open': base_price,
            'high': base_price + 5,
            'low': base_price - 5,
            'close': base_price + 1,
            'volume': 1.0
        })

    print(f"Data generated: {len(candles)} rows.")

    p = {
        "strategy_mode": "scalping",
        "timeframe_minutes": 1,
        "max_position_idr": 100000,
        "fee_buy_taker": 0.002,
        "fee_sell_taker": 0.002,
        "buy_threshold": 6.0,
        "sell_block_threshold": 5.0,
        "target_tp_pct": 0.035,
        "target_sl_pct": -0.035
    }

    start_t = time.time()
    hist = _get_signal_history("BTC_IDR", candles, p)
    end_t = time.time()
    print(f"_get_signal_history took {end_t - start_t:.4f} seconds")

    start_t = time.time()
    res = _run_backtest_pair(p, "BTC_IDR", candles, candles[0]['ts'], candles[-1]['ts'])
    end_t = time.time()
    print(f"_run_backtest_pair took {end_t - start_t:.4f} seconds")

if __name__ == "__main__":
    run_profiling()
