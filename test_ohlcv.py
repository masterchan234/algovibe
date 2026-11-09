import sys
sys.path.append('/home/chan/projects/algovibe')
from dashboard.server import _fetch_ohlcv_data
from app.domain.technicals import get_technical_summary

data = _fetch_ohlcv_data("btcusdt", "15m")
print(f"Got {len(data)} candles")
if data:
    prices = [c[4] for c in data]
    volumes = [c[5] for c in data]
    tech = get_technical_summary(prices, volumes=volumes)
    print("Tech:", tech)
