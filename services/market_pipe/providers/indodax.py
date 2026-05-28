"""
services/market_pipe/providers/indodax.py — Indodax Market Provider
====================================================================
Wraps the dynamic MarketDataWS (app/infrastructure/ws_market.py) and
translates its internal ticker dict into Protobuf Tick/Trade messages.

Khusus untuk Indodax exchange. Untuk Binance gunakan BinanceProvider.
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
    """Market provider untuk Indodax exchange."""

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
        return "indodax"

    #  Lifecycle 

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._running = True
        self._mws.start()
        threading.Thread(target=self._initial_subscriptions, daemon=True).start()
        logger.info("[INDODAX Provider] Started.")

    def _initial_subscriptions(self) -> None:
        """Subscribe ke pair-pair penting saat startup dari DB history."""
        time.sleep(2)
        from app.infrastructure.database import db

        watch_pair = 'btcidr'
        db_ready = False
        for i in range(30):
            try:
                watch_pair = db.get_state('current_watch_pair') or 'btcidr'
                with db.get_connection() as conn:
                    conn.execute("SELECT 1 FROM positions LIMIT 1")
                db_ready = True
                break
            except Exception as e:
                logger.warning("[INDODAX Provider] DB not ready (attempt %d/30): %s", i + 1, e)
                time.sleep(2)

        if not db_ready:
            logger.error("[INDODAX Provider] DB never became ready. Falling back.")

        pairs = {'btcidr', str(watch_pair).lower()}
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
            logger.warning("[INDODAX Provider] Could not load positions/history: %s", e)

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
            logger.warning("[INDODAX Provider] Trade subscriptions failed: %s", e)

        logger.info("[INDODAX Provider] Initial subscriptions: %d pairs", subscribed)

    def stop(self) -> None:
        self._running = False
        self._mws.stop()
        logger.info("[INDODAX Provider] Stopped.")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        return self._mws.wait_ready(timeout=timeout)

    def is_connected(self) -> bool:
        return self._mws.is_connected()

    #  Internal Handlers 

    def _handle_tick_update(self, pair: str, data: dict) -> None:
        try:
            ts_ms = int(time.time() * 1000)
            price = _safe_float(data.get("last"))
            if price <= 0:
                return

            tick = Tick()
            tick.symbol   = pair
            tick.price    = price
            tick.volume   = _safe_float(data.get("vol_coin"))
            tick.timestamp = ts_ms
            tick.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")
            tick.bid      = _safe_float(data.get("buy"))
            tick.ask      = _safe_float(data.get("sell"))
            tick.high_24h = _safe_float(data.get("high"))
            tick.low_24h  = _safe_float(data.get("low"))
            tick.vol_idr  = _safe_float(data.get("vol_idr"))

            latency_ms = 0.0
            try:
                epoch = data.get("epoch")
                if epoch:
                    latency_ms = (time.time() - float(epoch)) * 1000.0
            except Exception:
                pass

            self._emit(tick.SerializeToString(), latency_ms=latency_ms)
            if not self._ready_event.is_set():
                self._ready_event.set()
        except Exception as e:
            logger.error("[INDODAX Provider] Tick error for %s: %s", pair, e)

    def _handle_trade_update(self, pair: str, trade_data: list) -> None:
        try:
            t = Trade()
            t.symbol      = pair
            t.timestamp   = int(trade_data[1] * 1000)
            t.tid         = str(trade_data[2])
            t.side        = str(trade_data[3])
            t.price       = float(trade_data[4])
            t.amount      = float(trade_data[6])
            t.exchange_id = ExchangeId.Value("EXCHANGE_INDODAX")
            logger.info("[INDODAX Provider] Emitting trade: %s", pair)
            self._emit_trade(t.SerializeToString())
        except Exception as e:
            logger.error("[INDODAX Provider] Trade error for %s: %s", pair, e)


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
