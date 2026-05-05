"""
services/executive_core/main.py — Executive-Core Microservice Entry Point
==========================================================================
The "brain & muscle" of AlgoVibe in microservice form.

Responsibilities:
  • Subscribe to market ticks   (ipc:///tmp/market_stream.ipc)
  • Subscribe to equity updates (ipc:///tmp/equity_stream.ipc)
  • Continuously track:
        Net PnL = Σ(unrealised) + Σ(realised today)
  • Run the Net-Zero Survival Guard (→ BASKET_EXIT if PnL ≤ 0 + dropping)
  • Run the Profit Lock (→ BASKET_EXIT once daily profit > Rp 1jt and floor hit)
  • Execute all API orders as high-priority async tasks
  • Persist state to SQLite in WAL mode

Run:
    python -m services.executive_core.main
"""

import asyncio
import logging
import os
import signal
import sys
import time

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.transport import (
    ZMQSubscriber,
    ZMQPublisher,
    IPC_MARKET,
    IPC_EQUITY,
    install_uvloop,
)
from shared.algovibe_pb2 import Tick, EquityUpdate  # type: ignore[attr-defined]
from shared.pid_lock import PidLock
from services.executive_core.net_zero import NetZeroGuard
from services.executive_core.profit_lock import ProfitLock
from app.infrastructure.database import db
from app.infrastructure.exchange_factory import create_indodax_exchange
from utils.logger import log_message, send_telegram_message
from config import FEE_BUY_TAKER, FEE_SELL_TAKER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("executive-core")

# Install uvloop before any asyncio usage
install_uvloop()

# ── Velocity calculation window ────────────────────────────────────────────────
_VELOCITY_WINDOW = 5  # keep last N samples for smoothing


