"""
IndodaxAdapter — Indodax exchange implementation of BaseExchangeAdapter.

Handles all Indodax-specific concerns so that the rest of Algovibe can
work through the unified BaseExchangeAdapter interface:

  • Authentication  — HMAC-SHA512 signed payloads via the Trade API
                      (https://indodax.com/tapi)
  • Data mapping    — Translates Indodax field names (remain_idr,
                      available_idr, …) into Algovibe's unified schema
  • Rate limiting   — Token-bucket / cooldown queue to stay within
                      Indodax's published API limits (~180 req/min)
"""

import hashlib
import hmac
import logging
import threading
import time
import urllib.parse
from typing import Any

import requests

from .base_adapter import BaseExchangeAdapter

logger = logging.getLogger(__name__)

# ── Indodax Trade API constants ───────────────────────────────────────────────
TAPI_URL = "https://indodax.com/tapi"
TICKER_URL = "https://indodax.com/api/{pair}/ticker"
ORDER_BOOK_URL = "https://indodax.com/api/{pair}/depth"

# Primary quote asset for this exchange (used for get_balance default)
QUOTE_ASSET = "IDR"

# Rate-limit budget: Indodax recommends ≤ 3 private requests/second
_MAX_CALLS_PER_SEC = 3
_MIN_INTERVAL_SEC = 1.0 / _MAX_CALLS_PER_SEC  # ~0.333 s


