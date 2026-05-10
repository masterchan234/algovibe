#!/usr/bin/env python3
"""
Audit open positions to diagnose why some aren't executing TP.
Specifically check dry_run flag mismatch.
"""
import json
from app.infrastructure.database import Database

db = Database()
all_pos = db.get_open_positions()

print(f"\n📋 Auditing {len(all_pos)} open position(s):\n")

for pair, data in all_pos.items():
    dry_run = int(data.get('dry_run', 0))
    status = data.get('status', '?')
    entry_price = float(data.get('entry_price', 0))
    amount = float(data.get('amount_crypto', 0))
    
    print(f"  {pair.upper()}:")
    print(f"    • Status: {status}")
    print(f"    • Dry Run Flag: {dry_run} (0=live, 1=dry)")
    print(f"    • Entry: Rp {entry_price:,.2f} | Amount: {amount:.8f}")
    print()

# Check bot's current mode
try:
    import config
    print(f"\n🤖 Bot Runtime Mode:")
    print(f"  • DRY_RUN setting in config.py: {config.DRY_RUN}")
except:
    print("  ⚠️ Could not read config.DRY_RUN")
