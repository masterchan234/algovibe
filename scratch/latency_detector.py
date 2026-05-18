#!/usr/bin/env python3
"""
scratch/latency_detector.py — Real-time Lead-Lag Latency Arbitrage Research Script
================================================================================
Connects to both Binance and Indodax WebSocket feeds in parallel, logs
high-resolution tick-by-tick mid-prices, computes their cross-correlation
over various millisecond lags, and simulates real-world latency arbitrage.

Author: Senior Python Engineer & Quantitative Analyst
"""

import os
import sys
import time
import json
import threading
import numpy as np
import pandas as pd
import websocket
from typing import List, Tuple

# Allow importing from project root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.ws_market import WS_MARKET_URL, WS_MARKET_TOKEN

# ----------------- CONFIGURATION -----------------
RUN_DURATION_SECONDS = 30  # Duration to run the WS collection
RESAMPLE_GRID_MS = 50      # Time step for interpolation (e.g. 50ms)
MAX_LAG_MS = 3000          # Max lag to test (-3s to +3s)
LAG_STEP_MS = 50          # Step for lag correlation
FEE_TAKER_INDODAX = 0.0051 # 0.51% Indodax taker fee

# Shared queues/lists to hold tick updates
# Format: (local_receive_time, server_timestamp, price)
binance_ticks: List[Tuple[float, float, float]] = []
indodax_ticks: List[Tuple[float, float, float]] = []

binance_connected = threading.Event()
indodax_connected = threading.Event()
stop_event = threading.Event()

lock = threading.Lock()

# ----------------- BINANCE WS CLIENT -----------------
def run_binance_ws():
    """Connects to Binance BookTicker stream for ultra-low latency best bid/ask."""
    url = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
    
    def on_message(ws, raw):
        if stop_event.is_set():
            return
        
        try:
            data = json.loads(raw)
            # data: { "u":..., "s":"BTCUSDT", "b":"best_bid", "B":"...", "a":"best_ask", "A":"..." }
            local_ts = time.time() # Epoch seconds with microsecond resolution
            
            # Binance does not give transaction time in bookTicker payload directly, 
            # but we can use the local_ts as highly accurate reception timestamp.
            best_bid = float(data["b"])
            best_ask = float(data["a"])
            mid_price = (best_bid + best_ask) / 2.0
            
            if mid_price > 0:
                with lock:
                    binance_ticks.append((local_ts, local_ts * 1000.0, mid_price))
                if not binance_connected.is_set():
                    binance_connected.set()
        except Exception as e:
            pass

    def on_open(ws):
        print("[BINANCE-WS] Connected to BTCUSDT stream.")

    def on_error(ws, error):
        print(f"[BINANCE-WS] Error: {error}")

    def on_close(ws, code, reason):
        print("[BINANCE-WS] Connection closed.")

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(ping_interval=10, ping_timeout=5)


