import json, urllib.request
req = urllib.request.Request("http://localhost:8080/api/radar")
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        for row in data:
            if row['pair'] == 'BTCUSDT':
                print(row)
                break
except Exception as e:
    print(e)
