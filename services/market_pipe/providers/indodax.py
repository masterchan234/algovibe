"""
services/market_pipe/providers/indodax.py — Dynamic Market Provider
==================================================================
Wraps the dynamic MarketDataWS (app/infrastructure/ws_market.py) and
translates its internal ticker dict into Protobuf Tick/Trade messages.
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
    Dynamic exchange provider supporting Indodax and Binance Spot.
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
        self._mws.write_to_db = True
        self._running = False
        self._ready_event = threading.Event()

    #  Identity 

    @property
    def exchange_id(self) -> str:
        return os.getenv("EXCHANGE", "binance").lower()

    #  Lifecycle 

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._running = True
        self._mws.start()
        
        # Subscribe to essential pairs for trades on startup
        threading.Thread(target=self._initial_subscriptions, daemon=True).start()
        logger.info(f"[{self.exchange_id.upper()} Provider] Started.")

    def _initial_subscriptions(self) -> None:
        """Subscribe to essential trade channels on startup."""
        time.sleep(2)  # Wait for WS connection
        from app.infrastructure.database import db
        
        # Wait gracefully for database migrations to be ready (up to 60 seconds)
        eid = self.exchange_id
        default_pair = 'btcusdt' if eid == 'binance' else 'btcidr'
        watch_pair = default_pair
        
        db_ready = False
        for i in range(30):
            try:
                watch_pair = db.get_state('current_watch_pair') or default_pair
                with db.get_connection() as conn:
                    conn.execute("SELECT 1 FROM positions LIMIT 1")
                logger.info(f"[{eid.upper()} Provider] DB tables are ready for initial subscriptions.")
                db_ready = True
                break
            except Exception as e:
                logger.warning(f"[{eid.upper()} Provider] DB not ready yet (attempt {i+1}/30): {e}")
                time.sleep(2)
        
        if not db_ready:
            logger.error(f"[{eid.upper()} Provider] DB tables never became ready. Falling back.")

        pairs = {default_pair, str(watch_pair).lower()}

        # Also subscribe to all open positions
        try:
            with db.get_connection() as conn:
                rows = conn.execute("SELECT pair FROM positions").fetchall()
                for r in rows:
                    p = str(r['pair'] or '').lower().strip()
                    if p:
                        pairs.add(p)

                hist_rows = conn.execute(
                    "SELECT pair FROM ticker_history ORDER BY updated_at DESC LIMIT 40"
                ).fetchall()
                for r in hist_rows:
                    p = str(r['pair'] or '').lower().strip()
                    if p:
                        pairs.add(p)
        except Exception as e:
            logger.warning(f"[{eid.upper()} Provider] Could not load positions or history: {e}")

        # Subscribe dynamically in bulk to respect exchange rate limits
        subscribed = 0
        try:
            if hasattr(self._mws, 'subscribe_market_trades_bulk'):
                self._mws.subscribe_market_trades_bulk(list(pairs))
                subscribed = len(pairs)
            else:
                for p in sorted(pairs):
                    self._mws.subscribe_market_trades(p)
                    subscribed += 1
                    time.sleep(0.05)
        except Exception as e:
            logger.warning(f"[{eid.upper()} Provider] Trade subscriptions failed: {e}")

        logger.info(f"[{eid.upper()} Provider] Initial trade subscriptions registered: {subscribed} pairs")

    def stop(self) -> None:
        self._running = False
        self._mws.stop()
        logger.info(f"[{self.exchange_id.upper()} Provider] Stopped.")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        return self._mws.wait_ready(timeout=timeout)

    def is_connected(self) -> bool:
        return self._mws.is_connected()

    #  Internal Handlers 

    def _handle_tick_update(self, pair: str, data: dict) -> None:
        try:
            ts_ms = int(time.time() * 1000)
            price = _safe_float(data.get("last"))
            if price <= 0: return

            tick = Tick()
            tick.symbol      = pair
            tick.price       = price
            tick.volume      = _safe_float(data.get("vol_coin"))
            tick.timestamp   = ts_ms
            
            eid = self.exchange_id
            if eid == "binance":
                tick.exchange_id = ExchangeId.Value("EXCHANGE_BINANCE")
            else:
                tick.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")
                
            tick.bid         = _safe_float(data.get("buy"))
            tick.ask         = _safe_float(data.get("sell"))
            tick.high_24h    = _safe_float(data.get("high"))
            tick.low_24h     = _safe_float(data.get("low"))
            tick.vol_idr     = _safe_float(data.get("vol_idr"))

            # Latency check
            latency_ms = 0.0
            try:
                epoch = data.get("epoch")
                if epoch:
                    latency_ms = (time.time() - float(epoch)) * 1000.0
            except Exception: pass

            self._emit(tick.SerializeToString(), latency_ms=latency_ms)
            
            if not self._ready_event.is_set():
                self._ready_event.set()
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()} Provider] Tick serialisation error for {pair}: {e}")

    def _handle_trade_update(self, pair: str, trade_data: list) -> None:
        try:
            t = Trade()
            t.symbol = pair
            t.timestamp = int(trade_data[1] * 1000)
            t.tid = str(trade_data[2])
            t.side = str(trade_data[3])
            t.price = float(trade_data[4])
            t.amount = float(trade_data[6])
            
            eid = self.exchange_id
            if eid == "binance":
                t.exchange_id = ExchangeId.Value("EXCHANGE_BINANCE")
            else:
                t.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")

            logger.info(f"[{eid.upper()} Provider] Emitting trade: {pair}")
            self._emit_trade(t.SerializeToString())
        except Exception as e:
            logger.error(f"[{eid.upper()} Provider] Trade serialisation error for {pair}: {e}")


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