class IndodaxAdapter(BaseExchangeAdapter):
    """Indodax exchange adapter.

    Parameters
    ----------
    api_key:    Indodax Trade API key.
    api_secret: Indodax Trade API secret.
    """

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")

        # ── Rate-limit state ──────────────────────────────────────────────────
        self._rate_lock = threading.Lock()
        self._last_call_ts: float = 0.0   # Unix timestamp of the last TAPI call
        self._nonce_counter: int = int(time.time() * 1000)  # monotonic nonce

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def exchange_id(self) -> str:
        return "indodax"

    @property
    def fee_structure(self) -> dict:
        return {
            "maker_buy":        0.001111,   # 0.1% service + 0.0111% CFX
            "taker_buy":        0.002111,   # 0.2% service + 0.0111% CFX
            "maker_sell":       0.003211,   # 0.1% service + 0.21% tax + 0.0111% CFX
            "taker_sell":       0.004211,   # 0.2% service + 0.21% tax + 0.0111% CFX
            "round_trip_taker": 0.006322,   # buy_taker + sell_taker
            "slippage_buffer":  0.0021,     # 0.21% market-impact buffer
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _next_nonce(self) -> str:
        """Return a strictly-increasing millisecond nonce."""
        with self._rate_lock:
            ts_ms = int(time.time() * 1000)
            if ts_ms <= self._nonce_counter:
                self._nonce_counter += 1
            else:
                self._nonce_counter = ts_ms
            return str(self._nonce_counter)

    def _throttle(self) -> None:
        """Block until the minimum inter-request interval has elapsed."""
        with self._rate_lock:
            elapsed = time.time() - self._last_call_ts
            if elapsed < _MIN_INTERVAL_SEC:
                time.sleep(_MIN_INTERVAL_SEC - elapsed)
            self._last_call_ts = time.time()

    def _sign(self, payload: str) -> str:
        """Return HMAC-SHA512 hex digest of *payload* using the API secret."""
        return hmac.new(self._api_secret, payload.encode("utf-8"), hashlib.sha512).hexdigest()

    def _tapi_post(self, method: str, params: dict | None = None) -> dict:
        """Send a signed POST request to the Indodax Trade API.

        Parameters
        ----------
        method: TAPI method name, e.g. 'getInfo', 'trade', 'openOrders'.
        params: Optional additional parameters (merged with method + nonce).

        Returns
        -------
        dict — the 'return' sub-dict from a successful response.

        Raises
        ------
        RuntimeError on API-level errors or network failures.
        """
        self._throttle()

        payload_dict = {"method": method, "nonce": self._next_nonce()}
        if params:
            payload_dict.update(params)

        payload_str = urllib.parse.urlencode(payload_dict)
        sign = self._sign(payload_str)

        headers = {
            "Key":          self._api_key,
            "Sign":         sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            resp = requests.post(TAPI_URL, headers=headers, data=payload_str, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("[IndodaxAdapter] Network error in %s: %s", method, exc)
            raise RuntimeError(f"IndodaxAdapter network error: {exc}") from exc

        if data.get("success") != 1:
            err = data.get("error", "unknown error")
            err_code = data.get("error_code", "")
            logger.error("[IndodaxAdapter] API error in %s: %s (%s)", method, err, err_code)
            raise RuntimeError(f"Indodax API error [{err_code}]: {err}")

        return data.get("return", {})

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        """Normalise symbol to lowercase Indodax format, e.g. 'btcidr'."""
        return symbol.lower().replace("/", "").replace("_", "").replace("-", "")

    # ── Account ───────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """Return IDR balance in the unified format."""
        raw = self._tapi_post("getInfo")
        balance_raw = raw.get("balance", {})
        free_raw = raw.get("balance_hold", {})

        idr_free = float(balance_raw.get("idr", 0))
        idr_hold = float(free_raw.get("idr", 0))

        return {
            "asset": QUOTE_ASSET,
            "free":  idr_free,
            "used":  idr_hold,
            "total": idr_free + idr_hold,
        }

    def get_all_balances(self) -> list[dict]:
        """Return balances for every asset with a non-zero holding."""
        raw = self._tapi_post("getInfo")
        balance_raw = raw.get("balance", {})
        hold_raw    = raw.get("balance_hold", {})

        assets_seen: set[str] = set(balance_raw.keys()) | set(hold_raw.keys())
        result = []
        for asset in sorted(assets_seen):
            free  = float(balance_raw.get(asset, 0))
            used  = float(hold_raw.get(asset, 0))
            total = free + used
            if total > 0:
                result.append({
                    "asset": asset.upper(),
                    "free":  free,
                    "used":  used,
                    "total": total,
                })
        return result

    # ── Market data ───────────────────────────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
        """Fetch the public ticker for *symbol* and return the unified format."""
        pair = self._normalise_symbol(symbol)
        url = TICKER_URL.format(pair=pair)

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            raw = resp.json().get("ticker", {})
        except requests.RequestException as exc:
            logger.error("[IndodaxAdapter] get_ticker network error: %s", exc)
            raise RuntimeError(f"IndodaxAdapter get_ticker error: {exc}") from exc

        # Indodax field names → unified
        return {
            "symbol":    symbol.upper(),
            "last":      float(raw.get("last", 0)),
            "bid":       float(raw.get("buy", 0)),
            "ask":       float(raw.get("sell", 0)),
            "high":      float(raw.get("high", 0)),
            "low":       float(raw.get("low", 0)),
            "volume":    float(raw.get("vol_idr", raw.get("vol_btc", 0))),
            "timestamp": int(float(raw.get("server_time", time.time())) * 1000),
        }

    # ── Order management ─────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        volume: float,
        price: float | None = None,
    ) -> dict:
        """Place a limit or market order on Indodax.

        Indodax only supports limit orders natively; 'market' orders are
        executed as aggressive limit orders at the current bid/ask.
        """
        pair = self._normalise_symbol(symbol)
        side_lower = side.lower()

        params: dict[str, Any] = {
            "pair": pair,
            "type": side_lower,
        }

        if order_type.lower() == "market":
            # Use live ticker to approximate market price
            ticker = self.get_ticker(symbol)
            exec_price = ticker["ask"] if side_lower == "buy" else ticker["bid"]
        else:
            if price is None:
                raise ValueError("price is required for limit orders")
            exec_price = price

        # Indodax uses IDR amount for buys, crypto amount for sells
        base_asset = pair.replace("idr", "")
        if side_lower == "buy":
            params["price"] = str(int(exec_price))
            idr_amount = volume * exec_price
            params["idr"] = str(int(idr_amount))
        else:
            params["price"] = str(int(exec_price))
            params[base_asset] = str(volume)

        raw = self._tapi_post("trade", params)

        order_id = str(raw.get("order_id", raw.get("receive_idr", "unknown")))

        return {
            "order_id":  order_id,
            "symbol":    symbol.upper(),
            "side":      side_lower,
            "type":      order_type.lower(),
            "price":     exec_price,
            "volume":    volume,
            "status":    "open",
            "timestamp": int(time.time() * 1000),
            "raw":       raw,
        }

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Return all open orders, optionally filtered by trading pair."""
        params = {}
        if symbol:
            params["pair"] = self._normalise_symbol(symbol)

        raw = self._tapi_post("openOrders", params if params else None)
        orders_raw = raw.get("orders", [])
        if isinstance(orders_raw, dict):
            # Indodax sometimes returns a dict keyed by pair instead of a list
            orders_raw = list(orders_raw.values())

        result = []
        for o in orders_raw:
            result.append(self._map_order(o))
        return result

    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict:
        """Cancel the order identified by *order_id*.

        The *symbol* / pair is required by the Indodax Trade API.
        """
        params: dict[str, Any] = {"order_id": order_id}
        if symbol:
            params["pair"] = self._normalise_symbol(symbol)
            params["type"] = "sell"  # Indodax requires type for cancelOrder

        try:
            self._tapi_post("cancelOrder", params)
            return {"success": True, "order_id": order_id}
        except RuntimeError as exc:
            logger.warning("[IndodaxAdapter] cancel_order failed: %s", exc)
            return {"success": False, "order_id": order_id}

    # ── Connectivity ──────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Verify credentials by calling getInfo."""
        try:
            self._tapi_post("getInfo")
            return True
        except Exception as exc:
            logger.warning("[IndodaxAdapter] test_connection failed: %s", exc)
            return False

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _map_order(raw: dict) -> dict:
        """Translate a raw Indodax order dict into the unified order shape."""
        side = raw.get("type", "").lower()
        pair = raw.get("pair", raw.get("order_id", ""))

        # Determine price — Indodax uses 'price' for both sides
        price = float(raw.get("price", 0))

        # Determine volume (base asset)
        # For buy orders: remain_idr / price gives remaining crypto
        # For sell orders: remain_<asset> is explicit
        remain_idr    = float(raw.get("remain_idr", 0))
        volume = (remain_idr / price) if (side == "buy" and price > 0) else 0.0
        for k, v in raw.items():
            if k.startswith("remain_") and k != "remain_idr":
                volume = float(v)
                break

        # Map Indodax order status
        status_raw = str(raw.get("status", "")).lower()
        if status_raw in ("open", ""):
            status = "open"
        elif status_raw in ("filled", "closed", "done"):
            status = "closed"
        elif status_raw in ("cancelled", "canceled"):
            status = "cancelled"
        else:
            status = status_raw

        submit_time = raw.get("submit_time", raw.get("order_date", ""))
        try:
            ts_ms = int(float(submit_time) * 1000) if submit_time else int(time.time() * 1000)
        except (ValueError, TypeError):
            ts_ms = int(time.time() * 1000)

        return {
            "order_id":  str(raw.get("order_id", "")),
            "symbol":    pair.upper(),
            "side":      side,
            "type":      "limit",
            "price":     price,
            "volume":    volume,
            "status":    status,
            "timestamp": ts_ms,
            "raw":       raw,
        }
