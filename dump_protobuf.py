import sys
sys.path.append("/home/chan/projects/algovibe")
from shared.protos.trading_pb2 import Tick
t = Tick()
t.vol_idr = 1000
print("Has vol_idr:", hasattr(t, "vol_idr"))
