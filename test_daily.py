import os
os.environ['EXCHANGE'] = 'binance'
from app.infrastructure.database import db
from datetime import datetime
today_str = datetime.utcnow().strftime('%Y-%m-%d')
db.save_state(f"daily_pnl_{today_str}", "60.0")
print(f"Set daily_pnl_{today_str} to 60.0")
