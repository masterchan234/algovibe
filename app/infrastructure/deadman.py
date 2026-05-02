import hmac
import hashlib
import time
import threading
import requests
import os
import logging
from config import DEADMAN_SWITCH_ENABLED, DEADMAN_INTERVAL_SEC, DEADMAN_TIMEOUT_MS

logger = logging.getLogger(__name__)

class DeadmanSwitch:
    """
    Indodax Deadman Switch Implementation.
    Calls /countdownCancelAll as a heartbeat to ensure orders are canceled if bot crashes.
    """
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.running = False
        self.thread = None
        self.pairs = "btc_idr,eth_idr" # Default safe pairs, updated by ScalperBot
        self.endpoint = "https://indodax.com/tapi/countdownCancelAll"

    def set_pairs(self, pairs_list):
        """Update the list of pairs to protect."""
        if not pairs_list:
            return
        # Convert BTC/IDR -> btc_idr, btcidr -> btc_idr
        normalized = []
        for p in pairs_list:
            p = p.lower()
            if "/" in p:
                normalized.append(p.replace("/", "_"))
            elif "_" in p:
                normalized.append(p)
            elif p.endswith("idr"):
                normalized.append(p[:-3] + "_idr")
            elif p.endswith("usdt"):
                normalized.append(p[:-4] + "_usdt")
            else:
                normalized.append(p)
        
        # Remove duplicates
        normalized = list(set(normalized))
        self.pairs = ",".join(normalized)

    def _sign(self, params_str):
        return hmac.new(
            self.api_secret.encode('utf-8'),
            params_str.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()

    def send_heartbeat(self):
        """Send a single heartbeat request."""
        try:
            timestamp = int(time.time() * 1000)
            params = {
                "pair": self.pairs,
                "countdownTime": DEADMAN_TIMEOUT_MS,
                "timestamp": timestamp,
                "recvWindow": 10000
            }
            # Convert to query string format for signing
            params_str = "&".join([f"{k}={v}" for k, v in params.items()])
            sign = self._sign(params_str)

            headers = {
                "Key": self.api_key,
                "Sign": sign,
                "Content-Type": "text/plain"
            }

            resp = requests.post(self.endpoint, data=params_str, headers=headers, timeout=10)
            data = resp.json()

            if data.get("success") == 1:
                logger.debug(f"[Deadman] Heartbeat sent OK. Pairs protected: {self.pairs}")
                return True
            else:
                logger.error(f"[Deadman] Heartbeat failed: {data.get('error')}")
                return False
        except Exception as e:
            logger.error(f"[Deadman] Heartbeat exception: {e}")
            return False

    def start(self):
        if not DEADMAN_SWITCH_ENABLED:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("[Deadman] Safety heartbeat thread started.")

    def stop(self):
        self.running = False
        logger.info("[Deadman] Safety heartbeat thread stopped.")

    def _run(self):
        while self.running:
            self.send_heartbeat()
            time.sleep(DEADMAN_INTERVAL_SEC)
