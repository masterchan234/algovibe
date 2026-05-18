#!/usr/bin/env python3
"""
download_ohlcv.py — Script to download historical OHLCV data from Indodax
for the last N days with a specified timeframe and export to CSV.

Usage:
    python3 scripts/download_ohlcv.py --days 90 --timeframe 1 --output-dir data/ohlcv
"""

import os
import sys
import time
import csv
import argparse
from datetime import datetime, timezone
import requests

# Base endpoints
PAIRS_URL = "https://indodax.com/api/pairs"
OHLC_URL = "https://indodax.com/tradingview/history_v2"

# Defaults
DEFAULT_DAYS = 90
DEFAULT_TIMEFRAME = "1"  # "1" = 1 minute
DEFAULT_OUTPUT_DIR = "data/ohlcv"
RATE_LIMIT_DELAY = 0.4  # seconds between requests to respect 180 req/min limit
MAX_RETRIES = 3
BACKOFF_FACTOR = 2

def log_msg(category: str, msg: str):
    """Print standard Algovibe backend categorized logs without emojis."""
    print(f"[{category}] {msg}", flush=True)

def fetch_pairs() -> list:
    """Fetch all available pairs from Indodax public REST API."""
    log_msg("INFO", "Fetching pairs list from Indodax...")
    try:
        r = requests.get(PAIRS_URL, timeout=15)
        r.raise_for_status()
        pairs = r.json()
        log_msg("OK", f"Successfully loaded {len(pairs)} pairs from Indodax.")
        return pairs
    except Exception as e:
        log_msg("ERROR", f"Failed to fetch pairs from Indodax: {e}")
        sys.exit(1)