class ExecutiveCoreService:
    """
    Stateful executive core that subscribes to ZMQ streams, maintains
    real-time P&L, and fires emergency exits when risk thresholds are hit.
    """

    def __init__(self):
        self._exchange = create_indodax_exchange()
        self._tick_sub = ZMQSubscriber(IPC_MARKET)
        self._equity_sub = ZMQSubscriber(IPC_EQUITY)
        self._equity_pub = ZMQPublisher(IPC_EQUITY)

        # Current tick cache  {symbol: Tick}
        self._ticks: dict = {}

        # Equity tracking
        self._realized_pnl: float = 0.0
        self._unrealized_pnl: float = 0.0
        self._pnl_samples: list = []   # [(ts, net_pnl), ...]
        self._peak_equity: float = 0.0

        # Velocity (IDR/s)
        self._velocity: float = 0.0

        # Guards
        self._net_zero = NetZeroGuard(on_basket_exit=self._on_basket_exit)
        self._profit_lock = ProfitLock(on_profit_lock_exit=self._on_profit_lock_exit)

        # Order execution queue (high-priority async)
        self._order_queue: asyncio.Queue = asyncio.Queue()

        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._tick_sub.start()
        self._equity_pub.start()

        logger.info("[executive-core] ✅ Started — subscribing to ZMQ streams.")

        await asyncio.gather(
            self._consume_ticks(),
            self._order_processor(),
            self._equity_broadcast_loop(),
        )

    def stop(self) -> None:
        self._running = False
        self._tick_sub.stop()
        self._equity_pub.stop()
        logger.info("[executive-core] Stopped.")

    # ── Tick consumer ─────────────────────────────────────────────────────────

    async def _consume_ticks(self) -> None:
        """Consume Tick messages from market-pipe and update P&L."""
        async for raw in self._tick_sub.aiter_messages():
            if not self._running:
                break
            try:
                tick = Tick()
                tick.ParseFromString(raw)
                self._ticks[tick.symbol] = tick
                # Recalculate unrealised P&L with new prices
                await self._update_unrealized_pnl()
            except Exception as exc:
                logger.error(f"[executive-core] Tick parse error: {exc}")

    # ── P&L calculation ───────────────────────────────────────────────────────

    async def _update_unrealized_pnl(self) -> None:
        """
        Recalculate unrealised P&L from open positions in the database
        and run risk guards.
        """
        try:
            open_positions = db.get_open_positions()
        except Exception as exc:
            logger.error(f"[executive-core] DB error fetching positions: {exc}")
            return

        total_unrealized = 0.0
        for pos in open_positions:
            pair = pos.get("pair") or pos.get("symbol", "")
            tick = self._ticks.get(pair)
            if not tick or tick.price <= 0:
                continue
            entry = float(pos.get("entry_price") or 0)
            amount = float(pos.get("amount_crypto") or 0)
            amount_idr = float(pos.get("amount_idr") or 0)
            if entry <= 0 or amount <= 0:
                continue

            fee_buy = amount_idr * FEE_BUY_TAKER
            fee_sell = amount * tick.price * FEE_SELL_TAKER
            gross = (tick.price - entry) * amount
            total_unrealized += gross - fee_buy - fee_sell

        self._unrealized_pnl = total_unrealized

        # Fetch today's realised P&L from DB
        try:
            stats = db.get_daily_stats()
            self._realized_pnl = float(stats.get("total_pnl_idr") or 0.0)
        except Exception:
            pass

        net_pnl = self._realized_pnl + self._unrealized_pnl
        self._update_velocity(net_pnl)

        # Peak equity tracking
        if net_pnl > self._peak_equity:
            self._peak_equity = net_pnl

        # ── Run guards ────────────────────────────────────────────────────────
        self._net_zero.evaluate(net_pnl, self._velocity)
        self._profit_lock.update(net_pnl)

    def _update_velocity(self, net_pnl: float) -> None:
        """Update the velocity rolling buffer."""
        now = time.time()
        self._pnl_samples.append((now, net_pnl))
        if len(self._pnl_samples) > _VELOCITY_WINDOW:
            self._pnl_samples.pop(0)

        if len(self._pnl_samples) >= 2:
            oldest_ts, oldest_pnl = self._pnl_samples[0]
            dt = now - oldest_ts
            if dt > 0:
                self._velocity = (net_pnl - oldest_pnl) / dt

    # ── Equity broadcast ──────────────────────────────────────────────────────

    async def _equity_broadcast_loop(self) -> None:
        """Publish EquityUpdate every second to ipc:///tmp/equity_stream.ipc."""
        while self._running:
            try:
                equity = EquityUpdate()
                equity.total_realized   = self._realized_pnl
                equity.total_unrealized = self._unrealized_pnl
                equity.net_pnl          = self._realized_pnl + self._unrealized_pnl
                equity.velocity         = self._velocity
                equity.timestamp        = int(time.time() * 1000)
                equity.peak_equity      = self._peak_equity

                self._equity_pub.send(equity.SerializeToString())

                # Persist snapshot to DB for dashboard
                db.save_state("net_pnl_unrealized", str(round(self._unrealized_pnl, 2)))
                db.save_state("net_pnl_realized",   str(round(self._realized_pnl, 2)))

            except Exception as exc:
                logger.error(f"[executive-core] Equity broadcast error: {exc}")

            await asyncio.sleep(1.0)

    # ── Order execution ───────────────────────────────────────────────────────

    async def _order_processor(self) -> None:
        """
        Drain the order queue as high-priority async tasks.
        Orders are placed via the exchange adapter (non-blocking coroutine).
        """
        while self._running:
            try:
                order_task = await asyncio.wait_for(
                    self._order_queue.get(), timeout=1.0
                )
                asyncio.create_task(self._execute_order(order_task))
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error(f"[executive-core] Order processor error: {exc}")

    async def _execute_order(self, order: dict) -> None:
        """Execute a single order via the exchange adapter."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._exchange.place_order(
                    symbol=order["symbol"],
                    side=order["side"],
                    order_type=order.get("type", "limit"),
                    volume=order["volume"],
                    price=order.get("price"),
                ),
            )
            logger.info(
                f"[executive-core] Order placed: {order['side'].upper()} "
                f"{order['symbol']} → order_id={result.get('order_id')}"
            )
            # Persist to DB
            db.upsert_order_book(result)
        except Exception as exc:
            logger.error(f"[executive-core] Order execution failed: {exc}")
            send_telegram_message(f"⚠️ Order error: {exc}")

    # ── Guard callbacks ───────────────────────────────────────────────────────

    def _on_basket_exit(self, reason: str) -> None:
        """Triggered by NetZeroGuard — queue sell orders for all open positions."""
        logger.warning(f"[executive-core] BASKET_EXIT triggered: {reason}")
        send_telegram_message(f"🚨 BASKET EXIT ({reason}) — closing all positions")
        self._queue_close_all(reason)

    def _on_profit_lock_exit(self, reason: str, floor: float) -> None:
        """Triggered by ProfitLock — queue sell orders to lock profits."""
        logger.warning(
            f"[executive-core] PROFIT LOCK EXIT: {reason} | floor={floor:,.0f} IDR"
        )
        send_telegram_message(
            f"🔒 PROFIT LOCK EXIT ({reason}) — floor={floor:,.0f} IDR"
        )
        self._queue_close_all(reason)

    def _queue_close_all(self, reason: str) -> None:
        """Enqueue sell orders for every open position."""
        try:
            positions = db.get_open_positions()
            for pos in positions:
                pair = pos.get("pair") or pos.get("symbol", "")
                tick = self._ticks.get(pair)
                price = tick.price if tick and tick.price > 0 else None
                self._order_queue.put_nowait({
                    "symbol": pair,
                    "side": "sell",
                    "type": "market" if price is None else "limit",
                    "volume": float(pos.get("amount_crypto") or 0),
                    "price": price,
                    "_reason": reason,
                })
        except Exception as exc:
            logger.error(f"[executive-core] _queue_close_all error: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("  AlgoVibe Executive-Core Service starting…")
    logger.info("=" * 60)

    pid_lock = PidLock()
    pid_lock.acquire()

    service = ExecutiveCoreService()
    _shutdown = False

    def _handle_signal(signum, _frame):
        nonlocal _shutdown
        logger.info(f"[executive-core] Signal {signum} — shutting down…")
        _shutdown = True
        service.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        asyncio.run(service.start())
    finally:
        service.stop()
        pid_lock.release()
        logger.info("[executive-core] Exited cleanly.")


if __name__ == "__main__":
    main()
