
import requests

URL = "http://127.0.0.1:8080"
LOGIN_PAYLOAD = {"username": "admin", "password": "admin123"}

def check_radar_audit_api():
    session = requests.Session()
    # Login
    print(f"Logging in to {URL}/api/bot/login...")
    res = session.post(f"{URL}/api/bot/login", json=LOGIN_PAYLOAD)
    if res.status_code != 200:
        print(f"Login failed: {res.status_code} {res.text}")
        return
    
    token = res.json().get("token")
    print(f"Login success. Token: {token[:10]}...")
    
    # Fetch radar-audit
    headers = {"Authorization": f"Bearer {token}"}
    print(f"Fetching {URL}/api/radar-audit...")
    res = session.get(f"{URL}/api/radar-audit", headers=headers)
    print(f"Status: {res.status_code}")
    print(f"Response: {res.text}")

if __name__ == "__main__":
    check_radar_audit_api()
