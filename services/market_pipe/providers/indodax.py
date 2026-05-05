"""
services/market_pipe/providers/indodax.py — Indodax Market Provider
====================================================================
Wraps the existing MarketDataWS (app/infrastructure/ws_market.py) and
translates its internal ticker dict into Protobuf Tick messages that are
emitted to the on_tick callback.

The provider polls the MarketDataWS snapshot at a configurable interval
(default 100 ms) so that the ZMQ publisher receives updates at sub-second
granularity without blocking the WS thread.

Future extension:
  Replace the polling loop with a direct callback hook inside MarketDataWS
  once the WS layer exposes an on_update(pair, data) hook.
"""

import logging
import sys
import os
import threading
import time
from typing import Optional

# Allow importing from the repo root when running as standalone service
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.market_pipe.providers.base import MarketProvider, TickCallback
from shared.algovibe_pb2 import Tick, ExchangeId  # type: ignore[attr-defined]
from app.infrastructure.ws_market import MarketDataWS

logger = logging.getLogger(__name__)

# How often to poll the WS snapshot and emit ticks (seconds)
_POLL_INTERVAL_SEC = 0.10   # 100 ms → ≤ 10 ticks/s per pair


class IndodaxProvider(MarketProvider):
    """
    Indodax market-data provider.

    Connects to the Indodax WebSocket (via the existing MarketDataWS client),
    normalises every ticker into a Protobuf Tick, and calls on_tick with the
    serialised bytes.
    """

    def __init__(
        self,
        on_tick: Optional[TickCallback] = None,
        poll_interval: float = _POLL_INTERVAL_SEC,
    ):
        super().__init__(on_tick=on_tick)
        self._poll_interval = poll_interval
        self._mws: MarketDataWS = MarketDataWS()
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._ready_event = threading.Event()

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def exchange_id(self) -> str:
        return "indodax"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._running = True
        self._mws.start()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="IndodaxProvider-Poll",
        )
        self._poll_thread.start()
        logger.info("[IndodaxProvider] Started.")

    def stop(self) -> None:
        self._running = False
        self._mws.stop()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        logger.info("[IndodaxProvider] Stopped.")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        return self._ready_event.wait(timeout=timeout)

    def is_connected(self) -> bool:
        return self._mws.is_connected()

    # ── Internal poll loop ────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """
        Polls MarketDataWS.get_tickers() at _poll_interval and emits one
        Tick protobuf message per pair.
        """
        # Wait until WS is connected before publishing anything
        if not self._mws.wait_ready(timeout=60):
            logger.error("[IndodaxProvider] WS not ready within 60s — stopping poll loop.")
            return

        self._ready_event.set()
        logger.info("[IndodaxProvider] WS ready — starting tick emission loop.")

        while self._running:
            loop_start = time.monotonic()
            try:
                tickers = self._mws.get_tickers()
                ts_ms = int(time.time() * 1000)

                for pair, data in tickers.items():
                    tick_bytes = self._build_tick(pair, data, ts_ms)
                    if tick_bytes:
                        self._emit(tick_bytes)

            except Exception as exc:
                logger.error(f"[IndodaxProvider] Poll error: {exc}")

            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.0, self._poll_interval - elapsed)
            time.sleep(sleep_time)

    # ── Normalisation ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_tick(pair: str, data: dict, ts_ms: int) -> Optional[bytes]:
        """
        Normalise a raw Indodax ticker dict into serialised Tick bytes.

        Returns None if essential data is missing.
        """
        price = _safe_float(data.get("last"))
        if price <= 0:
            return None

        tick = Tick()
        tick.symbol      = pair
        tick.price       = price
        tick.volume      = _safe_float(data.get("vol_idr"))
        tick.timestamp   = ts_ms
        tick.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")
        tick.bid         = _safe_float(data.get("buy"))
        tick.ask         = _safe_float(data.get("sell"))
        tick.high_24h    = _safe_float(data.get("high"))
        tick.low_24h     = _safe_float(data.get("low"))
        tick.vol_idr     = _safe_float(data.get("vol_idr"))

        return tick.SerializeToString()


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
