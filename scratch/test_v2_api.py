import os
import time
import hmac
import hashlib
import requests
from dotenv import load_dotenv

load_dotenv()

def fetch_v2_trades(symbol):
    api_key = os.getenv('INDODAX_API_KEY')
    api_secret = os.getenv('INDODAX_API_SECRET')
    
    timestamp = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "limit": 10,
        "timestamp": timestamp,
        "recvWindow": 60000
    }
    
    query_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    sign = hmac.new(api_secret.encode(), query_str.encode(), hashlib.sha512).hexdigest()
    
    headers = {
        "X-APIKEY": api_key,
        "Sign": sign,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    url = f"https://tapi.indodax.com/api/v2/myTrades?{query_str}"
    resp = requests.get(url, headers=headers)
    print(f"Status: {resp.status_code}")
    print(f"Body: {resp.text[:500]}")
    return resp.json()

# Test for a pair that the user likely has trades in (e.g. btcidr or ethidr)
print("Testing BTCIDR...")
print(fetch_v2_trades("btcidr"))
print("\nTesting ETHIDR...")
print(fetch_v2_trades("ethidr"))
