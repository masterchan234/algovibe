"""
services/market_pipe/providers/base.py — Abstract Market Provider
==================================================================
Every exchange data-source MUST implement this ABC so that the market-pipe
service can treat all feeds identically.

Implementing a new exchange:
1. Subclass MarketProvider.
2. Implement all abstract methods.
3. Register the class in providers/__init__.py and pass it to market_pipe/main.py.
"""

import abc
import time
from typing import Callable, Optional


# Callback type: receives a Protobuf-serialised bytes object
TickCallback = Callable[[bytes], None]
TradeCallback = Callable[[bytes], None]


class MarketProvider(abc.ABC):
    """
    Abstract base class for exchange market-data providers.

    Lifecycle
    ---------
    provider = MyProvider(on_tick=callback, on_trade=trade_callback)
    provider.start()   # connects / subscribes in the background
    provider.wait_ready(timeout=30)
    ...
    provider.stop()
    """

    def __init__(
        self, 
        on_tick: Optional[TickCallback] = None,
        on_trade: Optional[TradeCallback] = None
    ):
        """
        Parameters
        ----------
        on_tick:
            Callable invoked with the serialised Protobuf Tick bytes every
            time a new price update arrives.
        on_trade:
            Callable invoked with the serialised Protobuf Trade bytes every
            time a new market trade arrives.
        """
        self._on_tick = on_tick
        self._on_trade = on_trade
        self._latest_tick_bytes: Optional[bytes] = None
        self._start_time: float = 0.0

    #  Identity 

    @property
    @abc.abstractmethod
    def exchange_id(self) -> str:
        """Short exchange identifier, e.g. 'indodax' or 'binance'."""

    #  Lifecycle 

    @abc.abstractmethod
    def start(self) -> None:
        """Start data ingestion (non-blocking; runs in a background thread)."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop data ingestion and release resources."""

    @abc.abstractmethod
    def wait_ready(self, timeout: float = 30.0) -> bool:
        """
        Block until the first valid tick is received.

        Returns
        -------
        bool
            True if ready within *timeout* seconds, False otherwise.
        """

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Return True if the underlying connection is live."""

    #  Data access 

    def get_latest_tick(self) -> Optional[bytes]:
        """Return the most recently received serialised Tick bytes (or None)."""
        return self._latest_tick_bytes

    #  Helpers for subclasses 

    def _emit(self, tick_bytes: bytes, topic: str = "tick", latency_ms: float = 0) -> None:
        """
        Store the latest tick and invoke the on_tick callback (if set).
        Subclasses call this after serialising a new Tick message.
        """
        self._latest_tick_bytes = tick_bytes
        if self._on_tick is not None:
            try:
                # If the callback is the publisher.send, it will use the topic and latency_ms
                self._on_tick(tick_bytes, topic=topic, latency_ms=latency_ms)
            except Exception as exc:  # noqa: BLE001
                # Never let a faulty callback crash the provider thread
                import logging
                logging.getLogger(__name__).error(
                    f"[{self.exchange_id}] on_tick callback raised: {exc}"
                )

    def _emit_trade(self, trade_bytes: bytes, topic: str = "trade") -> None:
        """
        Invoke the on_trade callback (if set).
        Subclasses call this after serialising a new Trade message.
        """
        if self._on_trade is not None:
            try:
                self._on_trade(trade_bytes, topic=topic)
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).error(
                    f"[{self.exchange_id}] on_trade callback raised: {exc}"
                )

    #  Diagnostics 

    @property
    def uptime_seconds(self) -> float:
        """Seconds since start() was called."""
        return time.monotonic() - self._start_time if self._start_time else 0.0
