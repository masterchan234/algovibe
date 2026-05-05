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

from services.market_pipe.providers.base import MarketProvider, TickCallback, TradeCallback
from shared.algovibe_pb2 import Tick, Trade, ExchangeId  # type: ignore[attr-defined]
from app.infrastructure.ws_market import MarketDataWS

logger = logging.getLogger(__name__)


class IndodaxProvider(MarketProvider):
    """
    Indodax market-data provider.

    Connects to the Indodax WebSocket (via the existing MarketDataWS client),
    normalises every ticker and trade into Protobuf messages, and emits them
    via callbacks.
    """

    def __init__(
        self,
        on_tick: Optional[TickCallback] = None,
        on_trade: Optional[TradeCallback] = None,
    ):
        super().__init__(on_tick=on_tick, on_trade=on_trade)
        self._mws: MarketDataWS = MarketDataWS(
            on_tick=self._handle_tick_update,
            on_trade=self._handle_trade_update
        )
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
        
        # Subscribe to essential pairs for trades on startup
        threading.Thread(target=self._initial_subscriptions, daemon=True).start()
        
        logger.info("[IndodaxProvider] Started.")

    def _initial_subscriptions(self) -> None:
        """Subscribe to essential trade channels on startup."""
        time.sleep(2) # Wait for WS connection
        from app.infrastructure.database import db
        watch_pair = db.get_state('current_watch_pair') or 'btcidr'
        
        self._mws.subscribe_market_trades('btcidr')
        if watch_pair != 'btcidr':
            self._mws.subscribe_market_trades(watch_pair)
        
        # Also subscribe to all open positions
        try:
            with db.get_connection() as conn:
                rows = conn.execute("SELECT pair FROM positions").fetchall()
                for r in rows:
                    self._mws.subscribe_market_trades(r['pair'])
        except Exception: pass

    def stop(self) -> None:
        self._running = False
        self._mws.stop()
        logger.info("[IndodaxProvider] Stopped.")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        return self._mws.wait_ready(timeout=timeout)

    def is_connected(self) -> bool:
        return self._mws.is_connected()

    # ── Internal Handlers ─────────────────────────────────────────────────────

    def _handle_tick_update(self, pair: str, data: dict) -> None:
        """Callback from MarketDataWS when a ticker updates."""
        try:
            ts_ms = int(time.time() * 1000)
            price = _safe_float(data.get("last"))
            if price <= 0: return

            tick = Tick()
            tick.symbol      = pair
            tick.price       = price
            tick.volume      = _safe_float(data.get("vol_coin"))
            tick.timestamp   = ts_ms
            tick.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")
            tick.bid         = _safe_float(data.get("buy"))
            tick.ask         = _safe_float(data.get("sell"))
            tick.high_24h    = _safe_float(data.get("high"))
            tick.low_24h     = _safe_float(data.get("low"))
            tick.vol_idr     = _safe_float(data.get("vol_idr"))

            self._emit(tick.SerializeToString())
            
            if not self._ready_event.is_set():
                self._ready_event.set()
        except Exception as e:
            logger.error(f"[IndodaxProvider] Tick serialisation error for {pair}: {e}")

    def _handle_trade_update(self, pair: str, trade_data: list) -> None:
        """Callback from MarketDataWS when a new trade arrives."""
        # Format trade_data: [pair, timestamp, sequence, side, price, vol_idr, vol_coin]
        try:
            t = Trade()
            t.symbol = pair
            t.timestamp = int(trade_data[1] * 1000)
            t.tid = str(trade_data[2])
            t.side = str(trade_data[3])
            t.price = float(trade_data[4])
            t.amount = float(trade_data[6])
            t.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")

            logger.info(f"[IndodaxProvider] Emitting trade: {pair}")
            self._emit_trade(t.SerializeToString())
        except Exception as e:
            logger.error(f"[IndodaxProvider] Trade serialisation error for {pair}: {e}")

def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
