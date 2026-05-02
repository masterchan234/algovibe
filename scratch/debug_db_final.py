from app.infrastructure.database import db
import sqlite3
import os

def debug_db():
    print(f"DB PATH: {db.db_path}")
    print(f"File exists: {os.path.exists(db.db_path)}")
    
    conn = sqlite3.connect(db.db_path)
    cursor = conn.cursor()
    
    # Check all trades
    cursor.execute("SELECT count(*), dry_run, reason FROM trade_history GROUP BY dry_run, reason")
    rows = cursor.fetchall()
    print("Trade History breakdown (count, dry_run, reason):")
    for r in rows:
        print(r)
    
    # Exact query from server.py
    cursor.execute("SELECT COUNT(*) FROM trade_history WHERE dry_run = 1 AND reason != 'BACKTEST'")
    count = cursor.fetchone()[0]
    print(f"\nResult of server.py query: {count}")

if __name__ == "__main__":
    debug_db()
