import sqlite3
import json

db_path = 'data/algotrade.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("--- Trade History (ETH) ---")
rows = cursor.execute("SELECT pair, pnl_idr, created_at FROM trade_history WHERE pair LIKE 'eth%' ORDER BY created_at DESC LIMIT 10").fetchall()
for r in rows:
    print(dict(r))

print("\n--- Trade Analytics (ETH) ---")
rows = cursor.execute("SELECT pair, pnl_idr, entry_timestamp FROM trade_analytics WHERE pair LIKE 'eth%' ORDER BY entry_timestamp DESC LIMIT 10").fetchall()
for r in rows:
    print(dict(r))

conn.close()
