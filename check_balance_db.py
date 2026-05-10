#!/usr/bin/env python3
"""
Quick check: verify balance in DB right now.
"""
from app.infrastructure.database import Database

db = Database()

# Get latest IDR balance
try:
    balances = db.get_balances()
    if balances and 'IDR' in balances:
        idr = balances['IDR']
        print(f"IDR Balance in Database:")
        print(f"  Free: {idr.get('free', 0)} IDR")
        print(f"  Used: {idr.get('used', 0)} IDR")
        print(f"  Total: {idr.get('total', 0)} IDR")
    else:
        print("IDR balance not found in database")
except Exception as e:
    print(f"Error: {e}")
