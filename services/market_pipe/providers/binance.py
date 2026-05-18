"""
services/market_pipe/providers/binance.py — Binance Market Provider
==================================================================
Connects to the public Binance WebSocket using a combined stream of bookTicker
updates, normalises updates into Tick Protobuf messages, and emits them via
the configured callback.

Stream list is built DYNAMICALLY at startup by:
  1. Fetching all active USDT trading pairs from Binance REST exchangeInfo.
  2. Fetching all IDR trading pairs from Indodax REST summaries.
  3. Mapping Indodax pairs (baseIDR → baseUSDT) and intersecting with Binance.
  4. Subscribing only to the intersection — covers all cross-listed coins
     automatically, skipping purely local / domestic-only tokens.

Binance combined stream limit: 1024 streams max per connection.
Includes automatic reconnection, ping/pong keepalive, and thread safety.
"""

import json
import logging
import os
import sys
import threading
import time
from typing import Optional, Set

import requests
import websocket

# Allow importing from the repo root when running as standalone service
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.market_pipe.providers.base import MarketProvider, TickCallback, TradeCallback
from shared.algovibe_pb2 import Tick, ExchangeId  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# Binance REST endpoints (public, no auth required)
_BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_INDODAX_SUMMARIES_URL = "https://indodax.com/api/summaries"

# Nama base yang berbeda antara Indodax dan Binance (indodax_base → binance_base).
# Tambahkan mapping di sini jika ada koin baru yang namanya tidak konsisten.
_BASE_OVERRIDE: dict = {
    "rndr": "render",   # Render Network: Indodax=RNDR, Binance=RENDER
}

# Pairs yang hanya ada di Indodax (tidak ada pasangan USDT di Binance) dan harus di-skip.
# Ini adalah safety-net; discovery otomatis akan melewatkan koin ini secara alami.
_LOCAL_ONLY_BASES: Set[str] = {
    "idr",    # USDT/IDR pair itu sendiri
}


