
import sys
import os
from datetime import datetime

# Root inside container is /app
sys.path.insert(0, "/app")

from services.backtester.db_backtest import get_connection

def check_data_availability():
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # 1. Check tables
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'ohlcv_%'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"Tables found: {tables}")
        
        for table in tables:
            print(f"\nAnalyzing {table}:")
            cur.execute(f"SELECT pair, MIN(ts), MAX(ts), COUNT(*) FROM {table} GROUP BY pair ORDER BY pair")
            rows = cur.fetchall()
            
            if not rows:
                print("  No data.")
                continue
                
            print(f"  {'Pair':<12} | {'From':<20} | {'To':<20} | {'Records'}")
            for r in rows:
                print(f"  {r[0]:<12} | {str(r[1]):<20} | {str(r[2]):<20} | {r[3]}")
        
        conn.close()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

if __name__ == "__main__":
    check_data_availability()
