"""
Private WebSocket Client — Indodax
====================================
Mendengarkan order update secara real-time via:
  wss://pws.indodax.com/ws/?cf_ws_frame_ping_pong=true

Flow:
  1. Generate connToken + channel via REST POST /api/private_ws/v1/generate_token
  2. Connect ke Private WS URL
  3. Send {"connect": {"token": <connToken>}, "id": 1}
  4. Send {"subscribe": {"channel": <channel>}, "id": 2}
  5. Terima push event order_update

Referensi: Private-websocket.md
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

# ─── Konstanta ────────────────────────────────────────────────────────────────
PWS_URL         = "wss://pws.indodax.com/ws/?cf_ws_frame_ping_pong=true"
TOKEN_ENDPOINT  = "https://indodax.com/api/private_ws/v1/generate_token"
TOKEN_TTL       = 23 * 3600   # refresh token setiap 23 jam (lifetime 24 jam)


class PrivateWS:
    """
    WebSocket client untuk Private Channel Indodax.

    Menerima event order_update secara real-time dan memanggil
    callback yang didaftarkan.

    Penggunaan:
        pws = PrivateWS(
            api_key    = "...",
            api_secret = "...",
            on_order_update = my_callback,   # fn(order_dict)
        )
        pws.start()
        ...
        pws.stop()
    """

    def __init__(self, api_key: str, api_secret: str, on_order_update=None):
        self._api_key       = api_key
        self._api_secret    = api_secret
        self._on_order_update = on_order_update  # callable(order: dict)

        self._ws            = None
        self._thread        = None
        self._running       = False
        self._conn_token    = None
        self._channel       = None
        self._token_fetched = 0.0   # timestamp kapan token terakhir di-fetch
        self._req_id        = 0
        self._subscribed    = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Mulai koneksi WS di background thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True, name="PrivateWS")
        self._thread.start()
        logger.info("[PrivateWS] Background thread started.")

    def stop(self):
        """Hentikan koneksi WS."""
        self._running = False
        if self._ws:
            self._ws.close()
        logger.info("[PrivateWS] Stopped.")

    def wait_subscribed(self, timeout: float = 30.0) -> bool:
        """Blok hingga berhasil subscribe ke private channel."""
        return self._subscribed.wait(timeout=timeout)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _generate_token(self) -> bool:
        """
        Request connToken + channel dari REST endpoint.
        Return True jika berhasil.
        """
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
            logger.error(f"[PrivateWS] Token error: {data.get('error')} [{data.get('error_code')}]")
            return False

        self._conn_token    = data["return"]["connToken"]
        self._channel       = data["return"]["channel"]
        self._token_fetched = time.time()
        logger.info(f"[PrivateWS] Token OK — channel: {self._channel}")
        return True

    def _token_expired(self) -> bool:
        return time.time() - self._token_fetched > TOKEN_TTL

    def _run(self):
        """Loop utama dengan auto-reconnect dan token refresh."""
        while self._running:
            # Fetch / refresh token sebelum connect
            if not self._conn_token or self._token_expired():
                if not self._generate_token():
                    logger.warning("[PrivateWS] Retrying token in 60s…")
                    time.sleep(60)
                    continue

            try:
                self._ws = websocket.WebSocketApp(
                    PWS_URL,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                # suppress_origin=True wajib — hindari 403 (lihat Troubleshooting docs)
                self._ws.run_forever(suppress_origin=True, ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"[PrivateWS] run_forever exception: {e}")

            if self._running:
                # Force refresh token saat reconnect
                self._conn_token = None
                self._subscribed.clear()
                logger.warning("[PrivateWS] Disconnected, reconnecting in 5s…")
                time.sleep(5)

    def _on_open(self, ws):
        logger.info("[PrivateWS] Connected. Authenticating…")
        # Step 3: Kirim connect dengan connToken
        connect_msg = {
            "connect": {"token": self._conn_token},
            "id": self._next_id(),
        }
        ws.send(json.dumps(connect_msg))

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # ── Auth/connect response ──────────────────────────────────────────
        if "connect" in msg and "client" in msg.get("connect", {}):
            logger.info(f"[PrivateWS] Auth OK — client: {msg['connect']['client']}")
            # Step 4: Subscribe ke private channel
            sub_msg = {
                "subscribe": {"channel": self._channel},
                "id": self._next_id(),
            }
            ws.send(json.dumps(sub_msg))
            return

        # ── Token expired saat connect ─────────────────────────────────────
        if "error" in msg and msg.get("id") == 1:
            code = msg["error"].get("code")
            if code == 109:
                logger.warning("[PrivateWS] Token expired. Reconnecting with new token…")
                self._conn_token = None
                ws.close()
            else:
                logger.error(f"[PrivateWS] Connect error: {msg['error']}")
            return

        # ── Subscribe ACK ──────────────────────────────────────────────────
        if "id" in msg and "subscribe" in msg:
            if "error" in msg:
                logger.error(f"[PrivateWS] Subscribe failed: {msg['error']}")
            else:
                logger.info(f"[PrivateWS] Subscribed to {self._channel}")
                self._subscribed.set()
            return

        # ── Subscribe failed (legacy format) ──────────────────────────────
        if "id" in msg and msg.get("id") == 2 and "error" in msg:
            logger.error(f"[PrivateWS] Subscribe error: {msg['error']}")
            return

        # ── Push / order update events ─────────────────────────────────────
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