def _fetch_binance_usdt_pairs(timeout: float = 10.0) -> Set[str]:
    """
    Fetch semua simbol USDT yang aktif diperdagangkan di Binance spot.
    Returns set of lowercase base symbols (e.g. {'btc', 'eth', 'sol', ...}).
    """
    try:
        resp = requests.get(_BINANCE_EXCHANGE_INFO_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        bases = set()
        for sym in data.get("symbols", []):
            if (
                sym.get("quoteAsset") == "USDT"
                and sym.get("status") == "TRADING"
                and sym.get("isSpotTradingAllowed", False)
            ):
                bases.add(sym["baseAsset"].lower())
        logger.info("[BinanceProvider] Fetched %d active USDT base assets from Binance.", len(bases))
        return bases
    except Exception as e:
        logger.warning("[BinanceProvider] Failed to fetch Binance exchange info: %s. Using empty set.", e)
        return set()


def _fetch_indodax_bases(timeout: float = 10.0) -> Set[str]:
    """
    Fetch semua pair IDR dari Indodax summaries API.
    Returns set of lowercase base symbols (e.g. {'btc', 'eth', 'sol', ...}).
    """
    try:
        resp = requests.get(_INDODAX_SUMMARIES_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        tickers = data.get("tickers", {})
        bases = set()
        for pair_key in tickers:
            # pair_key format: "btc_idr", "eth_idr", ...
            if pair_key.endswith("_idr"):
                base = pair_key[:-4]  # strip '_idr'
                if base not in _LOCAL_ONLY_BASES:
                    bases.add(base.lower())
        logger.info("[BinanceProvider] Fetched %d IDR base assets from Indodax.", len(bases))
        return bases
    except Exception as e:
        logger.warning("[BinanceProvider] Failed to fetch Indodax summaries: %s. Using empty set.", e)
        return set()


def _build_stream_list() -> list:
    """
    Build the full WebSocket stream subscription list by intersecting
    Binance active USDT pairs with Indodax IDR pair list.

    Returns list of bookTicker stream names (e.g. ['btcusdt@bookTicker', ...]).
    """
    binance_bases = _fetch_binance_usdt_pairs()
    indodax_bases = _fetch_indodax_bases()

    streams = []
    skipped_local = []

    for indodax_base in sorted(indodax_bases):
        # Apply override mapping (e.g. rndr → render)
        binance_base = _BASE_OVERRIDE.get(indodax_base, indodax_base)
        
        if binance_base in binance_bases:
            streams.append(f"{binance_base}usdt@bookTicker")
        else:
            skipped_local.append(indodax_base)

    logger.info(
        "[BinanceProvider] Dynamic discovery: %d streams to subscribe | "
        "%d local-only (no Binance listing): %s",
        len(streams), len(skipped_local),
        skipped_local[:20],  # log maks 20 agar tidak spam
    )
    return streams


class BinanceProvider(MarketProvider):
    """
    Binance market-data provider using a public combined bookTicker stream.
    No API keys are required.

    Stream list is built dynamically at startup by intersecting Indodax IDR
    pairs with Binance active USDT pairs, so all cross-listed coins are always
    covered without any manual configuration.
    """

    # Binance combined stream limit
    _MAX_STREAMS_PER_CONNECTION = 1024

    def __init__(
        self,
        on_tick: Optional[TickCallback] = None,
        on_trade: Optional[TradeCallback] = None,
    ):
        super().__init__(on_tick=on_tick, on_trade=on_trade)

        self._ws: Optional[websocket.WebSocketApp] = None
        self._running = False
        self._ready_event = threading.Event()
        self._ws_thread: Optional[threading.Thread] = None

        # streams akan di-build saat start() untuk memastikan discovery
        # berjalan fresh setiap kali service di-restart
        self.streams: list = []
        self.url: str = ""

    #  Identity 

    @property
    def exchange_id(self) -> str:
        return "binance"

    #  Lifecycle 

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._running = True
        self._ready_event.clear()

        self._ws_thread = threading.Thread(target=self._run_loop, daemon=True, name="BinanceWS-Thread")
        self._ws_thread.start()
        logger.info("[BinanceProvider] Background WS thread started.")

    def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception as e:
                logger.warning(f"[BinanceProvider] Error closing WS: {e}")
        logger.info("[BinanceProvider] Stopped.")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        return self._ready_event.wait(timeout=timeout)

    def is_connected(self) -> bool:
        if self._ws and self._ws.sock and self._ws.sock.connected:
            return True
        return False

    #  Connection Loop 

    def _run_loop(self) -> None:
        # Dynamic discovery — dilakukan sekali saat pertama terhubung
        logger.info("[BinanceProvider] Running dynamic pair discovery...")
        self.streams = _build_stream_list()

        if not self.streams:
            logger.error("[BinanceProvider] No streams discovered. Binance feed disabled.")
            return

        # Batasi ke maksimum per koneksi Binance
        if len(self.streams) > self._MAX_STREAMS_PER_CONNECTION:
            logger.warning(
                "[BinanceProvider] Discovered %d streams exceeds limit %d. Truncating.",
                len(self.streams), self._MAX_STREAMS_PER_CONNECTION
            )
            self.streams = self.streams[:self._MAX_STREAMS_PER_CONNECTION]

        streams_query = "/".join(self.streams)
        self.url = f"wss://stream.binance.com:9443/stream?streams={streams_query}"

        while self._running:
            logger.info(
                "[BinanceProvider] Connecting to Binance combined stream (%d pairs)...",
                len(self.streams)
            )

            self._ws = websocket.WebSocketApp(
                self.url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open
            )

            # Run the client loop with automatic ping keepalive
            self._ws.run_forever(
                ping_interval=15,
                ping_timeout=10
            )

            if self._running:
                logger.warning("[BinanceProvider] WS disconnected. Reconnecting in 3 seconds...")
                time.sleep(3)

    #  WS Callbacks 

    def _on_open(self, ws) -> None:
        logger.info(
            "[BinanceProvider] [OK] Connected to Binance stream. Subscribed to %d pairs.",
            len(self.streams)
        )
        # Signal readiness immediately on connection open
        if not self._ready_event.is_set():
            self._ready_event.set()

    def _on_message(self, ws, raw_msg: str) -> None:
        try:
            payload = json.loads(raw_msg)
            data = payload.get("data", {})
            if not data:
                return

            symbol = str(data.get("s", "")).lower()  # e.g., "btcusdt"
            bid = float(data.get("b", 0.0))
            ask = float(data.get("a", 0.0))

            if bid <= 0 or ask <= 0:
                return

            price = (bid + ask) / 2.0
            ts_ms = int(time.time() * 1000)

            # Create Tick protobuf
            tick = Tick()
            tick.symbol      = symbol
            tick.price       = price
            tick.volume      = 0.0  # bookTicker doesn't carry 24h volume
            tick.timestamp   = ts_ms
            tick.exchange_id = ExchangeId.Value("EXCHANGE_BINANCE")
            tick.bid         = bid
            tick.ask         = ask
            tick.high_24h    = price
            tick.low_24h     = price
            tick.vol_idr     = 0.0

            self._emit(tick.SerializeToString(), topic="tick")

        except Exception as e:
            logger.error(f"[BinanceProvider] Error processing message: {e}")

    def _on_error(self, ws, err) -> None:
        logger.error(f"[BinanceProvider] WebSocket Error: {err}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        logger.warning(f"[BinanceProvider] WebSocket Closed: {close_status_code} - {close_msg}")