# ----------------- INDODAX WS CLIENT -----------------
def run_indodax_ws():
    """Connects to Indodax WS and subscribes to BTCIDR orderbook."""
    def on_message(ws, raw):
        if stop_event.is_set():
            return
        
        try:
            msg = json.loads(raw)
            local_ts = time.time()
            
            # Handle Auth OK Response
            if "result" in msg and isinstance(msg.get("result"), dict) and "client" in msg.get("result", {}):
                print("[INDODAX-WS] Authenticated successfully. Subscribing to BTCIDR orderbook...")
                # Subscribe to BTCIDR orderbook for instant bid/ask changes
                sub_msg = {
                    "method": 1,
                    "params": {"channel": "market:order-book-btcidr"},
                    "id": 101,
                }
                ws.send(json.dumps(sub_msg))
                return
            
            # Handle Stream Data
            result = msg.get("result", {})
            if isinstance(result, dict):
                channel = result.get("channel", "")
                if channel == "market:order-book-btcidr":
                    # Fix: Indodax nest the actual order book under another 'data' key inside result['data']
                    data_wrap = result.get("data", {}).get("data", {})
                    bids = data_wrap.get("bid", [])
                    asks = data_wrap.get("ask", [])
                    
                    if bids and asks:
                        best_bid = float(bids[0]["price"])
                        best_ask = float(asks[0]["price"])
                        mid_price = (best_bid + best_ask) / 2.0
                        
                        # Estimate server flight time if epoch/timestamp is present
                        server_ts = local_ts * 1000.0
                        epoch = result.get("data", {}).get("epoch")
                        if epoch:
                            server_ts = float(epoch) * 1000.0
                            
                        if mid_price > 0:
                            with lock:
                                indodax_ticks.append((local_ts, server_ts, mid_price))
                            if not indodax_connected.is_set():
                                indodax_connected.set()
        except Exception as e:
            print(f"[INDODAX-WS-ERR] Exception in on_message: {e}")

    def on_open(ws):
        print("[INDODAX-WS] Connected. Authenticating...")
        auth_msg = {
            "params": {"token": WS_MARKET_TOKEN},
            "id": 1,
        }
        ws.send(json.dumps(auth_msg))

    def on_error(ws, error):
        print(f"[INDODAX-WS] Error: {error}")

    def on_close(ws, code, reason):
        print("[INDODAX-WS] Connection closed.")

    ws = websocket.WebSocketApp(
        WS_MARKET_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(suppress_origin=True, ping_interval=10, ping_timeout=5)


# ----------------- ANALYZER & SIMULATOR -----------------
def analyze_and_simulate():
    """Runs the cross-correlation quantitative analysis and trade simulator."""
    global binance_ticks, indodax_ticks
    
    with lock:
        b_len = len(binance_ticks)
        i_len = len(indodax_ticks)
        
    print(f"\n[OK] Data collection completed.")
    print(f"  Binance ticks gathered: {b_len}")
    print(f"  Indodax ticks gathered: {i_len}")
    
    if b_len < 10 or i_len < 10:
        print("[ERROR] Insufficient tick updates captured on one or both exchanges. Please try again.")
        return
    
    # 1. Convert to DataFrames
    df_b = pd.DataFrame(binance_ticks, columns=["local_ts", "server_ts", "price_binance"])
    df_i = pd.DataFrame(indodax_ticks, columns=["local_ts", "server_ts", "price_indodax"])
    
    # Use localized high-resolution timestamps
    df_b["time"] = pd.to_datetime(df_b["local_ts"], unit="s")
    df_i["time"] = pd.to_datetime(df_i["local_ts"], unit="s")
    
    df_b = df_b.set_index("time")
    df_i = df_i.set_index("time")
    
    # 2. Resample onto a regular millisecond grid (e.g. 50ms grid)
    start_time = max(df_b.index.min(), df_i.index.min())
    end_time = min(df_b.index.max(), df_i.index.max())
    
    grid = pd.date_range(start=start_time, end=end_time, freq=f"{RESAMPLE_GRID_MS}ms")
    
    # Reindex and forward-fill to simulate real-time stale pricing
    series_b = df_b["price_binance"].reindex(grid, method="ffill")
    series_i = df_i["price_indodax"].reindex(grid, method="ffill")
    
    # Drop any initial NaNs if grid started before first tick arrival
    valid_mask = series_b.notna() & series_i.notna()
    series_b = series_b[valid_mask]
    series_i = series_i[valid_mask]
    
    if len(series_b) < 50:
        print("[ERROR] Overlapping timeline between exchanges is too short. Try running longer.")
        return
        
    # Standardize (percentage change from the starting price of this session)
    norm_b = (series_b - series_b.iloc[0]) / series_b.iloc[0] * 100.0
    norm_i = (series_i - series_i.iloc[0]) / series_i.iloc[0] * 100.0
    
    # 3. Compute Cross-Correlation over Lags
    # Corr(lag) measures correlation of Binance(t) and Indodax(t + lag).
    # If lag > 0 (e.g. +500ms), it means Binance price is correlated with Indodax price 500ms in the future.
    # Therefore, Binance LEADS Indodax by 500ms.
    lags = range(-MAX_LAG_MS, MAX_LAG_MS + 1, LAG_STEP_MS)
    correlations = []
    
    # Resampled grid steps per millisecond
    steps_per_ms = 1.0 / RESAMPLE_GRID_MS
    
    for lag_ms in lags:
        # Calculate shift steps in terms of grid indices
        shift_steps = int(round(lag_ms * steps_per_ms))
        
        # Shift Indodax backwards to align with past Binance, or forwards
        shifted_i = norm_i.shift(-shift_steps)
        corr = norm_b.corr(shifted_i)
        correlations.append((lag_ms, corr if not np.isnan(corr) else 0.0))
        
    # Find the peak correlation
    correlations.sort(key=lambda x: x[1], reverse=True)
    best_lag, best_corr = correlations[0]
    
    print("\n" + "=" * 60)
    print("           LEAD-LAG QUANTITATIVE ANALYSIS REPORT")
    print("=" * 60)
    print(f"Resampling Interval : {RESAMPLE_GRID_MS} ms")
    print(f"Data Session Length : {(end_time - start_time).total_seconds():.2f} seconds")
    print(f"Optimal Lead/Lag    : {best_lag} ms")
    print(f"Peak Correlation R  : {best_corr:.4f}")
    
    if best_lag > 0:
        print(f"Verdict             : [VALID] Binance LEADS Indodax by {best_lag} ms!")
    elif best_lag < 0:
        print(f"Verdict             : [REVERSED] Indodax leads Binance by {abs(best_lag)} ms! (Highly unusual/noise)")
    else:
        print(f"Verdict             : [CO-MOVEMENT] Co-aligned prices. No latency gap found.")
        
    # Print a small text graph of the correlation profile
    print("\nCorrelation Waveform Profile:")
    # Get a subset around the peak or middle to display
    display_lags = [x for x in correlations if -1500 <= x[0] <= 1500]
    display_lags.sort(key=lambda x: x[0])
    
    for lag, corr in display_lags:
        # Print a tiny bar chart
        bar_len = int(abs(corr) * 20)
        bar = ("+" if corr >= 0 else "-") * bar_len
        peak_indicator = " <== PEAK LEAD" if lag == best_lag else ""
        print(f"  Lag {lag:+5d} ms: [{bar:<20}] {corr:+.4f}{peak_indicator}")
        
    # 4. Latency Arbitrage Profitability Simulator
    # Simulates: "When Binance changes by X% in Y milliseconds, we place a Market Order on Indodax
    # and hold it until Indodax adjusts to the new price level (or Y ms later)."
    print("\n" + "=" * 60)
    print("          LATENCY ARBITRAGE SIMULATION (BACKTEST)")
    print("=" * 60)
    
    # Parameters for the signal trigger
    trigger_thresholds = [0.10, 0.20, 0.35, 0.50] # % Binance movement
    trigger_window_ms = 400                      # Lookback window on Binance
    hold_period_ms = max(best_lag, 500)          # Hold period on Indodax
    
    for thresh in trigger_thresholds:
        trades = []
        total_pnl = 0.0
        wins = 0
        
        # Step through the resampled series
        steps_window = int(round(trigger_window_ms * steps_per_ms))
        steps_hold = int(round(hold_period_ms * steps_per_ms))
        
        i = steps_window
        while i < len(norm_b) - steps_hold:
            # Check price change on Binance over the lookback window
            binance_delta = norm_b.iloc[i] - norm_b.iloc[i - steps_window]
            
            if binance_delta >= thresh:
                # Trigger a simulated BUY on Indodax
                buy_price = series_i.iloc[i]
                # Sell after hold period
                sell_price = series_i.iloc[i + steps_hold]
                
                # Raw return
                trade_return = (sell_price - buy_price) / buy_price
                # Subtract double transaction fee (taker buy + taker/maker sell)
                net_pnl = trade_return - FEE_TAKER_INDODAX
                
                trades.append(net_pnl)
                total_pnl += net_pnl
                if net_pnl > 0:
                    wins += 1
                    
                # Skip forward to avoid overlapping trade signals
                i += steps_hold
            else:
                i += 1
                
        num_trades = len(trades)
        win_rate = (wins / num_trades * 100) if num_trades > 0 else 0.0
        avg_pnl = (total_pnl / num_trades * 100) if num_trades > 0 else 0.0
        
        print(f"Trigger Threshold: {thresh:+.2f}% change on Binance in {trigger_window_ms}ms")
        print(f"  Simulated Trades : {num_trades}")
        print(f"  Win Rate         : {win_rate:.1f}%")
        print(f"  Average Net PnL  : {avg_pnl:+.4f}% (After 0.51% Indodax Fee)")
        print(f"  Total Session PnL: {total_pnl * 100:+.4f}%")
        print("-" * 50)
        
    print("\n[RECOMMENDATION]")
    if best_corr > 0.6 and best_lag > 150:
        print(f"  * High alpha opportunity! The latency gap of {best_lag}ms is large enough to build a signal.")
        print(f"  * Recommended to integrate `services/market_pipe/providers/binance.py` to capture Tokyo feeds.")
    else:
        print("  * Low direct latency discrepancy or high market co-alignment today.")
        print("  * Consider running this script during periods of high US/Global volatility (e.g. US market open).")
    print("=" * 60)


# ----------------- MAIN RUNNER -----------------
def main():
    print("=" * 60)
    print("  AlgoVibe Latency Arbitrage & Lead-Lag Detector")
    print("=" * 60)
    print("Starting dual WebSocket listeners...")
    print(f"Collecting high-resolution tick data for {RUN_DURATION_SECONDS} seconds...")
    
    t_binance = threading.Thread(target=run_binance_ws, daemon=True)
    t_indodax = threading.Thread(target=run_indodax_ws, daemon=True)
    
    t_binance.start()
    t_indodax.start()
    
    # Wait for both websockets to establish their first connection
    print("Waiting for streams to become active...")
    b_ready = binance_connected.wait(timeout=10)
    i_ready = indodax_connected.wait(timeout=10)
    
    if not b_ready:
        print("[WARN] Binance stream did not receive ticks in 10s.")
    if not i_ready:
        print("[WARN] Indodax stream did not receive ticks in 10s. Verify network or WS Token.")
        
    if b_ready or i_ready:
        print("[OK] Running collection phase...")
        try:
            for remaining in range(RUN_DURATION_SECONDS, 0, -1):
                if remaining % 5 == 0 or remaining <= 5:
                    print(f"  Remaining: {remaining}s...")
                time.sleep(1)
        except KeyboardInterrupt:
            print("[INFO] Interrupted by user.")
    
    # Stop streams
    stop_event.set()
    print("Stopping connections and starting mathematical processing...")
    
    # Run analysis
    analyze_and_simulate()


if __name__ == "__main__":
    main()
