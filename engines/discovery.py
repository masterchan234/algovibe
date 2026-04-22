import requests

def fetch_tickers():
    url = "https://indodax.com/api/tickers"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()['tickers']
    except Exception as e:
        print(f"Discovery Error: {e}")
    return None