import zmq
from shared.protos.trading_pb2 import Tick

ctx = zmq.Context()
sub = ctx.socket(zmq.SUB)
sub.connect("ipc:///tmp/market_stream.ipc")
sub.setsockopt(zmq.SUBSCRIBE, b"")

print("Waiting for tick...")
for i in range(10):
    topic, raw = sub.recv_multipart()
    if topic == b"tick":
        tick = Tick()
        tick.ParseFromString(raw)
        if b"usdt" in tick.symbol.lower().encode('utf-8'):
            print(f"Symbol: {tick.symbol}, Price: {tick.price}, Vol IDR: {tick.vol_idr}, Vol Coin: {tick.volume}")
            break
