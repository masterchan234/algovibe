#!/usr/bin/env python3
"""Quick test of news maker market headline generation."""

import os
import sys
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.getcwd())

from app.infrastructure.database import db
from services.news_maker.main import NewsMaker

async def test():
    maker = NewsMaker()
    if not maker._engine_enabled:
        print("❌ LLM Engine not initialized")
        return
    
    print("✅ News Maker initialized")
    print(f"Current time (WIB): {datetime.now(ZoneInfo('Asia/Jakarta'))}")
    
    # Test market context build
    context = maker._build_market_context()
    print("\n=== Market Context ===")
    print(context)
    
    # Simulate some tickers for testing
    maker._tickers = {
        "btc": {"price": 45000000, "ch_1h": 2.5, "ch_24h": 8.5, "vol_idr": 500e9},
        "eth": {"price": 2500000, "ch_1h": -1.2, "ch_24h": -5.3, "vol_idr": 300e9},
        "xrp": {"price": 3500, "ch_1h": 15.8, "ch_24h": 35.5, "vol_idr": 200e9},
    }
    
    # Test market mover detection
    movers = maker._detect_significant_movers()
    if movers:
        coin, data = movers
        print(f"\n=== Detected Mover ===")
        print(f"Coin: {coin.upper()}")
        print(f"Change 24h: {data['pct_change']:+.1f}%")
        print(f"Score: {data['score']:.1f}")
        
        # Test headline generation
        print(f"\n=== Generating Headline ===")
        await maker._generate_market_headline(coin, data)
        
        # Check if saved
        with db.get_connection() as conn:
            rows = conn.execute("SELECT id, title, LENGTH(content) FROM intelligence_reports ORDER BY id DESC LIMIT 1").fetchall()
            if rows:
                rid, title, clen = rows[0]
                print(f"✅ Article saved! ID={rid}, Title={title[:60]}, ContentLen={clen}")
            else:
                print("❌ No article saved")
    else:
        print("\n❌ No significant movers detected")

if __name__ == "__main__":
    asyncio.run(test())
