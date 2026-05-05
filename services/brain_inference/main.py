"""
services/brain_inference/main.py — Brain-Inference Microservice Entry Point
===========================================================================
Long-running Oracle inference worker for the microservice stack.
Now restored with full AI Analysis, Technical Scoring, and DB persistence.

Responsibilities:
  - Subscribe to market ticks from ipc:///tmp/market_stream.ipc
  - Maintain real-time price history and ticker buffers
  - Run the Oracle insight loop (AI Analysis)
  - Calculate Technical Scores (RSI, Trend, Score) and save to DB
  - Publish signals to ipc:///tmp/signal_stream.ipc

Run:
    python -m services.brain_inference.main
"""

import asyncio
import logging
import os
import signal
import sys
import time
import json
import threading
from datetime import datetime, timedelta
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import ccxt

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.transport import ZMQPublisher, ZMQSubscriber, IPC_MARKET, IPC_SIGNAL, install_uvloop
from shared.algovibe_pb2 import Tick, Signal, SignalAction, SignalSource  # type: ignore[attr-defined]

# Bot domain/infrastructure imports
from app.application.oracle import Oracle
from app.infrastructure.database import db
from app.domain.scoring import calculate_score
from app.domain.technicals import get_technical_summary, derive_trade_signal
from app.application.use_cases.candidate_enrichment import enrich_pair
from app.application.use_cases.entry_gates import check_entry_gates
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("brain-inference")

install_uvloop()

class ZMQMarketProxy:
    """Mock MarketDataWS but powered by ZMQ ticks."""
    def __init__(self, tickers, history):
        self.tickers = tickers
        self.history = history

    def get_tickers(self):
        return self.tickers

    def get_trades(self, pair):
        # We don't have historical trades in ZMQ ticks, return empty
        return []

    def get_price_history(self, pair):
        return list(self.history.get(pair, []))

