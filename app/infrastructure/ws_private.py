"""
Private WebSocket Client — Indodax & Binance Spot
===============================================
Mendengarkan order update secara real-time.
Mendukung switching exchange antara Indodax dan Binance Spot.
"""

import hmac
import hashlib
import json
import logging
import os
import threading
import time

import requests
import websocket   # websocket-client

logger = logging.getLogger(__name__)

# Constants Indodax
PWS_URL         = "wss://pws.indodax.com/ws/?cf_ws_frame_ping_pong=true"
TOKEN_ENDPOINT  = "https://indodax.com/api/private_ws/v1/generate_token"
TOKEN_TTL       = 23 * 3600   # 23 hours


class PrivateWS:
    """
    WebSocket client untuk Private Channel (Indodax / Binance Spot).
    """

    def __init__(self, api_key: str, api_secret: str, on_order_update=None):
        self._api_key       = api_key
        self._api_secret    = api_secret
        self._on_order_update = on_order_update  

        self._ws            = None
        self._thread        = None
        self._running       = False
        self._conn_token    = None
        self._channel       = None
        self._token_fetched = 0.0   
        self._req_id        = 0
        self._subscribed    = threading.Event()
        self._keep_alive_thread = None

    #  Public API 

    def start(self):
        """Mulai koneksi WS di background thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True, name="PrivateWS")
        self._thread.start()
        
        # Start keep-alive loop for Binance userDataStream
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            self._keep_alive_thread = threading.Thread(target=self._keep_alive_loop, daemon=True, name="BinanceKeepAlive")
            self._keep_alive_thread.start()
            
        logger.info("[PrivateWS] Background thread started.")

    def stop(self):
        """Hentikan koneksi WS."""
        self._running = False
        if self._ws:
            self._ws.close()
        logger.info("[PrivateWS] Stopped.")

    def wait_subscribed(self, timeout: float = 30.0) -> bool:
        """Blok hingga berhasil subscribe."""
        return self._subscribed.wait(timeout=timeout)

    #  Internal 

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _generate_token(self) -> bool:
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            return self._generate_binance_listen_key()
        return self._generate_indodax_token()

    def _generate_binance_listen_key(self) -> bool:
        try:
            headers = {"X-MBX-APIKEY": self._api_key}
            r = requests.post("https://api.binance.com/api/v3/userDataStream", headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            self._conn_token = data.get("listenKey")
            self._token_fetched = time.time()
            logger.info("[PrivateWS] Binance listenKey generated successfully.")
            return True
        except Exception as e:
            logger.error(f"[PrivateWS] Failed to generate Binance listenKey: {e}")
            return False

    def _generate_indodax_token(self) -> bool:
        request_body = f"client=tapi&tapi_key={self._api_key}"
        sign = hmac.new(
            self._api_secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Sign": sign,
        }

        try:
            resp = requests.post(
                TOKEN_ENDPOINT,
                headers=headers,
                data=request_body,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[PrivateWS] Token request failed: {e}")
            return False

        if data.get("success") != 1:
            logger.error(f"[PrivateWS] Token error: {data.get('error')}")
            return False

        self._conn_token    = data["return"]["connToken"]
        self._channel       = data["return"]["channel"]
        self._token_fetched = time.time()
        logger.info(f"[PrivateWS] Indodax Token OK — channel: {self._channel}")
        return True

    def _token_expired(self) -> bool:
        return time.time() - self._token_fetched > TOKEN_TTL

    def _keep_alive_loop(self):
        """Send keep-alive requests for Binance user data stream listenKey."""
        while self._running:
            time.sleep(20 * 60)  # Wait 20 minutes
            if not self._running:
                break
            eid = os.getenv("EXCHANGE", "binance").lower()
            if eid == "binance" and self._conn_token:
                try:
                    headers = {"X-MBX-APIKEY": self._api_key}
                    params = {"listenKey": self._conn_token}
                    r = requests.put("https://api.binance.com/api/v3/userDataStream", headers=headers, params=params, timeout=10)
                    if r.status_code == 200:
                        logger.info("[PrivateWS] Binance listenKey keep-alive sent OK.")
                    else:
                        logger.warning(f"[PrivateWS] Binance listenKey keep-alive failed with status {r.status_code}.")
                except Exception as e:
                    logger.warning(f"[PrivateWS] Exception during Binance listenKey keep-alive: {e}")

    def _run(self):
        while self._running:
            if not self._conn_token or self._token_expired():
                if not self._generate_token():
                    logger.warning("[PrivateWS] Retrying token / listenKey in 60s…")
                    time.sleep(60)
                    continue

            eid = os.getenv("EXCHANGE", "binance").lower()
            if eid == "binance":
                url = f"wss://stream.binance.com:9443/ws/{self._conn_token}"
            else:
                url = PWS_URL

            logger.info(f"[PrivateWS] Connecting directly to {eid.upper()} WS...")
            try:
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                self._ws.run_forever(suppress_origin=True, ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"[PrivateWS] run_forever exception: {e}")

            if self._running:
                self._conn_token = None
                self._subscribed.clear()
                logger.warning("[PrivateWS] Disconnected, reconnecting in 5s…")
                time.sleep(5)

    def _on_open(self, ws):
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            logger.info("[PrivateWS] Connected to Binance Spot private user stream.")
            self._subscribed.set()
        else:
            logger.info("[PrivateWS] Connected. Authenticating Indodax...")
            connect_msg = {
                "connect": {"token": self._conn_token},
                "id": self._next_id(),
            }
            ws.send(json.dumps(connect_msg))

    def _map_binance_to_indodax_order(self, data: dict) -> dict:
        symbol = str(data.get("s", "")).lower()
        binance_status = str(data.get("X", "")).lower()
        
        status = binance_status
        if binance_status == "filled":
            status = "filled"
        elif binance_status == "partially_filled":
            status = "fill"
        elif binance_status == "canceled":
            status = "canceled"
        elif binance_status == "rejected":
            status = "rejected"
            
        orig_qty = float(data.get("q", 0.0))
        exec_qty = float(data.get("z", 0.0))
        unfilled = max(orig_qty - exec_qty, 0.0)
        
        price = float(data.get("p", 0.0))
        if price == 0.0:
            price = float(data.get("L", 0.0)) # Last executed price or avg price
            
        mapped = {
            "id": str(data.get("i", "")),
            "orderId": str(data.get("i", "")),
            "clientOrderId": str(data.get("c", "")),
            "symbol": symbol,
            "pair": symbol,
            "side": str(data.get("S", "")).upper(),
            "status": status,
            "price": price,
            "origQty": orig_qty,
            "executedQty": exec_qty,
            "amount": exec_qty,
            "unfilledQty": unfilled,
            "tradeId": str(data.get("t", "")),
            "fillInformation": {
                "participant": "TAKER" if not data.get("m", False) else "MAKER",
                "fee": float(data.get("n", 0.0)),
                "tax": 0.0,
                "clearing": 0.0
            }
        }
        return mapped

    def _on_message(self, ws, raw: str):
        eid = os.getenv("EXCHANGE", "binance").lower()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if eid == "binance":
            event_type = msg.get("e")
            if event_type == "executionReport":
                mapped_order = self._map_binance_to_indodax_order(msg)
                logger.debug(f"[PrivateWS] mapped executionReport: {mapped_order}")
                if callable(self._on_order_update):
                    try:
                        self._on_order_update(mapped_order)
                    except Exception as e:
                        logger.error(f"[PrivateWS] on_order_update callback error: {e}")
            return

        # Indodax Message processing
        if "connect" in msg and "client" in msg.get("connect", {}):
            logger.info(f"[PrivateWS] Indodax Auth OK — client: {msg['connect']['client']}")
            sub_msg = {
                "subscribe": {"channel": self._channel},
                "id": self._next_id(),
            }
            ws.send(json.dumps(sub_msg))
            return

        if "error" in msg and msg.get("id") == 1:
            code = msg["error"].get("code")
            if code == 109:
                logger.warning("[PrivateWS] Token expired. Reconnecting...")
                self._conn_token = None
                ws.close()
            else:
                logger.error(f"[PrivateWS] Connect error: {msg['error']}")
            return

        if "id" in msg and "subscribe" in msg:
            if "error" in msg:
                logger.error(f"[PrivateWS] Subscribe failed: {msg['error']}")
            else:
                logger.info(f"[PrivateWS] Subscribed to {self._channel}")
                self._subscribed.set()
            return

        push = msg.get("push", {})
        if push.get("channel") == self._channel:
            pub_data = push.get("pub", {}).get("data", [])
            for event in pub_data:
                if event.get("eventType") == "order_update":
                    order = event.get("order", {})
                    logger.debug(f"[PrivateWS] order_update: {order}")
                    if callable(self._on_order_update):
                        try:
                            self._on_order_update(order)
                        except Exception as e:
                            logger.error(f"[PrivateWS] on_order_update callback error: {e}")

    def _on_error(self, ws, error):
        logger.error(f"[PrivateWS] Error: {error}")
        ws.close()

    def _on_close(self, ws, code, reason):
        self._subscribed.clear()
        logger.warning(f"[PrivateWS] Connection closed (code={code}, reason={reason})")
