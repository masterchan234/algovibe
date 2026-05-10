#!/usr/bin/env python3
"""
Check which pairs have fresh ticker data from the market WebSocket.
This will reveal why some positions skip the TP evaluation.
"""
from app.application.scalper_bot import ScalperBot
import time

# Initialize bot infrastructure (but don't run the full bot)
bot = ScalperBot.__new__(ScalperBot)
bot.market_ws = __import__('app.infrastructure.ws_market', fromlist=['MarketDataWS']).MarketDataWS()

# Start WebSocket in background
bot.market_ws.start()

# Wait for it to connect
if bot.market_ws.wait_ready(timeout=5):
    print("✅ WebSocket connected. Fetching tickers...\n")
    
    # Get all available tickers
    tickers = bot.market_ws.get_tickers()
    
    # Target pairs from the open positions
    target_pairs = ['bioidr', 'sxtidr', 'ondoidr', 'plumeidr', 'tnsridr']
    
    print(f"📊 Ticker Coverage Report ({len(tickers)} total pairs in cache):\n")
    
    for pair in target_pairs:
        if pair in tickers:
            ticker = tickers[pair]
            last_price = ticker.get('last', ticker.get('buy', 0))
            print(f"  ✅ {pair.upper()}: last={last_price:.2f} IDR")
        else:
            print(f"  ❌ {pair.upper()}: NOT IN TICKERS (will be skipped in exit monitor)")
    
    print(f"\n💡 Tickers missing from WebSocket will be skipped in exit evaluation!")
    bot.market_ws.stop()
else:
    print("❌ WebSocket failed to connect. Check if market_ws is running properly.")