class BrainInferenceService:
    """Restored Inference loop with AI Analytics and Technical Scoring."""

    def __init__(self):
        self._tick_sub = ZMQSubscriber(IPC_MARKET)
        self._signal_pub = ZMQPublisher(IPC_SIGNAL)
        self._running = False
        self._last_heartbeat = 0.0
        
        # Buffers for realtime analysis
        self._tickers = {}  # {symbol: {last, bid, ask, high, low, vol_idr, change_24h}}
        self._history = {}  # {symbol: deque([prices], maxlen=200)}
        # Monolith parity: keep exactly the same rolling history depth.
        self._history_maxlen = 100
        self._pre_load_history()
        
        # [Restoration] Exchange for backfill
        from app.infrastructure.exchange_factory import create_indodax_exchange
        self._exchange = create_indodax_exchange()
        self._backfill_thread = None
        
        # Oracle initialization
        self.market_proxy = ZMQMarketProxy(self._tickers, self._history)
        self.oracle = Oracle(self.market_proxy)
        
        # Adaptive config for scoring
        self._adaptive_cfg = {
            'max_spread': config.MAX_ALLOWED_SPREAD_PCT,
            'min_vol': config.MIN_VOLUME_FILTER,
            'tech_threshold': config.TECH_CONFIDENCE_THRESHOLD,
            'min_price': config.MIN_PRICE_FILTER,
        }
        
        # Load blacklist
        self.blacklist = set(db.get_blacklist())
        self.daily_trades = int(db.get_state("daily_trades", 0) or 0)
        self.start_day = int(db.get_state("start_day", datetime.now().day) or datetime.now().day)

    async def start(self) -> None:
        self._running = True
        self._tick_sub.start()
        self._signal_pub.start()
        self._stop_event = asyncio.Event()

        # Start Backfill in background
        self._backfill_thread = threading.Thread(target=self._backfill_historical_data, daemon=True)
        self._backfill_thread.start()

        logger.info("[brain-inference] ✅ Started — subscribing to ticks.")
        
        # Start the analysis loop in a separate thread to not block tick consumption
        self._analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self._analysis_thread.start()
        
        await asyncio.gather(
            self._consume_ticks(),
            self._stop_event.wait(),
        )

    def stop(self) -> None:
        self._running = False
        self._tick_sub.stop()
        self._signal_pub.stop()
        if hasattr(self, "_stop_event"):
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass
        logger.info("[brain-inference] Stopped.")

    async def _consume_ticks(self) -> None:
        """Consume market ticks and update internal buffers."""
        async for raw in self._tick_sub.aiter_messages():
            if not self._running:
                break

            try:
                tick = Tick()
                tick.ParseFromString(raw)
                
                symbol = tick.symbol.lower()
                price = float(tick.price)
                
                # Update Tickers
                self._tickers[symbol] = {
                    'pair': symbol,
                    'last': price,
                    'bid': float(tick.bid) if tick.bid else price,
                    'ask': float(tick.ask) if tick.ask else price,
                    'high': float(tick.high_24h) if tick.high_24h else price,
                    'low': float(tick.low_24h) if tick.low_24h else price,
                    'vol_idr': float(tick.vol_idr) if tick.vol_idr else 0,
                    'change_24h': 0.0, # Not in current proto definition
                }
                
                # Update History
                if symbol not in self._history:
                    self._history[symbol] = deque(maxlen=self._history_maxlen)
                
            except Exception as exc:
                logger.warning("[brain-inference] Tick processing error: %s", exc)
                continue

    def _pre_load_history(self):
        """Pre-load history from DB on startup."""
        logger.info("[brain-inference] Pre-loading history from DB...")
        try:
            with db.get_connection() as conn:
                rows = conn.execute("SELECT pair, history_json FROM ticker_history").fetchall()
                for row in rows:
                    pair = row['pair'].lower()
                    if row['history_json']:
                        prices = json.loads(row['history_json'])
                        self._history[pair] = deque(prices, maxlen=self._history_maxlen)
            logger.info("[brain-inference] Pre-loaded %d pairs.", len(self._history))
        except Exception as e:
            logger.error("[brain-inference] Pre-load error: %s", e)

    def _analysis_loop(self):
        """Background thread for high-level market analysis and AI insights."""
        logger.info("[brain-inference] Analysis loop started.")
        last_flush = 0
        
        while self._running:
            try:
                time.sleep(15)  # Run analysis every 15 seconds
                
                if not self._tickers:
                    continue
                
                # 1. Update Oracle state
                regime = self.oracle.get_market_regime()
                self._adaptive_cfg = self.oracle.get_adaptive_config()
                
                # 2. Rank pairs
                ranked = []
                for symbol, data in self._tickers.items():
                    res = calculate_score(((symbol, data), self._adaptive_cfg, regime))
                    if res:
                        ranked.append(res)
                
                ranked.sort(key=lambda x: x['score'], reverse=True)
                
                # 3. AI Insights
                # Request periodic analysis for top movers
                self.oracle.request_periodic_analysis(ranked)
                
                # Detect fake pumps
                for r in ranked[:10]:
                    pair = r['pair']
                    change = r['change']
                    self.oracle.detect_fake_pump(pair, change)
                    if abs(change) > 3.0:
                        self.oracle.request_pump_analysis(pair, change)
                
                # Process LLM Insights (saves to system_events table)
                self.oracle.process_llm_insights()
                
                # 3.5 [RESTORATION] Scan for trade signals (Entry Logic)
                self._scan_for_signals(ranked)
                
                # 4. Technical Scoring and DB Update (Radar data)
                self._update_ticker_history(ranked)
                
                # Heartbeat to DB
                db.save_state("brain_heartbeat", time.time())
                
            except Exception as e:
                logger.error("[brain-inference] Error in analysis loop: %s", e)

    def _backfill_historical_data(self):
        """Restore full 1h history for all IDR pairs (Smart Backfill)."""
        logger.info("[brain-inference] ⏳ Starting Smart Backfill (1h window)...")
        db.save_state("backfill_status", "SYNCING")

        try:
            markets = self._exchange.fetch_markets()
            full_idr_list = [m['symbol'] for m in markets if m['quote'] == 'IDR' and m['active']]
            
            idr_pairs = []
            for symbol in full_idr_list:
                pair_id = symbol.replace('/', '').lower()
                if len(self._history.get(pair_id, [])) < 50:
                    idr_pairs.append(symbol)
            
            total_pairs = len(idr_pairs)
            if not idr_pairs:
                logger.info("[brain-inference] ✅ All pairs already warm. Skipping backfill.")
                db.save_state("backfill_status", "COMPLETED")
                return

            logger.info(f"[brain-inference] 📦 Syncing {total_pairs} pairs...")

            def fetch_one(symbol):
                try:
                    ohlcv = self._exchange.fetch_ohlcv(symbol, timeframe='1m', limit=60)
                    if not ohlcv: return None
                    return (symbol.replace('/', '').lower(), [c[4] for c in ohlcv])
                except Exception: return None

            batch_size = 20
            all_updates = []
            with ThreadPoolExecutor(max_workers=5) as pool:
                for i in range(0, total_pairs, batch_size):
                    batch = idr_pairs[i:i + batch_size]
                    results = list(pool.map(fetch_one, batch))
                    
                    for res in results:
                        if res:
                            pair_id, prices = res
                            if pair_id not in self._history:
                                self._history[pair_id] = deque(maxlen=self._history_maxlen)
                            self._history[pair_id].extend(prices)
                            
                            # Prepare for DB update
                            last = prices[-1]
                            ch_5m = ((last - prices[-5]) / prices[-5] * 100) if len(prices) >= 5 else 0
                            ch_15m = ((last - prices[-15]) / prices[-15] * 100) if len(prices) >= 15 else 0
                            ch_1h = ((last - prices[0]) / prices[0] * 100) if len(prices) >= 60 else 0
                            
                            all_updates.append((pair_id, list(self._history[pair_id]), ch_5m, ch_15m, ch_1h))
                    
                    time.sleep(0.1)

            if all_updates:
                db.save_ticker_history_batch(all_updates)
            
            logger.info(f"[brain-inference] ✅ Backfill completed for {len(all_updates)} pairs.")
            db.save_state("backfill_status", "COMPLETED")
            
        except Exception as e:
            logger.error(f"[brain-inference] ⚠️ Backfill failed: {e}")
            db.save_state("backfill_status", "FAILED")

    def _scan_for_signals(self, ranked: list):
        """Evaluate ranked candidates for BUY signals and emit to Executive-Core."""
        if not ranked:
            return

        # Monolith parity: reset daily counters on calendar day rollover.
        current_day = datetime.now().day
        if current_day != self.start_day:
            self.daily_trades = 0
            self.start_day = current_day
            db.save_state("daily_trades", 0)
            db.save_state("start_day", current_day)
            db.save_state("session_mode", "NORMAL")
            db.save_state("session_buy_enabled", "true")
            db.save_state("session_latched", "false")

        regime = self.oracle.get_market_regime()
        open_positions = db.get_open_positions()

        # Monolith parity: run target/loss gates before evaluating entries.
        adaptive_cfg = dict(self._adaptive_cfg)
        target_status = self.oracle.get_daily_target_status()
        session_info = self.oracle.evaluate_and_update_session_mode(target_status)
        if not session_info.get("buy_enabled", True):
            return

        if target_status.get("near"):
            original_threshold = adaptive_cfg.get("tech_threshold", config.TECH_CONFIDENCE_THRESHOLD)
            adaptive_cfg["tech_threshold"] = min(0.85, original_threshold + 0.15)

        recovery_mode = False
        recovery_new_positions = 0
        if target_status.get("loss_limit_hit"):
            adaptive_cfg["tech_threshold"] = 0.80
            recovery_mode = True
        
        # Mocking bot-like interface for entry_gates use case
        class BotMock:
            def __init__(self, oracle, blacklist, history, history_maxlen):
                self.oracle = oracle
                self.blacklist = blacklist
                self.price_history = history # Shared reference to self._history
                self._history_maxlen = history_maxlen

            def update_history_and_trend(self, pair, price):
                history_deque = self.price_history.get(pair)
                if history_deque is None:
                    history_deque = deque(maxlen=self._history_maxlen)
                    self.price_history[pair] = history_deque
                # Monolith parity: append one sample per evaluation cycle.
                history_deque.append(price)
                history = list(history_deque)
                if not history:
                    return "SIDEWAYS", False, []
                
                avg_20 = sum(history[-20:]) / 20 if len(history) >= 20 else history[0]
                avg_50 = sum(history[-50:]) / 50 if len(history) >= 50 else avg_20

                is_uptrend = avg_20 > avg_50 * 1.001
                is_pullback = len(history) >= 50 and is_uptrend and price <= avg_20 * 1.002

                return "UP" if is_uptrend else "SIDEWAYS", is_pullback, history

        bot_mock = BotMock(self.oracle, self.blacklist, self._history, self._history_maxlen)
        
        for r in ranked[:config.MAX_TRACKED_PAIRS]:
            pair = r['pair']
            try:
                if self.daily_trades >= config.MAX_TRADES_PER_DAY:
                    break

                # Prepare mock for enrichment/gates
                bot_mock.price_history[pair] = self._history.get(pair, deque())
                
                enriched = enrich_pair(bot_mock, r, self._tickers)
                gate = check_entry_gates(bot_mock, r, enriched, regime, adaptive_cfg, open_positions)

                if recovery_mode:
                    total_conf = float((gate or {}).get("total_confidence", 0)) if isinstance(gate, dict) else 0.0
                    if total_conf < 75.0:
                        continue
                    if recovery_new_positions >= 1:
                        continue
                
                if isinstance(gate, dict) and gate.get("mode") == "LIVE":
                    gate_conf = float(gate.get('total_confidence', 0) or 0.0)
                    tq_prob = float(enriched.get('tq_prob', 0.0) or 0.0)
                    logger.info(
                        f"[brain-inference] 🔥 SIGNAL DETECTED: {pair} "
                        f"(gate={gate_conf:.1f}%, tq_prob={tq_prob * 100:.1f}%)"
                    )
                    
                    # Emit Signal via ZMQ
                    sig = Signal()
                    sig.symbol = pair
                    sig.action = SignalAction.Value("ACTION_BUY")
                    # Monolith parity: execute_trade consumes technical probability (0..1).
                    sig.confidence = tq_prob
                    sig.source = SignalSource.Value("SOURCE_ORACLE")
                    sig.timestamp = int(time.time() * 1000)
                    
                    # Use "signal" topic for ZMQ multipart
                    self._signal_pub.send(sig.SerializeToString(), topic="signal")
                    self.daily_trades += 1
                    db.save_state("daily_trades", self.daily_trades)
                    if recovery_mode:
                        recovery_new_positions += 1
                    
            except Exception as e:
                logger.error(f"[brain-inference] Signal scan error for {pair}: {e}")

    def _update_ticker_history(self, ranked):
        """Calculate technicals for top pairs and update the ticker_history table."""
        updates = []
        # Process top 50 pairs for the radar
        for r in ranked[:50]:
            pair = r['pair'].lower()
            hist = list(self._history.get(pair, []))
            if len(hist) < 5:
                continue
            
            try:
                tech = get_technical_summary(hist)
                signal_info = derive_trade_signal(tech)
                
                # Prepare update tuple for database.save_ticker_history_batch
                # Expected: (pair, history_list, ch_5m, ch_15m, ch_1h)
                # Wait, our database method is limited. Let's see if we should enhance it.
                # Actually, I'll use a direct query to save everything.
                
                updates.append({
                    'pair': pair,
                    'history_json': json.dumps(hist),
                    'prediction_prob': 0.0, # Will be updated by LLM if needed
                    'trend': tech['trend'],
                    'signal': signal_info['verdict'],
                    'ch_5m': tech['ch_5m'],
                    'ch_15m': tech['ch_15m'],
                    'ch_1h': tech['ch_1h'],
                    'volatility': r['volatility'],
                    'score': r['score']
                })
            except Exception as e:
                logger.debug("Failed tech summary for %s: %s", pair, e)
        
        if updates:
            self._save_to_db(updates)

    def _save_to_db(self, updates):
        """Directly update ticker_history with full analytics."""
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                for up in updates:
                    cursor.execute('''
                        INSERT INTO ticker_history (pair, history_json, prediction_prob, trend, signal,
                                                  ch_5m, ch_15m, ch_1h, volatility, score, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(pair) DO UPDATE SET
                            history_json=excluded.history_json,
                            prediction_prob=excluded.prediction_prob,
                            trend=excluded.trend,
                            signal=excluded.signal,
                            ch_5m=excluded.ch_5m,
                            ch_15m=excluded.ch_15m,
                            ch_1h=excluded.ch_1h,
                            volatility=excluded.volatility,
                            score=excluded.score,
                            updated_at=excluded.updated_at
                    ''', (
                        up['pair'], up['history_json'], up['prediction_prob'], up['trend'],
                        up['signal'], up['ch_5m'], up['ch_15m'], up['ch_1h'],
                        up['volatility'], up['score']
                    ))
        except Exception as e:
            logger.error("[brain-inference] DB Update Error: %s", e)

def main() -> None:
    service = BrainInferenceService()

    # Ensure we have an event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def _shutdown(signum, _frame):
        logger.info("[brain-inference] Signal %s received — shutting down.", signum)
        service.stop()
        # We don't stop the loop immediately, let service.stop() do its thing
        # but we set a timeout just in case
        loop.call_later(2, loop.stop)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        loop.close()

if __name__ == "__main__":
    main()
