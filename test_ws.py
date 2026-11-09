import json, websocket
def on_message(ws, msg):
    print("Received:", msg[:100])
ws = websocket.WebSocketApp("wss://stream.binance.com:9443/ws/!miniTicker@arr", on_message=on_message)
ws.run_forever(ping_interval=30, ping_timeout=10)
