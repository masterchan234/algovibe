import requests
import time

def main():
    # 90 days ago to now
    to_ts = int(time.time())
    from_ts = to_ts - (90 * 24 * 60 * 60) # 90 days
    symbol = "BTCIDR"
    tf = "1"
    
    url = "https://indodax.com/tradingview/history_v2"
    params = {
        "symbol": symbol,
        "tf": tf,
        "from": from_ts,
        "to": to_ts
    }
    
    print(f"Querying {url} with 90 days window:")
    print(params)
    
    try:
        start_time = time.time()
        r = requests.get(url, params=params, timeout=60)
        elapsed = time.time() - start_time
        print(f"Request took {elapsed:.2f} seconds")
        print(f"Status code: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"Response type: {type(data)}")
            if isinstance(data, list):
                print(f"Number of candles returned: {len(data)}")
                if len(data) > 0:
                    print(f"First candle: {data[0]}")
                    print(f"Last candle: {data[-1]}")
                    first_time = data[0].get('Time')
                    last_time = data[-1].get('Time')
                    actual_span_days = (last_time - first_time) / (24 * 60 * 60)
                    print(f"Actual span in days: {actual_span_days:.2f} days")
            else:
                print(f"Response snippet: {str(data)[:500]}")
        else:
            print(f"Response text: {r.text}")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
    main()
