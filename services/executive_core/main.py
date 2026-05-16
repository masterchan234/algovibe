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
from collections import defaultdict

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
    IPC_SIGNAL,
    IPC_LLM_SIGNAL,
    IPC_COMMAND,
    install_uvloop,
)
from shared.algovibe_pb2 import Tick, EquityUpdate, Signal, Trade  # type: ignore[attr-defined]
from shared.pid_lock import PidLock
from services.executive_core.net_zero import NetZeroGuard
from services.executive_core.profit_lock import ProfitLock
from app.infrastructure.database import db
from app.infrastructure.exchange_factory import create_indodax_exchange
from app.infrastructure.ws_private import PrivateWS
from utils.logger import log_message, send_telegram_message
from config import FEE_BUY_TAKER, FEE_SELL_TAKER
import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("executive-core")

# Install uvloop before any asyncio usage
install_uvloop()

#  Velocity calculation window 
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
        self._signal_sub = ZMQSubscriber(IPC_SIGNAL)
        self._llm_signal_sub = ZMQSubscriber(IPC_LLM_SIGNAL)
        self._command_sub = ZMQSubscriber(IPC_COMMAND)
        self._equity_pub = ZMQPublisher(IPC_EQUITY)

        # Current tick cache  {symbol: Tick}
        self._ticks: dict = {}

        # Equity tracking
        self._realized_pnl: float = 0.0
        self._unrealized_pnl: float = 0.0
        self._pnl_samples: list = []   # [(ts, net_pnl), ...]
        self._peak_equity: float = 0.0
        try:
            self._peak_equity = float(db.get_state("peak_net_pnl_session", 0.0) or 0.0)
        except Exception:
            self._peak_equity = 0.0

        # Velocity (IDR/s)
        self._velocity: float = 0.0
        
        # Price history buffer for Dashboard
        self._price_history: dict = defaultdict(list)
        self._last_history_save: float = 0.0

        # Guards
        self._net_zero = NetZeroGuard(on_basket_exit=self._on_basket_exit)
        self._profit_lock = ProfitLock(on_profit_lock_exit=self._on_profit_lock_exit)

        # Order execution queue (high-priority async)
        self._order_queue: asyncio.Queue = asyncio.Queue()

        self._running = False
        
        # [Restoration] Bot-like interface for domain logic reuse
        from app.application.oracle import Oracle
        class ZMQMarketProxy:
            """Adapts the live ZMQ tick cache to the MarketDataWS interface expected
            by Oracle.  Uses real tick prices and real price history — no mock data."""
            def __init__(self, ticks, price_history):
                self.ticks = ticks
                self.price_history = price_history  # shared ref to _price_history dict
            def get_tickers(self):
                out = {}
                for s, t in self.ticks.items():
                    last = float(t.price or 0.0)
                    # Derive change_24h from rolling price history where available
                    hist = self.price_history.get(s, [])
                    if len(hist) >= 2:
                        oldest = float(hist[0] or last)
                        change_24h = ((last - oldest) / oldest * 100.0) if oldest > 0 else 0.0
                    else:
                        change_24h = 0.0
                    out[s] = {
                        "pair": s,
                        "last": last,
                        "bid": float(t.bid or last),
                        "ask": float(t.ask or last),
                        "high": float(t.high_24h or last),
                        "low": float(t.low_24h or last),
                        "vol_idr": float(t.vol_idr or 0.0),
                        "change_24h": change_24h,
                    }
                return out
            def is_connected(self):
                return True
            def get_latency(self):
                return 0.0
            def get_order_book(self, pair):
                # Return single-level depth from the tick cache so Oracle whale
                # detection and fake-pump logic have real bid/ask data to work with.
                # Volume per level is unknown (not in Tick proto), so amount=0 is set;
                # WHALE_THRESHOLD_IDR checks will not fire until a depth ZMQ stream
                # is added, but detect_fake_pump will no longer return True by default.
                t = self.ticks.get(pair)
                if not t:
                    return {"bids": [], "asks": []}
                bid = float(t.bid or t.price or 0.0)
                ask = float(t.ask or t.price or 0.0)
                return {
                    "bids": [[bid, 0.0]] if bid > 0 else [],
                    "asks": [[ask, 0.0]] if ask > 0 else [],
                }
        
        self.market_ws = ZMQMarketProxy(self._ticks, self._price_history)
        self.oracle = Oracle(self.market_ws)
        self.cached_balance = 0.0
        # Compatibility aliases for legacy use_cases.trade_execution
        self.exchange = self._exchange
        self.price_history = self._price_history

        # Legacy parity mode: reuse full ExitManager logic (TP/SL/force-profit/manual-exit).
        from app.application.exit_manager import ExitManager
        self.exit_manager = ExitManager(self._exchange, oracle=self.oracle, bot=self)
        self._legacy_exit_enabled = True

        # Private WS order updates are required so pending BUY/SELL can be reconciled.
        api_key = os.getenv("INDODAX_API_KEY", "")
        api_secret = os.getenv("INDODAX_API_SECRET", "")
        self._private_ws = None
        if api_key and api_secret:
            self._private_ws = PrivateWS(
                api_key=api_key,
                api_secret=api_secret,
                on_order_update=self.exit_manager.handle_order_update,
            )
        else:
            logger.warning("[executive-core] PrivateWS disabled: missing API credentials")

    #  Lifecycle 

    async def start(self) -> None:
        self._running = True
        try:
            db.save_state("executive_core_started_at", str(time.time()))
        except Exception:
            pass
        self._tick_sub.start()
        self._signal_sub.start()
        self._llm_signal_sub.start()
        self._command_sub.start()
        self._equity_pub.start()
        if self._private_ws:
            self._private_ws.start()

        # Warm cache from 24h REST snapshot to avoid vol_idr=0 on cold start.
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._warmup_market_snapshot)
        except Exception as exc:
            logger.warning(f"[executive-core] Market snapshot warmup skipped: {exc}")

        logger.info("[executive-core] [OK] Started — subscribing to ZMQ streams.")

        # Keep legacy reconciliation behavior at startup.
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.exit_manager.reconcile_with_exchange)
        except Exception as exc:
            logger.warning(f"[executive-core] Exit reconcile skipped: {exc}")

        await asyncio.gather(
            self._consume_ticks(),
            self._consume_signals(),
            self._consume_llm_signals(),
            self._consume_commands(),
            self._order_processor(),
            self._equity_broadcast_loop(),
            self._legacy_exit_monitor_loop(),
        )

    def stop(self) -> None:
        self._running = False
        self._tick_sub.stop()
        self._signal_sub.stop()
        self._llm_signal_sub.stop()
        self._command_sub.stop()
        self._equity_pub.stop()
        if self._private_ws:
            self._private_ws.stop()
        logger.info("[executive-core] Stopped.")

    def _build_current_tickers_snapshot(self) -> dict:
        """Convert protobuf tick cache into MarketWS-compatible ticker dict."""
        out = {}
        for pair, tick in self._ticks.items():
            last = float(tick.price or 0.0)
            hist = self._price_history.get(pair, [])
            if len(hist) >= 2:
                oldest = float(hist[0] or last)
                change_24h = ((last - oldest) / oldest * 100.0) if oldest > 0 else 0.0
            else:
                change_24h = 0.0
            out[pair] = {
                "pair": pair,
                "last": last,
                "buy": float(tick.bid or last),
                "sell": float(tick.ask or last),
                "high": float(tick.high_24h or last),
                "low": float(tick.low_24h or last),
                "vol_idr": float(tick.vol_idr or 0.0),
                "change_24h": change_24h,
            }
        return out

    async def _legacy_exit_monitor_loop(self) -> None:
        """Parity loop: run legacy ExitManager every 2s like the monolith."""
        if not self._legacy_exit_enabled:
            return

        get_prediction = None
        try:
            from app.domain.predictor import get_prediction as _gp
            get_prediction = _gp
        except Exception as exc:
            logger.warning(f"[executive-core] predictor unavailable for exit monitor: {exc}")

        loop_cycle = 0
        while self._running:
            try:
                tickers = self._build_current_tickers_snapshot()
                loop_cycle += 1
                if loop_cycle % 30 == 0:  # Log every 60 seconds (30 cycles × 2s)
                    logger.info(f"[exit-monitor] Cycle #{loop_cycle}: {len(tickers)} tickers in snapshot")
                
                if tickers:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: self.exit_manager.monitor_and_execute(
                            current_tickers=tickers,
                            price_history=self.price_history,
                            get_prediction_fn=get_prediction,
                        ),
                    )
            except Exception as exc:
                logger.error(f"[executive-core] Legacy exit monitor error: {exc}")

            await asyncio.sleep(2.0)

    def _sync_balances_to_db(self) -> None:
        """Compatibility hook used by ExitManager order-update callback."""
        try:
            bal = self._exchange.fetch_balance()
            db.save_balances(bal)
            self.cached_balance = float(bal.get("free", {}).get("IDR", self.cached_balance))
        except Exception as exc:
            logger.debug(f"[executive-core] _sync_balances_to_db skip: {exc}")

    def _warmup_market_snapshot(self) -> None:
        """Seed _ticks from exchange 24h ticker snapshot for all IDR pairs."""
        try:
            # Add timeout to prevent hanging if exchange API is laggy
            self._exchange.timeout = 30000 # 30s
            raw_tickers = self._exchange.fetch_tickers()
        except Exception as exc:
            logger.warning(f"[executive-core] Warmup fetch_tickers failed: {exc}")
            return

        if not isinstance(raw_tickers, dict):
            logger.warning("[executive-core] Warmup fetch_tickers returned non-dict payload")
            return

        seeded = 0
        ts_ms = int(time.time() * 1000)

        for symbol, payload in raw_tickers.items():
            sym = str(symbol or "")
            if "/" in sym:
                pair = sym.replace("/", "").lower()
            else:
                pair = sym.lower()
            if not pair.endswith("idr"):
                continue

            info = payload.get("info") if isinstance(payload, dict) else {}
            info = info if isinstance(info, dict) else {}

            last = float(payload.get("last") or info.get("last") or 0.0)
            bid = float(payload.get("bid") or info.get("buy") or last)
            ask = float(payload.get("ask") or info.get("sell") or last)
            high = float(payload.get("high") or info.get("high") or last)
            low = float(payload.get("low") or info.get("low") or last)
            vol_idr = float(
                payload.get("quoteVolume")
                or info.get("vol_idr")
                or info.get("vol_idr24h")
                or 0.0
            )
            vol_coin = float(payload.get("baseVolume") or info.get("vol_btc") or 0.0)

            if last <= 0:
                continue

            tick = Tick()
            tick.symbol = pair
            tick.price = last
            tick.volume = vol_coin
            tick.timestamp = ts_ms
            tick.bid = bid
            tick.ask = ask
            tick.high_24h = high
            tick.low_24h = low
            tick.vol_idr = vol_idr

            self._ticks[pair] = tick
            seeded += 1

        logger.info(f"[executive-core] Warmup snapshot seeded: {seeded} IDR pairs")

    #  Signal consumer 

    async def _consume_signals(self) -> None:
        """Consume Signal messages from brain-inference and execute trades."""
        async for raw in self._signal_sub.aiter_messages():
            if not self._running:
                break
            try:
                sig = Signal()
                sig.ParseFromString(raw)
                
                if sig.action == 1: # BUY
                    # --- [DYNAMIC TARGETING UNPACK] ---
                    raw_symbol = sig.symbol
                    pair = raw_symbol
                    final_tp = cfg.get_dynamic_tp_pct(self.cached_balance)
                    final_sl = cfg.get_dynamic_sl_pct(self.cached_balance)

                    if "|" in raw_symbol:
                        parts = raw_symbol.split("|")
                        pair = parts[0]
                        if len(parts) >= 3:
                            try:
                                final_tp = float(parts[1])
                                final_sl = float(parts[2])
                                logger.info(f"[EXECUTIVE] Using INTELLIGENT targets for {pair}: TP {final_tp*100:.2f}%, SL {final_sl*100:.2f}%")
                            except Exception: pass

                    logger.info(f"[EXECUTIVE] Received BUY signal for {pair} (conf: {sig.confidence:.1f}%)")
                    # Enqueue a buy order. 
                    # Use the domain execute_trade logic
                    from app.application.use_cases.trade_execution import execute_trade
                    tq_prob = float(sig.confidence or 0.0)
                    # Compatibility: accept either 0..1 or 0..100 confidence payloads.
                    if tq_prob > 1.0:
                        tq_prob /= 100.0
                    tq_prob = max(0.0, min(1.0, tq_prob))
                    tick_obj = self._ticks.get(pair)
                    entry_price = float(tick_obj.price) if tick_obj and float(tick_obj.price) > 0 else 0.0
                    if entry_price <= 0:
                        logger.warning(f"[executive-core] BUY signal ignored for {pair}: no valid tick price yet")
                        continue
                    
                    # We run this in a thread because it might do blocking DB/API calls
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: execute_trade(
                            self,
                            pair,
                            entry_price,
                            trend="ORACLE_SIGNAL",
                            tq_prob=tq_prob,
                            dynamic_tp=final_tp,
                            dynamic_sl=final_sl,
                            source="ORACLE"
                        )
                    )
                elif sig.action == 2:  # SELL
                    pair = str(sig.symbol or "").lower()
                    logger.info(f"[EXECUTIVE] Received SELL signal for {pair} (conf: {sig.confidence:.1f}%)")
                    pos = db.get_position(pair)
                    if pos and pos.get("status") == "OPEN":
                        tick = self._ticks.get(pair)
                        px = float(tick.price) if tick and tick.price > 0 else float(pos.get("entry_price") or 0)
                        if px > 0:
                            await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: self.exit_manager.execute_exit(
                                    pair=pair,
                                    amount=float(pos.get("amount_crypto") or 0),
                                    price=px,
                                    reason="SIGNAL_SELL",
                                    entry_price=float(pos.get("entry_price") or 0),
                                    amount_idr=float(pos.get("amount_idr") or 0),
                                    position_data=pos,
                                ),
                            )
            except Exception as exc:
                logger.error(f"[EXECUTIVE] Failed to parse signal: {exc}")

    async def _consume_llm_signals(self) -> None:
        """Consume Signal messages from litert-lm-decision (LLM signals).

        Ketika TECHNICAL_ONLY_MODE aktif, semua signal dari LLM diabaikan —
        keputusan BUY/SELL murni dari jalur teknikal (brain-inference).
        Service litert-lm-decision tetap berjalan untuk keperluan lain (artikel, insight).
        """
        async for raw in self._llm_signal_sub.aiter_messages():
            if not self._running:
                break
            try:
                sig = Signal()
                sig.ParseFromString(raw)

                # [TECHNICAL_ONLY_MODE] Abaikan semua signal LLM jika mode teknikal aktif.
                if cfg.TECHNICAL_ONLY_MODE:
                    logger.debug(
                        f"[EXECUTIVE] LLM signal for {sig.symbol} ignored (TECHNICAL_ONLY_MODE active)"
                    )
                    continue

                if sig.action == 1:  # BUY
                    # --- [DYNAMIC TARGETING UNPACK] ---
                    raw_symbol = sig.symbol
                    pair = raw_symbol
                    final_tp = cfg.get_dynamic_tp_pct(self.cached_balance)
                    final_sl = cfg.get_dynamic_sl_pct(self.cached_balance)

                    if "|" in raw_symbol:
                        parts = raw_symbol.split("|")
                        pair = parts[0]
                        if len(parts) >= 3:
                            try:
                                final_tp = float(parts[1])
                                final_sl = float(parts[2])
                                logger.info(f"[EXECUTIVE] Using INTELLIGENT targets for LLM {pair}: TP {final_tp*100:.2f}%, SL {final_sl*100:.2f}%")
                            except Exception: pass

                    logger.info(f"[EXECUTIVE] Received LLM BUY signal for {pair} (conf: {sig.confidence:.1f}%)")
                    from app.application.use_cases.trade_execution import execute_trade
                    tq_prob = float(sig.confidence or 0.0)
                    if tq_prob > 1.0:
                        tq_prob /= 100.0
                    tq_prob = max(0.0, min(1.0, tq_prob))
                    tick_obj = self._ticks.get(pair)
                    entry_price = float(tick_obj.price) if tick_obj and float(tick_obj.price) > 0 else 0.0
                    if entry_price <= 0:
                        logger.warning(f"[EXECUTIVE] LLM BUY signal ignored for {pair}: no valid tick price yet")
                        continue

                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: execute_trade(
                            self,
                            pair,
                            entry_price,
                            trend="LLM_SIGNAL",
                            tq_prob=tq_prob,
                            dynamic_tp=dyn_tp,
                            dynamic_sl=dyn_sl,
                            taker=True,
                        )
                    )
                elif sig.action == 2:  # SELL
                    pair = str(sig.symbol or "").lower()
                    logger.info(f"[EXECUTIVE] Received LLM SELL signal for {pair} (conf: {sig.confidence:.1f}%)")
                    pos = db.get_position(pair)
                    if pos and pos.get("status") == "OPEN":
                        tick = self._ticks.get(pair)
                        px = float(tick.price) if tick and tick.price > 0 else float(pos.get("entry_price") or 0)
                        if px > 0:
                            await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: self.exit_manager.execute_exit(
                                    pair=pair,
                                    amount=float(pos.get("amount_crypto") or 0),
                                    price=px,
                                    reason="LLM_SIGNAL_SELL",
                                    entry_price=float(pos.get("entry_price") or 0),
                                    amount_idr=float(pos.get("amount_idr") or 0),
                                    position_data=pos,
                                ),
                            )
            except Exception as exc:
                logger.error(f"[EXECUTIVE] Failed to parse LLM signal: {exc}")

    #  Command consumer 

    async def _consume_commands(self) -> None:
        """Consume command messages from dashboard (e.g., mode changes)."""
        import json
        async for raw in self._command_sub.aiter_messages():
            if not self._running:
                break
            try:
                cmd = json.loads(raw.decode())
                action = cmd.get("action")
                payload = cmd.get("payload", {})
                
                if action == "MODE_CHANGE":
                    new_dry_run = payload.get("dry_run")
                    logger.info(f"[EXECUTIVE] Mode Change Received: {'DRY RUN' if new_dry_run else 'LIVE'}")
                    # Force reload of DB mode cache and config cache
                    import config as cfg
                    cfg.clear_dry_run_cache()
                    db._mode_cache = None
                    # Update local state if needed
                    # ExitManager already checks db.is_dry_run() which now points to the new DB
                    # Re-reconcile with exchange for the new mode
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.exit_manager.reconcile_with_exchange)
            except Exception as exc:
                logger.error(f"[EXECUTIVE] Failed to process command: {exc}")

    #  Tick consumer 

    async def _consume_ticks(self) -> None:
        """Consume Tick messages from market-pipe and update P&L."""
        async for raw in self._tick_sub.aiter_messages():
            if not self._running:
                break
            try:
                tick = Tick()
                tick.ParseFromString(raw)
            except Exception as exc:
                logger.error(
                    f"[EXECUTIVE] Failed to parse tick message: {exc}"
                )
                continue
            try:
                prev = self._ticks.get(tick.symbol)
                if prev is not None:
                    if float(tick.vol_idr or 0.0) <= 0:
                        tick.vol_idr = float(prev.vol_idr or 0.0)
                    if float(tick.high_24h or 0.0) <= 0:
                        tick.high_24h = float(prev.high_24h or tick.price)
                    if float(tick.low_24h or 0.0) <= 0:
                        tick.low_24h = float(prev.low_24h or tick.price)
                    if float(tick.bid or 0.0) <= 0:
                        tick.bid = float(prev.bid or tick.price)
                    if float(tick.ask or 0.0) <= 0:
                        tick.ask = float(prev.ask or tick.price)

                self._ticks[tick.symbol] = tick
                # History buffering for radar/dashboard
                if tick.symbol not in self._price_history:
                    self._price_history[tick.symbol] = []
                # Only append if price changed or it's empty to save space
                if not self._price_history[tick.symbol] or self._price_history[tick.symbol][-1] != tick.price:
                    self._price_history[tick.symbol].append(tick.price)
                    if len(self._price_history[tick.symbol]) > 60:
                        self._price_history[tick.symbol].pop(0)

            except Exception as exc:
                logger.error(f"[EXECUTIVE] Tick processing error: {exc}")

    def _flush_ticker_history(self) -> None:
        """Flushes the buffered price history to DB for dashboard radar."""
        try:
            updates = []
            for symbol, hist in self._price_history.items():
                if len(hist) > 0:
                    updates.append((symbol, list(hist), 0.0, 0.0, 0.0))
            if updates:
                db.save_ticker_history_batch(updates)
        except Exception as e:
            logger.error(f"[EXECUTIVE] Failed to flush ticker_history: {e}")

    #  P&L calculation 

    async def _update_unrealized_pnl(self) -> None:
        """
        Recalculate unrealised P&L from open positions in the database
        and run risk guards.
        """
        try:
            open_positions = db.get_open_positions()
        except Exception as exc:
            logger.error(f"[EXECUTIVE] DB error fetching positions: {exc}")
            return

        if isinstance(open_positions, dict):
            positions_iter = open_positions.values()
        else:
            positions_iter = open_positions

        current_dr_val = 1 if db.is_dry_run() else 0
        total_unrealized = 0.0
        for pos in positions_iter:
            if int(pos.get("dry_run", 0)) != current_dr_val:
                continue
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

        #  Update Balance & Stats 
        try:
            # Update balance every 10 seconds to avoid rate limits
            if int(time.time()) % 10 == 0:
                loop = asyncio.get_event_loop()
                bal_data = await loop.run_in_executor(None, self._exchange.fetch_balance)
                self.cached_balance = float(bal_data.get('free', {}).get('IDR', 0))
            
            stats = db.get_all_time_stats()
            self._realized_pnl = float(stats.get("total_pnl_idr") or 0.0)
        except Exception as e:
            logger.debug(f"[EXECUTIVE] Balance/Stats fetch skip: {e}")
            pass

        net_pnl = self._realized_pnl + self._unrealized_pnl
        self._update_velocity(net_pnl)

        # Peak equity tracking
        if net_pnl > self._peak_equity:
            self._peak_equity = net_pnl

        #  Run guards 
        # In legacy-parity mode, ExitManager handles advanced exit orchestration.
        if not self._legacy_exit_enabled:
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

    #  Equity broadcast 

    async def _equity_broadcast_loop(self) -> None:
        """Publish EquityUpdate every second to ipc:///tmp/equity_stream.ipc."""
        _heartbeat_counter = 0
        while self._running:
            try:
                # 1. Update PnL once per second instead of per-tick
                await self._update_unrealized_pnl()

                # 2. Flush history periodically here
                now = time.time()
                if now - self._last_history_save > 30:
                    self._flush_ticker_history()
                    self._last_history_save = now

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
                db.save_state("peak_net_pnl_session", str(round(self._peak_equity, 2)))

                # Heartbeat every 5 seconds
                _heartbeat_counter += 1
                if _heartbeat_counter >= 5:
                    _heartbeat_counter = 0
                    net_pnl = self._realized_pnl + self._unrealized_pnl
                    logger.info(
                        f"[EXECUTIVE] Heartbeat — Net PnL: {net_pnl:,.0f} IDR "
                        f"(realized={self._realized_pnl:,.0f}, "
                        f"unrealized={self._unrealized_pnl:,.0f})"
                    )

            except Exception as exc:
                logger.error(f"[EXECUTIVE] Equity broadcast error: {exc}")

            await asyncio.sleep(1.0)

    #  Order execution 

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
            pair = str(order.get("symbol") or "").lower().replace("/", "")
            side = str(order.get("side") or "").lower()
            order_type = str(order.get("type") or "limit").lower()
            volume = float(order.get("volume") or 0.0)
            price = order.get("price")

            if not pair or side not in {"buy", "sell"} or volume <= 0:
                logger.warning(f"[executive-core] Invalid order payload skipped: {order}")
                return

            ccxt_symbol = pair.upper().replace("IDR", "/IDR")

            loop = asyncio.get_event_loop()
            if hasattr(self._exchange, "place_order"):
                result = await loop.run_in_executor(
                    None,
                    lambda: self._exchange.place_order(
                        symbol=pair,
                        side=side,
                        order_type=order_type,
                        volume=volume,
                        price=price,
                    ),
                )
            else:
                pair_param = pair[:-3] + "_idr" if pair.endswith("idr") else pair
                result = await loop.run_in_executor(
                    None,
                    lambda: self._exchange.create_order(
                        ccxt_symbol,
                        order_type,
                        side,
                        volume,
                        None if order_type == "market" else price,
                        {"pair": pair_param},
                    ),
                )

            order_id = str(result.get("order_id") or result.get("id") or f"EXEC-{int(time.time())}")
            exec_price = float(result.get("price") or price or 0.0)
            status = str(result.get("status") or "submitted")

            logger.info(
                f"[executive-core] Order placed: {side.upper()} "
                f"{pair} → order_id={order_id}"
            )
            # Persist to DB
            db.upsert_order_book(
                order_id=order_id,
                pair=pair,
                side=side.upper(),
                price=exec_price,
                amount=volume,
                status=status,
                raw_response=result,
            )
        except Exception as exc:
            logger.error(f"[executive-core] Order execution failed: {exc}")
            send_telegram_message(f"[WARN] Order error: {exc}")

    #  Guard callbacks 

    def _on_basket_exit(self, reason: str) -> None:
        """Triggered by NetZeroGuard — queue sell orders for all open positions."""
        logger.warning(f"[executive-core] BASKET_EXIT triggered: {reason}")
        send_telegram_message(f"[CRITICAL] BASKET EXIT ({reason}) — closing all positions")
        self._queue_close_all(reason)

    def _on_profit_lock_exit(self, reason: str, floor: float) -> None:
        """Triggered by ProfitLock — queue sell orders to lock profits."""
        logger.warning(
            f"[executive-core] PROFIT LOCK EXIT: {reason} | floor={floor:,.0f} IDR"
        )
        send_telegram_message(
            f" PROFIT LOCK EXIT ({reason}) — floor={floor:,.0f} IDR"
        )
        self._queue_close_all(reason)

    def _queue_close_all(self, reason: str) -> None:
        """Enqueue sell orders for every open position."""
        try:
            positions = db.get_open_positions()
            positions_iter = positions.values() if isinstance(positions, dict) else positions
            current_dr_val = 1 if db.is_dry_run() else 0
            for pos in positions_iter:
                if int(pos.get("dry_run", 0)) != current_dr_val:
                    continue
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


#  Entry point 

def main() -> None:
    logger.info("=" * 60)
    logger.info("  AlgoVibe Executive-Core Service starting…")
    logger.info("=" * 60)

        # [Container Fix] Disable PID lock in Docker — PID 1 always alive inside container
        # pid_lock = PidLock()
        # pid_lock.acquire()

    # Explicitly run migrations before starting service
    db.run_migrations()

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
            # pid_lock.release()
        logger.info("[executive-core] Exited cleanly.")


if __name__ == "__main__":
    main()
