#!/usr/bin/env python3
"""
Audit balance data dalam database vs log bot.
"""
from app.infrastructure.database import Database
import json

db = Database()

# 1. Check balance di state
cached_idr = db.get_state("cached_idr_balance")
print(f"Bot Cached Balance (DB state): {cached_idr}")

# 2. Check balances table (jika ada)
try:
    # Query database untuk balance terbaru
    balances_data = db.db.execute(
        "SELECT asset, free, used, total FROM balances ORDER BY updated_at DESC LIMIT 1"
    ).fetchall()
    if balances_data:
        print("\nBalances Table (latest):")
        for row in balances_data:
            print(f"  {row}")
    else:
        print("\nBalances Table: EMPTY")
except Exception as e:
    print(f"Error querying balances: {e}")

# 3. Check what cache says
print(f"\nBot State - cached_idr_balance: {cached_idr}")