def get_ohlcv_data(symbol: str, tf: str, from_ts: int, to_ts: int) -> list:
    """Fetch OHLCV data for a single symbol with retries and rate limit handling."""
    params = {
        "symbol": symbol.upper(),
        "tf": tf,
        "from": from_ts,
        "to": to_ts
    }
    
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(OHLC_URL, params=params, timeout=30)
            
            # Handle rate limiting specifically
            if r.status_code == 429:
                log_msg("WARN", f"Rate limit (429) hit for {symbol}. Sleeping 60s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(60)
                continue
                
            # Handle server/other errors
            if r.status_code != 200:
                log_msg("WARN", f"Received HTTP {r.status_code} for {symbol} (attempt {attempt}/{MAX_RETRIES}). Retrying in {delay}s...")
                time.sleep(delay)
                delay *= BACKOFF_FACTOR
                continue
                
            data = r.json()
            if isinstance(data, list):
                return data
            else:
                log_msg("WARN", f"Unexpected JSON response format for {symbol} (not a list).")
                return []
                
        except requests.exceptions.Timeout:
            log_msg("WARN", f"Timeout fetching {symbol} (attempt {attempt}/{MAX_RETRIES}). Retrying in {delay}s...")
            time.sleep(delay)
            delay *= BACKOFF_FACTOR
        except Exception as e:
            log_msg("WARN", f"Network error fetching {symbol} (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in {delay}s...")
            time.sleep(delay)
            delay *= BACKOFF_FACTOR
            
    log_msg("ERROR", f"Exhausted all retry attempts for pair {symbol}.")
    return []

def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data from Indodax.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of historical days to download (default: 90)")
    parser.add_argument("--timeframe", "-t", type=str, default=DEFAULT_TIMEFRAME, help="Timeframe (1, 15, 30, 60, 240, 1D, 3D, 1W. default: 1)")
    parser.add_argument("--output-dir", "-o", type=str, default=DEFAULT_OUTPUT_DIR, help=f"Directory to save CSV files (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--limit", type=int, default=None, help="Limit to downloading only the first N pairs (useful for dry runs/testing)")
    parser.add_argument("--quote", type=str, default=None, choices=["idr", "usdt", "all"], help="Filter pairs by quote asset (idr, usdt, or all. default: all)")
    
    args = parser.parse_args()
    
    # Setup directories
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Calculate time window
    to_ts = int(time.time())
    from_ts = to_ts - (args.days * 24 * 60 * 60)
    
    # Display config
    log_msg("INFO", f"Starting historical downloader.")
    log_msg("INFO", f"Timeframe: {args.timeframe}m" if args.timeframe.isdigit() else f"Timeframe: {args.timeframe}")
    log_msg("INFO", f"Window   : {args.days} days (from {datetime.fromtimestamp(from_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} to {datetime.fromtimestamp(to_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})")
    log_msg("INFO", f"Output   : {args.output_dir}/")
    
    # Load all pairs
    all_pairs = fetch_pairs()
    
    # Filter pairs
    target_pairs = []
    for p in all_pairs:
        pair_id = str(p.get("id", "")).lower()
        base_currency = str(p.get("base_currency", "")).lower()
        
        is_idr = base_currency == "idr" or pair_id.endswith("idr")
        is_usdt = base_currency == "usdt" or pair_id.endswith("usdt")
        
        if args.quote == "idr" and not is_idr:
            continue
        if args.quote == "usdt" and not is_usdt:
            continue
        
        target_pairs.append(p)
        
    if args.limit:
        target_pairs = target_pairs[:args.limit]
        log_msg("INFO", f"Limit applied: processing first {args.limit} pairs.")
        
    total_pairs = len(target_pairs)
    log_msg("INFO", f"Target pairs to download: {total_pairs}")
    
    success_count = 0
    empty_count = 0
    fail_count = 0
    
    start_time = time.time()
    
    for idx, p in enumerate(target_pairs, 1):
        pair_id = str(p.get("id", "")).lower()
        symbol = str(p.get("symbol", "")).upper()
        
        log_msg("INFO", f"[{idx}/{total_pairs}] Processing {symbol} ({pair_id})...")
        
        # Get candles
        candles = get_ohlcv_data(pair_id, args.timeframe, from_ts, to_ts)
        
        if not candles:
            # We already logged a warning/error in get_ohlcv_data or it returned empty
            if candles == []:
                log_msg("WARN", f"No data returned for {symbol}.")
                empty_count += 1
            else:
                fail_count += 1
            time.sleep(RATE_LIMIT_DELAY)
            continue
            
        # Write to CSV
        tf_suffix = f"{args.timeframe}m" if args.timeframe.isdigit() else args.timeframe
        filename = f"{pair_id}_{tf_suffix}_{args.days}d.csv"
        filepath = os.path.join(args.output_dir, filename)
        
        try:
            with open(filepath, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                # Header
                writer.writerow(["timestamp", "datetime", "open", "high", "low", "close", "volume"])
                
                for c in candles:
                    ts = int(c.get("Time", 0))
                    # Convert to datetime string
                    dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    
                    writer.writerow([
                        ts,
                        dt_str,
                        float(c.get("Open", 0)),
                        float(c.get("High", 0)),
                        float(c.get("Low", 0)),
                        float(c.get("Close", 0)),
                        float(c.get("Volume", 0) or 0)
                    ])
            
            log_msg("OK", f"Downloaded {len(candles)} candles for {symbol} -> {filepath}")
            success_count += 1
        except Exception as e:
            log_msg("ERROR", f"Failed to write CSV for {symbol}: {e}")
            fail_count += 1
            
        # Standard polite delay between requests to stay below 180 request/minute public limit
        time.sleep(RATE_LIMIT_DELAY)
        
    elapsed = time.time() - start_time
    log_msg("OK", f"Completed historical OHLCV download in {elapsed:.2f} seconds.")
    log_msg("OK", f"Summary: Total: {total_pairs} | Success: {success_count} | Empty/No Data: {empty_count} | Failed: {fail_count}")

if __name__ == "__main__":
    main()
