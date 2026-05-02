from app.infrastructure.database import db
import json

def check():
    trades = db.get_recent_trades(limit=10)
    print(f"Total Recent Trades (Recent): {len(trades)}")
    
    perf = db.get_daily_performance(dry_run=1)
    print(f"Daily Performance (Dry Run): {json.dumps(perf, indent=2)}")

if __name__ == "__main__":
    check()
