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

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.transport import ZMQPublisher, ZMQSubscriber, IPC_MARKET, IPC_SIGNAL, install_uvloop
from shared.algovibe_pb2 import Tick, Signal, SignalAction, SignalSource  # type: ignore[attr-defined]

# Bot domain/infrastructure imports
from app.application.oracle import Oracle
from app.infrastructure.database import db, db_backtest
from app.domain.scoring import calculate_score
from app.domain.technicals import get_technical_summary, derive_trade_signal, compute_adaptive_thresholds
from app.application.use_cases.candidate_enrichment import enrich_pair
from app.application.use_cases.entry_gates import check_entry_gates
from app.application.session_awareness import compute_session_awareness
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
        # ZMQ Tick stream does not carry per-trade records — the Trade proto
        # events are published but brain-inference has no ZMQSubscriber for
        # IPC_TRADE yet.  Oracle callers all guard with `or []`, so returning
        # an empty list degrades VWAP/flow-imbalance quality but does not crash.
        # TODO: add a ZMQSubscriber(IPC_TRADE) and a rolling trade cache here
        #       once the Trade IPC stream is wired in market-pipe.
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
        
        # Adapter for public REST API backfill (no API key needed)
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            from app.infrastructure.providers.binance_adapter import BinanceAdapter
            _api_key = os.getenv("BINANCE_API_KEY", "")
            _api_secret = os.getenv("BINANCE_API_SECRET", "")
            self._adapter = BinanceAdapter(_api_key, _api_secret)
        else:
            from app.infrastructure.providers.indodax_adapter import IndodaxAdapter
            _api_key = os.getenv("INDODAX_API_KEY", "")
            _api_secret = os.getenv("INDODAX_API_SECRET", "")
            self._adapter = IndodaxAdapter(_api_key, _api_secret)
        self._backfill_thread = None

        # OHLC H/L/Volume cache untuk ATR akurat + OBV/Volume Ratio aktif
        self._ohlc_highs: dict = {}  # {pair: deque([highs])}
        self._ohlc_lows:  dict = {}  # {pair: deque([lows])}
        self._ohlc_vols:  dict = {}  # {pair: deque([volumes])} — untuk OBV & Volume Ratio

        # Market movers (top gainer/loser/trending) — di-refresh tiap 5 menit
        self._market_movers: dict = {}
        self._movers_last_refresh: float = 0.0
        
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
        # Live 1m OHLCV candle builder
        # _candles: running (open) candle per pair
        #   {pair: {ts, open, high, low, close, vol_start, vol_cur}}
        self._candles: dict = {}
        self._candle_buf: list = []          # completed candles waiting flush
        self._candle_buf_lock = threading.Lock()
        self._candle_last_flush: float = 0.0

        # Cache per-pair untuk compute_adaptive_thresholds (TTL 5 menit).
        # Menghindari O(n²) recalculation tiap tick untuk 200+ pair.
        self._adaptive_cache: dict = {}  # {pair: (timestamp, thresholds|None)}
        # Cache trend filter 15m (TTL 2 menit) agar tidak membanjiri API.
        self._trend15_cache: dict = {}  # {pair: (timestamp, passed, detail)}
        self._trend15_stats = {
            "window_start": time.time(),
            "checked": 0,
            "passed": 0,
            "blocked": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        self._running = True
        self._tick_sub.start()
        self._signal_pub.start()
        self._stop_event = asyncio.Event()

        # Start Backfill in background
        self._backfill_thread = threading.Thread(target=self._backfill_historical_data, daemon=True)
        self._backfill_thread.start()

        logger.info("[brain-inference] [OK] Started — subscribing to ticks.")
        
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
                
                # Update rolling price history (was created but never appended — bug fix)
                if symbol not in self._history:
                    self._history[symbol] = deque(maxlen=self._history_maxlen)
                self._history[symbol].append(price)

                # Live 1m candle builder — gunakan base-asset 24h volume (tick.volume)
                # agar delta per-menit menghasilkan volume dalam satuan base-asset (e.g. TRX)
                # yang konsisten dengan data historis dari Binance klines API.
                _vol_base = float(tick.volume) if tick.volume else 0.0
                self._update_live_candle(symbol, price, _vol_base)

                # Sinkronisasi OHLC H/L cache agar panjang == hist (proxy ATR).
                # Volume TIDAK diisi dari WS tick karena nilai per-candle tidak tersedia;
                # mengisi dengan 0.0 akan mendilusi data volume asli dari backfill OHLC API.
                # Volume deque hanya diisi oleh _backfill_historical_data().
                if symbol in self._ohlc_highs:
                    self._ohlc_highs[symbol].append(float(tick.high_24h) if tick.high_24h else price)
                if symbol in self._ohlc_lows:
                    self._ohlc_lows[symbol].append(float(tick.low_24h) if tick.low_24h else price)

                # Derive change_24h dari rolling price history WS.
                # Gunakan nilai dari summaries API jika history WS masih pendek (<60 candle),
                # karena summaries menggunakan harga 24 jam lalu yang akurat.
                hist = self._history[symbol]
                if len(hist) >= 60:
                    oldest = float(hist[0] or price)
                    change_24h = ((price - oldest) / oldest * 100.0) if oldest > 0 else 0.0
                else:
                    # Pertahankan nilai dari summaries (di-set oleh _refresh_market_movers) jika ada
                    existing = self._tickers.get(symbol, {})
                    change_24h = existing.get("change_24h", 0.0)

                # Update Tickers
                self._tickers[symbol] = {
                    'pair': symbol,
                    'last': price,
                    'bid': float(tick.bid) if tick.bid else price,
                    'ask': float(tick.ask) if tick.ask else price,
                    'high': float(tick.high_24h) if tick.high_24h else price,
                    'low': float(tick.low_24h) if tick.low_24h else price,
                    'vol_idr': float(tick.vol_idr) if tick.vol_idr else 0,
                    'change_24h': change_24h,
                }
                
            except Exception as exc:
                logger.warning("[brain-inference] Tick processing error: %s", exc)
                continue

    def _update_live_candle(self, pair: str, price: float, vol_base: float) -> None:
        """Akumulasi tick ke candle 1m. Saat menit berganti, candle selesai
        dimasukkan ke buffer untuk di-flush ke ohlcv_1m.
        vol_base = 24h base-asset rolling volume dari tick.volume.
        Delta per menit ≈ volume base-asset pada candle tersebut."""
        ts_min = int(time.time()) // 60 * 60  # truncate ke awal menit
        existing = self._candles.get(pair)
        if existing is None:
            # Mulai candle pertama untuk pair ini
            self._candles[pair] = {
                "ts": ts_min, "open": price, "high": price,
                "low": price, "close": price,
                "vol_start": vol_base, "vol_cur": vol_base,
            }
            return
        if existing["ts"] == ts_min:
            # Masih menit yang sama — update running candle
            if price > existing["high"]:
                existing["high"] = price
            if price < existing["low"]:
                existing["low"] = price
            existing["close"] = price
            existing["vol_cur"] = vol_base
        else:
            # Menit berganti — selesaikan candle lama, mulai yang baru
            vol_delta = max(0.0, existing["vol_cur"] - existing["vol_start"])
            completed = {
                "ts":     existing["ts"],
                "pair":   pair,
                "open":   existing["open"],
                "high":   existing["high"],
                "low":    existing["low"],
                "close":  existing["close"],
                "volume": vol_delta,
            }
            with self._candle_buf_lock:
                self._candle_buf.append(completed)
            self._candles[pair] = {
                "ts": ts_min, "open": price, "high": price,
                "low": price, "close": price,
                "vol_start": vol_base, "vol_cur": vol_base,
            }

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
                time.sleep(1)  # Run analysis every 1 second
                
                if not self._tickers:
                    continue
                
                # 1. Update Oracle state
                regime = self.oracle.get_market_regime()
                self._adaptive_cfg = self.oracle.get_adaptive_config()
                
                # 2. Rank pairs
                ranked = []
                # Use list() to avoid "dictionary changed size during iteration" from concurrent tick updates
                for symbol, data in list(self._tickers.items()):
                    res = calculate_score(((symbol, data), self._adaptive_cfg, regime, self._history))
                    if res:
                        ranked.append(res)
                
                ranked.sort(key=lambda x: x['score'], reverse=True)

                # 2.5 Refresh market movers (top gainer/loser/trending) setiap 5 menit
                self._refresh_market_movers()

                # 3. AI Insights
                # Request periodic analysis for top movers
                self.oracle.request_periodic_analysis(ranked)
                
                # Detect fake pumps
                for r in ranked[:30]:
                    pair = r['pair']
                    change = r['change']
                    self.oracle.detect_fake_pump(pair, change)
                    if abs(change) > 3.0:
                        self.oracle.request_pump_analysis(pair, change)
                
                # 3.5 [RESTORATION] Scan for trade signals (Entry Logic)
                self._scan_for_signals(ranked)

                # 4. Technical Scoring and DB Update (Radar data)
                self._update_ticker_history(ranked)
                
                # 5. AI Insights (DISABLED in brain-inference for stability/latency)
                # litert-lm-decision service handles this now.
                pass
                
                # Flush completed 1m candles ke ohlcv_1m
                _now = time.time()
                if _now - self._candle_last_flush >= 60:
                    with self._candle_buf_lock:
                        _to_flush = self._candle_buf[:]
                        self._candle_buf.clear()
                    if _to_flush:
                        db_backtest.save_ohlcv_1m(_to_flush)
                        logger.info("[OHLCV] Flushed %d 1m candles to backtest DB.", len(_to_flush))
                    self._candle_last_flush = _now

                # Heartbeat to DB
                db.save_state("brain_heartbeat", time.time())
                
            except Exception as e:
                logger.error("[brain-inference] Error in analysis loop: %s", e)

    def _backfill_historical_data(self):
        """Restore 1h price history for all IDR pairs via Indodax public OHLC API."""
        logger.info("[brain-inference]  Starting Smart Backfill (native OHLC API)...")
        db.save_state("backfill_status", "SYNCING")

        try:
            all_idr = self._adapter.fetch_idr_pairs()
            idr_pairs = [
                p for p in all_idr
                if len(self._history.get(p, [])) < 50
            ]

            total_pairs = len(idr_pairs)
            if not idr_pairs:
                logger.info("[brain-inference] [OK] All pairs already warm. Skipping backfill.")
                db.save_state("backfill_status", "COMPLETED")
                return

            logger.info("[brain-inference]  Syncing %d pairs via OHLC API...", total_pairs)

            def fetch_one(pair_id):
                try:
                    # Ambil full OHLC (bukan hanya closes) agar H/L tersedia untuk ATR akurat
                    candles = self._adapter.fetch_ohlc(pair_id, tf=config.BACKFILL_TF_MIN, limit=100)
                    if not candles:
                        return None
                    closes = [c["close"]  for c in candles if c["close"] > 0]
                    highs  = [c["high"]   for c in candles if c["close"] > 0]
                    lows   = [c["low"]    for c in candles if c["close"] > 0]
                    vols   = [c["volume"] for c in candles if c["close"] > 0]
                    if not closes:
                        return None
                    return (pair_id, closes, highs, lows, vols)
                except Exception:
                    return None

            batch_size = 20
            all_updates = []
            with ThreadPoolExecutor(max_workers=5) as pool:
                for i in range(0, total_pairs, batch_size):
                    batch = idr_pairs[i:i + batch_size]
                    results = list(pool.map(fetch_one, batch))

                    for res in results:
                        if not res:
                            continue
                        pair_id, prices, highs, lows, vols = res
                        if pair_id not in self._history:
                            self._history[pair_id] = deque(maxlen=self._history_maxlen)
                        self._history[pair_id].extend(prices)

                        # Simpan H/L dan Volume untuk ATR + OBV/Volume Ratio aktif
                        if pair_id not in self._ohlc_highs:
                            self._ohlc_highs[pair_id] = deque(maxlen=self._history_maxlen)
                            self._ohlc_lows[pair_id]  = deque(maxlen=self._history_maxlen)
                            self._ohlc_vols[pair_id]  = deque(maxlen=self._history_maxlen)
                        self._ohlc_highs[pair_id].extend(highs)
                        self._ohlc_lows[pair_id].extend(lows)
                        self._ohlc_vols[pair_id].extend(vols)

                        last = prices[-1]
                        ch_5m  = ((last - prices[-5])  / prices[-5]  * 100) if len(prices) >= 5  else 0
                        ch_15m = ((last - prices[-15]) / prices[-15] * 100) if len(prices) >= 15 else 0
                        ch_1h  = ((last - prices[0])   / prices[0]   * 100) if len(prices) >= 60 else 0
                        all_updates.append((pair_id, list(self._history[pair_id]), ch_5m, ch_15m, ch_1h))

                    time.sleep(0.1)  # stay within public rate limit (180 req/min)

            if all_updates:
                db.save_ticker_history_batch(all_updates)

            logger.info("[brain-inference] [OK] Backfill completed for %d pairs.", len(all_updates))
            db.save_state("backfill_status", "COMPLETED")

        except Exception as e:
            logger.error("[brain-inference] [WARN] Backfill failed: %s", e)
            db.save_state("backfill_status", "FAILED")

    def _refresh_market_movers(self):
        """Refresh top gainer/loser/trending dari Indodax API setiap 5 menit.

        Memperluas scan universe dengan pair yang belum masuk via WS tapi sudah
        menunjukkan pergerakan harga signifikan (naik/turun/volume tinggi).
        """
        now = time.time()
        if now - self._movers_last_refresh < 300:
            return
        try:
            movers = self._adapter.fetch_market_movers(top_n=30)
            if not movers:
                return
            self._market_movers = movers
            self._movers_last_refresh = now

            # Seed _tickers untuk pair mover yang belum diterima via WS
            all_mover_pairs = {
                m["pair"]: m
                for cat in ("gainers", "losers", "trending", "momentum_7d")
                for m in movers.get(cat, [])
            }
            seeded = 0
            for pair_norm, m in all_mover_pairs.items():
                if pair_norm not in self._tickers and m["last"] > 0:
                    self._tickers[pair_norm] = {
                        "pair":       pair_norm,
                        "last":       m["last"],
                        "bid":        m["last"],
                        "ask":        m["last"],
                        "high":       m["last"],
                        "low":        m["last"],
                        "vol_idr":    m["vol_idr"],
                        "change_24h": m["change_24h"],
                    }
                    seeded += 1
                elif pair_norm in self._tickers:
                    # Update change_24h dengan nilai akurat dari summaries
                    self._tickers[pair_norm]["change_24h"] = m["change_24h"]

            logger.info("[BRAIN] Top Gainers 24h: %s", movers.get('gainers_brief'))
            logger.info("[BRAIN] Top Losers  24h: %s", movers.get('losers_brief'))
            logger.info("[BRAIN] Trending (vol): %s", movers.get('trending_brief'))
            if seeded > 0:
                logger.info("[BRAIN] Seeded %d mover pair ke scan universe.", seeded)
        except Exception as e:
            logger.warning("[BRAIN] fetch_market_movers gagal: %s", e)

    @staticmethod
    def _ema(values, period):
        if not values:
            return []
        period = max(1, int(period))
        alpha = 2.0 / (period + 1.0)
        out = [float(values[0])]
        for v in values[1:]:
            out.append((float(v) * alpha) + (out[-1] * (1.0 - alpha)))
        return out

    def _passes_15m_trend_filter(self, pair: str):
        now = time.time()
        cached = self._trend15_cache.get(pair)
        if cached and (now - cached[0]) < 120:
            return cached[1], cached[2]

        passed = False
        detail = "insufficient_data"
        try:
            # Get velocity from latest tickers if available
            ticker = self._tickers.get(pair, {})
            last_price = float(ticker.get('last', 0))
            
            # Use technicals to get velocity for bypass
            hist = list(self._history.get(pair, []))
            velocity = 0.0
            if len(hist) >= 5:
                # Simple velocity calculation: (now - prev) / prev * 100
                prev = float(hist[-2])
                velocity = (last_price - prev) / prev * 100.0 if prev > 0 else 0.0

            # Momentum Bypass: If price is accelerating fast, skip strict trend checks
            if velocity > 0.25:
                self._trend15_cache[pair] = (now, True, f"momentum_bypass(vel={velocity:.2f}%)")
                return True, f"momentum_bypass(vel={velocity:.2f}%)"

            candles = self._adapter.fetch_ohlc(pair, tf=config.TREND_FILTER_TF_MIN, limit=60)
            closes = [c["close"] for c in candles if float(c.get("close") or 0) > 0]

            if len(closes) >= 50:
                ema50 = self._ema(closes, 50)
                ema50_now = ema50[-1]
                ema50_prev = ema50[-2] if len(ema50) > 1 else ema50_now
                last = float(closes[-1])
                ch_15m = ((last - float(closes[-2])) / float(closes[-2]) * 100.0) if len(closes) >= 2 and closes[-2] > 0 else 0.0

                is_above_ema = last >= (ema50_now * 0.998) # 0.2% tolerance
                is_ema_rising = ema50_now >= (ema50_prev * 0.999) # Allow flat EMA
                is_change_ok = ch_15m >= (config.TREND_FILTER_MIN_CHANGE_PCT - 0.1)

                passed = bool(is_above_ema and is_ema_rising and is_change_ok)
                detail = (
                    f"last={last:.8f}, ema50={ema50_now:.8f}, "
                    f"ema_slope={'up' if is_ema_rising else 'down'}, ch15={ch_15m:.2f}%"
                )
        except Exception as exc:
            detail = f"error={exc}"

        self._trend15_cache[pair] = (now, passed, detail)
        return passed, detail

    def _record_trend15_stat(self, pair: str, passed: bool, detail: str):
        now = time.time()
        stats = self._trend15_stats
        stats["checked"] += 1

        if detail.startswith("error="):
            stats["errors"] += 1
            if stats["errors"] <= 3:
                logger.warning("[brain] [WARN] TREND15 ERROR: %s (%s)", pair, detail)

        if passed:
            stats["passed"] += 1
        else:
            stats["blocked"] += 1

        window_sec = max(30, int(getattr(config, "TREND_FILTER_STATS_WINDOW_SEC", 300)))
        elapsed = now - stats["window_start"]
        if elapsed >= window_sec:
            checked = max(1, stats["checked"])
            pass_rate = (stats["passed"] / checked) * 100.0
            block_rate = (stats["blocked"] / checked) * 100.0
            logger.info(
                "[BRAIN] TREND15 STATS %ds | checked=%d pass=%d (%.1f%%) block=%d (%.1f%%) error=%d",
                int(elapsed),
                stats["checked"],
                stats["passed"], pass_rate,
                stats["blocked"], block_rate,
                stats["errors"],
            )
            self._trend15_stats = {
                "window_start": now,
                "checked": 0,
                "passed": 0,
                "blocked": 0,
                "errors": 0,
            }

    def _scan_for_signals(self, ranked: list):
        """Evaluate ranked candidates for BUY signals and emit to Executive-Core."""
        if not ranked:
            return

        regime = self.oracle.get_market_regime()
        open_positions = db.get_open_positions()

        # ── Session Awareness: satu titik evaluasi kondisi bot ────────────
        consecutive_losses = int(db.get_state("consecutive_losses") or 0)
        awareness = compute_session_awareness(self.oracle, regime, consecutive_losses)

        # Monolith parity: run target/loss gates before evaluating entries.
        adaptive_cfg = dict(self._adaptive_cfg)
        target_status = self.oracle.get_daily_target_status()
        session_info = self.oracle.evaluate_and_update_session_mode(target_status)
        if not session_info.get("buy_enabled", True):
            return

        # Inject awareness params ke adaptive_cfg (override param standar)
        adaptive_cfg["tech_threshold"]      = awareness["tech_threshold"]
        adaptive_cfg["buy_score_threshold"] = awareness["buy_score_threshold"]
        adaptive_cfg["focus_top_n"]         = awareness["focus_top_n"]

        # Near-target: naikkan threshold lebih jauh di atas awareness threshold
        if target_status.get("near"):
            adaptive_cfg["tech_threshold"] = min(0.90, awareness["tech_threshold"] + 0.12)

        recovery_mode = awareness["state"] in ("RECOVERY_CRISIS", "RECOVERY_DAILY", "RECOVERY_ALLTIME")
        recovery_new_positions = 0
        _max_new = awareness["max_new_positions"]

        # Mocking bot-like interface for entry_gates use case
        class BotMock:
            def __init__(self, oracle, blacklist, history, history_maxlen, ohlc_highs, ohlc_lows, ohlc_vols):
                self.oracle = oracle
                self.blacklist = blacklist
                self.price_history = history # Shared reference to self._history
                self._history_maxlen = history_maxlen
                self.ohlc_highs = ohlc_highs  # {pair: deque([highs])} — untuk ATR akurat
                self.ohlc_lows  = ohlc_lows   # {pair: deque([lows])}
                self.ohlc_vols  = ohlc_vols   # {pair: deque([volumes])} — untuk OBV aktif

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

        bot_mock = BotMock(self.oracle, self.blacklist, self._history, self._history_maxlen,
                           self._ohlc_highs, self._ohlc_lows, self._ohlc_vols)
        
        for r in ranked[:config.MAX_TRACKED_PAIRS]:
            pair = r['pair']
            try:
                # Prepare mock for enrichment/gates
                bot_mock.price_history[pair] = self._history.get(pair, deque())
                
                enriched = enrich_pair(bot_mock, r, self._tickers)
                gate = check_entry_gates(bot_mock, r, enriched, regime, adaptive_cfg, open_positions)

                if gate == "BREAK":
                    logger.warning(
                        "[DEBUG-BRAIN] %s Gate requested BREAK (consecutive-loss halt). Stopping candidate scan.",
                        pair,
                    )
                    break

                if gate is None:
                    prs = db.get_pair_runtime_state(pair.lower())
                    gate_reason = prs.get('gate_skip_reason') or 'Gate rejected'
                    print(f"[DEBUG-BRAIN] {pair} Gate Rejected: {gate_reason}", flush=True)
                    continue

                print(f"[DEBUG-BRAIN] {pair} Gate Result: {gate}", flush=True)

                if recovery_mode:
                    total_conf = float((gate or {}).get("total_confidence", 0)) if isinstance(gate, dict) else 0.0
                    # Recovery: butuh confidence minimum 75% agar masuk
                    if total_conf < 75.0:
                        continue
                    # Batasi posisi baru sesuai max_new_positions dari awareness
                    if recovery_new_positions >= _max_new:
                        continue
                
                if isinstance(gate, dict) and gate.get("mode") == "LIVE":
                    gate_conf = float(gate.get('total_confidence', 0) or 0.0)
                    tq_prob = float(enriched.get('tq_prob', 0.0) or 0.0)

                    # 15m trend guard: only allow entries when higher-timeframe trend supports it.
                    trend_passed, trend_detail = self._passes_15m_trend_filter(pair)
                    self._record_trend15_stat(pair, trend_passed, trend_detail)
                    if not trend_passed:
                        logger.info("[BRAIN] TREND15 FILTER: %s blocked (%s)", pair, trend_detail)
                        continue

                    # FLOW GUARD (REMOVED) - Max momentum responsiveness

                    logger.info(
                        f"[BRAIN] SIGNAL DETECTED: {pair} "
                        f"(gate={gate_conf:.1f}%, tq_prob={tq_prob * 100:.1f}%)"
                    )
                    
                    # --- [DYNAMIC TARGETING] ---
                    # Calculate market-aware TP/SL targets based on ATR and conviction
                    final_dyn_tp = 0.0
                    final_dyn_sl = 0.0
                    try:
                        from app.application.use_cases.entry_targeting import calculate_entry_target
                        target_result = calculate_entry_target(self, r, enriched, gate, adaptive_cfg)
                        if target_result:
                            final_dyn_tp = target_result.get("final_dyn_tp", 0.0)
                            final_dyn_sl = target_result.get("final_dyn_sl", 0.0)
                    except Exception as e:
                        logger.warning(f"[brain] Targeting error for {pair}: {e}")

                    # Emit Signal via ZMQ
                    sig = Signal()
                    # Pack dynamic targets into symbol: pair|tp|sl
                    # If tp/sl are 0, executive-core will fallback to defaults.
                    if final_dyn_tp > 0:
                        sig.symbol = f"{pair}|{final_dyn_tp:.4f}|{final_dyn_sl:.4f}"
                    else:
                        sig.symbol = pair
                    
                    sig.action = SignalAction.Value("ACTION_BUY")
                    # Monolith parity: execute_trade consumes technical probability (0..1).
                    sig.confidence = tq_prob
                    sig.source = SignalSource.Value("SOURCE_ORACLE")
                    sig.timestamp = int(time.time() * 1000)
                    
                    # Use "signal" topic for ZMQ multipart
                    self._signal_pub.send(sig.SerializeToString(), topic="signal")
                    if recovery_mode:
                        recovery_new_positions += 1
                    
            except Exception as e:
                logger.error(f"[brain-inference] Signal scan error for {pair}: {e}")

    def _update_ticker_history(self, ranked):
        """Calculate technicals for ALL ranked pairs and update the ticker_history table."""
        updates = []
        for r in ranked:
            pair = r['pair'].lower()
            hist = list(self._history.get(pair, []))
            if len(hist) < 5:
                continue

            try:
                _vols_bi = list(self._ohlc_vols.get(pair, []))
                tech = get_technical_summary(hist, _vols_bi if len(_vols_bi) == len(hist) else None)
                _sector_bi = config.SECTOR_MAP.get(pair)
                _now_at = time.time()
                _at_cached = self._adaptive_cache.get(pair)
                if _at_cached and (_now_at - _at_cached[0]) < 300:  # 5-menit TTL
                    _adaptive_bi = _at_cached[1]
                else:
                    _adaptive_bi = compute_adaptive_thresholds(hist) if len(hist) >= 30 else None
                    # Hanya cache jika compute berhasil menghasilkan nilai;
                    # jika None (history pendek), biarkan tanpa cache agar dicoba lagi tiap siklus.
                    if _adaptive_bi is not None:
                        self._adaptive_cache[pair] = (_now_at, _adaptive_bi)
                _vol_idr_bi = (self._tickers.get(pair) or {}).get('vol_idr')
                # Inject buy_pressure dari imbalance ratio (sudah tersedia dari scored pair)
                # Konversi dari ratio (0-1) ke persentase (0-100) agar sesuai threshold OB di derive_trade_signal
                _imbalance_r = float(r.get('imbalance', 0.5) or 0.5)
                tech['buy_pressure'] = round(_imbalance_r * 100.0, 1)
                signal_info = derive_trade_signal(tech, sector=_sector_bi, adaptive_thresholds=_adaptive_bi, vol_idr_24h=_vol_idr_bi)

                # Hitung trend dari history (avg_20 vs avg_50)
                avg_20 = sum(hist[-20:]) / 20 if len(hist) >= 20 else hist[-1]
                avg_50 = sum(hist[-50:]) / 50 if len(hist) >= 50 else avg_20
                if avg_20 > avg_50 * 1.001:
                    trend = "UP"
                elif avg_20 < avg_50 * 0.999:
                    trend = "DOWN"
                else:
                    trend = "SIDEWAYS"

                # Hitung perubahan harga berdasarkan history in-memory (LIVE)
                last = float(hist[-1])
                ch_5m = ((last - hist[-5]) / hist[-5] * 100) if len(hist) >= 5 and hist[-5] > 0 else 0.0
                ch_15m = ((last - hist[-15]) / hist[-15] * 100) if len(hist) >= 15 and hist[-15] > 0 else 0.0
                ch_1h = ((last - hist[-60]) / hist[-60] * 100) if len(hist) >= 60 and hist[-60] > 0 else 0.0

                updates.append({
                    'pair': pair,
                    'history_json': json.dumps(hist),
                    'prediction_prob': 0.0,
                    'trend': trend,
                    'signal': signal_info['verdict'],
                    'ch_5m': ch_5m,
                    'ch_15m': ch_15m,
                    'ch_1h': ch_1h,
                    'volatility': r['volatility'],
                    'score': r['score'],
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
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
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
    # Explicitly run migrations before starting service
    db.run_migrations()
    
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
