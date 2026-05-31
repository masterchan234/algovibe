from dashboard.server import mws
import time

print("Waiting for tickers...")
time.sleep(3)
tickers = mws.get_tickers()
if 'btcusdt' in tickers:
    print("BTCUSDT:", tickers['btcusdt'])
else:
    print("No BTCUSDT yet...")
