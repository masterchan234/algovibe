import re

with open('dashboard/server.py', 'r') as f:
    content = f.read()

# First remove the broken block
broken_block = """
# Global cache for 24h change
_ch24h_cache = {}
_ch24h_last_update = 0

def get_cached_ch24h(pair):
    global _ch24h_cache, _ch24h_last_update
    import time
    import requests
    # Update every 60 seconds
    if time.time() - _ch24h_last_update > 60:
        try:
            eid = os.getenv("EXCHANGE", "binance").lower()
            if eid == "binance":
                res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=5).json()
                for t in res:
                    _ch24h_cache[t["symbol"].lower()] = float(t.get("priceChangePercent", 0.0))
            else:
                res = requests.get("https://indodax.com/api/summaries", timeout=5).json()
                for pair_key, t in res.get("tickers", {}).items():
                    _ch24h_cache[pair_key.replace("_", "").lower()] = float(t.get("price_24h_percent", 0.0))
            _ch24h_last_update = time.time()
        except Exception as e:
            logging.warning(f"Failed to fetch 24hr ticker: {e}")
    return _ch24h_cache.get(pair.lower(), 0.0)
"""

content = content.replace(broken_block, "")

# Now insert it BEFORE @app.route('/api/radar')
content = content.replace("@app.route('/api/radar')", broken_block + "\n@app.route('/api/radar')")

with open('dashboard/server.py', 'w') as f:
    f.write(content)
