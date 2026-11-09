import os
os.environ['EXCHANGE'] = 'binance'
from app.infrastructure.ws_market import MarketDataWS
import time

mws = MarketDataWS()
mws.start()
time.sleep(5)
tickers = mws.get_tickers()
if tickers:
    pair = list(tickers.keys())[0]
    print("Found pair:", pair)
    print("Data:", tickers[pair])
else:
    print("No tickers")
mws.stop()
