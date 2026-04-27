import sqlite3
import os

DB_PATH = 'data/algotrade.db'

def check_positions():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("--- Open Positions ---")
    cursor.execute("SELECT pair, entry_price, status, target_tp_pct, dry_run, amount_crypto, highest_price FROM positions")
    rows = cursor.fetchall()
    for row in rows:
        print(dict(row))
    
    print("\n--- Ticker History (Last Prices) ---")
    # Just checking a few pairs from positions if any
    pairs = [row['pair'] for row in rows]
    for pair in pairs:
        cursor.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (pair,))
        row = cursor.fetchone()
        if row:
            import json
            history = json.loads(row['history_json'])
            if history:
                print(f"{pair} last price: {history[-1]}")
    
    conn.close()

if __name__ == "__main__":
    check_positions()
