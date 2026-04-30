import os
import requests
import threading
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def _send_async(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram] Error sending message: {e}")

def send_telegram_message(message: str):
    """
    Kirim pesan ke Telegram secara non-blocking (background thread)
    agar tidak mengganggu latensi main loop trading.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
        
    # Gunakan thread agar HTTP request tidak mem-block engine utama
    t = threading.Thread(target=_send_async, args=(message,))
    t.daemon = True
    t.start()
