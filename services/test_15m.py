from services.backtester.main import _load_active_profiles, _run_backtest_pair, _process_pair_worker
from services.backtester.db_backtest import get_connection
import pandas as pd
from datetime import datetime, timedelta, timezone

profiles = _load_active_profiles()
p = next(p for p in profiles if p['profile_name'] == 'anglingdharma')
p['buy_threshold'] = 4.5
p['target_tp_pct'] = 0.035
p['target_sl_pct'] = -0.02

start = datetime.now(timezone.utc) - timedelta(days=90)
end = datetime.now(timezone.utc)
pair_trades = _process_pair_worker('btcidr', start, end, [p])

print(f"Trades: {len(pair_trades)}")
if pair_trades:
    wins = len([t for t in pair_trades if t['pnl_idr'] > 0])
    pnl = sum([t['pnl_pct'] for t in pair_trades])
    print(f"WR: {wins/len(pair_trades):.2f} | PnL: {pnl:.2f}%")
