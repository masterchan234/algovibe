import time
from app.infrastructure.ws_market import MarketDataWS
mws = MarketDataWS()
def on_tick(pair, data):
    if pair == 'btcusdt':
        print(f"BTCUSDT Tick: {data}")
mws.on_tick = on_tick
mws.start()
time.sleep(5)
