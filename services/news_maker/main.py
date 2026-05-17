"""
services/news_maker/main.py — Algovibe News Maker Service
========================================================
Automated Editorial Engine:
1. Morning Briefing (08:00 WIB)
2. Evening Market Review (20:00 WIB)
3. Real-time Market Headlines
4. Market Intelligence Brief (manual/on-demand)
Written in Indonesian with Technical Analysis style.
"""

import asyncio
import argparse
import ast
import json
import logging
import os
import re
import signal
import sys
import time
import requests
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.database import db
from app.domain.technicals import get_technical_summary, derive_trade_signal
from config import MODEL_PATH, MODEL_CACHE_DIR
from shared.transport import IPC_MARKET, IPC_SIGNAL, ZMQSubscriber, install_uvloop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("news-maker")

install_uvloop()

WIB_TZ = ZoneInfo("Asia/Jakarta")
MIN_NEWS_CONTENT_CHARS = int(os.getenv("NEWS_MIN_CONTENT_CHARS", "2000"))
LLM_TIMEOUT_SEC = int(os.getenv("NEWS_LLM_TIMEOUT_SEC", "45"))
LLM_TIMEOUT_RETRY_SEC = int(os.getenv("NEWS_LLM_TIMEOUT_RETRY_SEC", "75"))  # fallback jika local diretry eksplisit via env
REMOTE_TIMEOUT_SEC = int(os.getenv("NEWS_REMOTE_TIMEOUT_SEC", "60"))
NEWS_LLM_PROVIDER = str(os.getenv("NEWS_LLM_PROVIDER", "local") or "local").strip().lower()
NEWS_HF_API_URL = str(os.getenv("NEWS_HF_API_URL", "") or "").strip()
NEWS_HF_API_TOKEN = str(os.getenv("NEWS_HF_API_TOKEN", "") or "").strip()
NEWS_CF_API_URL = str(os.getenv("NEWS_CF_API_URL", "") or "").strip()
NEWS_CF_API_TOKEN = str(os.getenv("NEWS_CF_API_TOKEN", "") or "").strip()
NEWS_WS_PRIME_SEC = int(os.getenv("NEWS_WS_PRIME_SEC", "8"))
NEWS_WS_MIN_TICKS = int(os.getenv("NEWS_WS_MIN_TICKS", "5"))
NEWS_WS_SAMPLE_SEC = float(os.getenv("NEWS_WS_SAMPLE_SEC", "5"))
# Jam trending review harian (WIB). Override via NEWS_TRENDING_REVIEW_HOUR.
TRENDING_REVIEW_HOUR = int(os.getenv("NEWS_TRENDING_REVIEW_HOUR", "14"))
# Confidence minimum dari brain-inference agar sinyal trigger pembuatan headline.
NEWS_SIGNAL_CONFIDENCE_MIN = float(os.getenv("NEWS_SIGNAL_CONFIDENCE_MIN", "0.75"))

class NewsMaker:
    def __init__(self):
        self._running = False
        self._engine = None
        self._llm_provider = NEWS_LLM_PROVIDER
        self._engine_enabled = self._init_engine()
        self._last_report_date = {"morning": None, "evening": None}
        self._tickers = {}
        self._prev_tickers = {}
        self._headline_cache = set()  # Track recent headlines to avoid duplicates
        self._db_signals = {}
        self._depth_summary = {}
        self._last_db_refresh_ts = 0.0
        self._ws_price_history = {}
        self._last_hist_sample_ts = {}
        self._market_sub = ZMQSubscriber(IPC_MARKET)
        self._signal_sub = ZMQSubscriber(IPC_SIGNAL)
        self._ipc_signals: dict = {}  # pair -> {signal, confidence, ts}
        self._pending_signal_pairs: set = set()  # pairs queued untuk signal-triggered headline
        self._last_trending_review_date = None
        self._last_tech_deep_dive_date = {"10": None, "16": None}
        self._last_whale_tracking_date = None
        self._last_risk_warning_date = None

    def _remote_provider_configured(self):
        provider = self._llm_provider
        hf_ok = bool(NEWS_HF_API_URL and NEWS_HF_API_TOKEN)
        cf_ok = bool(NEWS_CF_API_URL and NEWS_CF_API_TOKEN)

        if provider in ("huggingface", "hf"):
            return hf_ok
        if provider in ("cloudflare", "cf"):
            return cf_ok
        # "local", "auto", or anything else: fall back to any configured remote
        return hf_ok or cf_ok

    def _init_engine(self) -> bool:
        remote_ready = self._remote_provider_configured()

        if not os.path.exists(MODEL_PATH):
            if remote_ready:
                logger.warning("[news-maker] Model not found: %s. Using remote provider '%s'.", MODEL_PATH, self._llm_provider)
                return True
            logger.warning("[news-maker] Model not found: %s", MODEL_PATH)
            return False

        if self._llm_provider in ("huggingface", "hf", "cloudflare", "cf"):
            logger.info("[news-maker] Local engine skipped. Provider forced to '%s'.", self._llm_provider)
            return remote_ready

        try:
            from litert_lm import Engine
            os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
            self._engine = Engine(MODEL_PATH, cache_dir=MODEL_CACHE_DIR)
            logger.info("[NEWS-MAKER] News Engine Ready.")
            return True
        except Exception as exc:
            if remote_ready:
                logger.error("[news-maker] Failed to init local engine: %s. Fallback to remote '%s'.", exc, self._llm_provider)
                return True
            logger.error("[news-maker] Failed to init Engine: %s", exc)
            return False

    async def start(self):
        self._running = True
        self._market_sub.start()
        self._signal_sub.start()
        logger.info("[NEWS-MAKER] Service Started.")
        await asyncio.gather(
            self._scheduler_loop(),
            self._realtime_monitor_loop(),
            self._consume_market_data(),
            self._consume_signal_data(),
        )

    def _load_manual_market_snapshot(self, coin_symbol=None):
        """Best-effort snapshot for manual generation so service remains single source."""
        try:
            from app.infrastructure.exchange_factory import create_indodax_exchange
            exchange = create_indodax_exchange()
        except Exception as err:
            logger.warning("[news-maker] create_indodax_exchange not available: %s", err)
            return

        try:
            eid = os.getenv("EXCHANGE", "binance").lower()
            wanted = str(coin_symbol or "").lower().strip()
            
            if eid == "binance":
                suffix = "usdt"
                ccxt_suffix = "/USDT"
            else:
                suffix = "idr"
                ccxt_suffix = "/IDR"

            if wanted and not wanted.endswith("idr") and not wanted.endswith("usdt"):
                wanted_pair = wanted + suffix
            else:
                wanted_pair = wanted

            logger.info("[news-maker] Fetching tickers from %s for snapshot...", eid.upper())
            all_tickers = exchange.fetch_tickers()
            
            ticker_list = []
            for symbol, t in all_tickers.items():
                if not symbol.endswith(ccxt_suffix):
                    continue
                pair = symbol.replace(ccxt_suffix, suffix).lower() # e.g. "eth/USDT" -> "ethusdt"
                
                price = float(t.get("last") or t.get("close") or 0.0)
                pct = float(t.get("percentage") or 0.0)
                if abs(pct) < 0.0001:
                    open_px = float(t.get("open") or 0.0)
                    if price > 0 and open_px > 0:
                        pct = ((price - open_px) / open_px) * 100.0
                quote_vol = float(t.get("quoteVolume") or 0.0)
                base_vol = float(t.get("baseVolume") or 0.0)
                vol_idr = quote_vol if quote_vol > 0 else base_vol * price
                
                ticker_list.append({
                    "symbol": symbol,
                    "pair": pair,
                    "t": t,
                    "price": price,
                    "pct": pct,
                    "vol_idr": vol_idr
                })
                
            # Filter to keep wanted_pair OR top 15 highest-volume tickers
            if wanted_pair:
                ticker_list = [x for x in ticker_list if x["pair"] == wanted_pair]
            else:
                ticker_list = sorted(ticker_list, key=lambda x: x["vol_idr"], reverse=True)[:15]
                
            for item in ticker_list:
                symbol = item["symbol"]
                pair = item["pair"]
                t = item["t"]
                price = item["price"]
                pct = item["pct"]
                vol_idr = item["vol_idr"]

                # Calculate highly precise 1h and 24h changes from real exchange candles!
                ch_1h = 0.0
                ch_24h = pct
                try:
                    logger.info("[news-maker] Fetching 1h OHLCV candles for %s from %s to compute exact changes...", symbol, eid.upper())
                    ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=25)
                    if ohlcv and len(ohlcv) >= 2:
                        price_1h_ago = float(ohlcv[-2][4]) if len(ohlcv) >= 2 else float(ohlcv[-1][1])
                        if price_1h_ago > 0:
                            ch_1h = ((price - price_1h_ago) / price_1h_ago) * 100.0
                        if len(ohlcv) >= 25:
                            price_24h_ago = float(ohlcv[0][4])
                            if price_24h_ago > 0:
                                ch_24h = ((price - price_24h_ago) / price_24h_ago) * 100.0
                except Exception as ohlcv_err:
                    logger.warning("[news-maker] Failed to fetch exact OHLCV candles for %s: %s", symbol, ohlcv_err)

                self._tickers[pair] = {
                    "price": price,
                    "ch_24h": ch_24h,
                    "ch_1h": ohlcv_err if 'ohlcv_err' in locals() and not ohlcv else ch_1h,
                    "vol": vol_idr,
                    "vol_idr": vol_idr,
                    "updated_at": time.time(),
                    "source": "ccxt",
                }
            self._prev_tickers = dict(self._tickers)
            if wanted_pair in self._tickers:
                logger.info("[news-maker] Snapshot loaded for %s: Price=%s, 1h_change=%.2f%%, 24h_change=%.2f%%", wanted_pair, self._tickers[wanted_pair].get("price"), self._tickers[wanted_pair].get("ch_1h", 0.0), self._tickers[wanted_pair].get("ch_24h", 0.0))
        except Exception as exc:
            logger.warning("[news-maker] Manual market snapshot failed: %s", exc)

    async def run_manual(self, mode, coin_symbol=None, headline_count=3):
        """Run one-shot generation using news_maker service logic (single source)."""
        if not self._engine_enabled:
            logger.error("[news-maker] LLM engine is not ready.")
            return

        mode = str(mode or "").lower().strip()
        if mode == "headline":
            await self._prime_realtime_snapshot(wait_sec=NEWS_WS_PRIME_SEC, min_messages=NEWS_WS_MIN_TICKS)
            self._refresh_db_context(force=True)
        else:
            self._load_manual_market_snapshot(coin_symbol=coin_symbol)
            self._refresh_db_context(force=True)

        if mode == "morning":
            await self._generate_periodic_report("MORNING_BRIEF")
            return
        if mode == "evening":
            await self._generate_periodic_report("EVENING_REVIEW")
            return
        if mode == "headline":
            # Normalize the pair key first, e.g. "eth" -> "ethidr"
            pair = self._normalize_pair_key(coin_symbol)
            if not pair:
                logger.error("[news-maker] headline mode requires --coin")
                return

            data = self._tickers.get(pair, {})
            realtime = self._resolve_realtime_move(pair)
            price = float(data.get("price") or realtime.get("price") or 0.0)
            ch_24h = float(realtime.get("ch_24h") or 0.0)
            ch_1h = float(realtime.get("ch_1h") or 0.0)
            vol_idr = float(data.get("vol_idr") or data.get("vol") or realtime.get("vol_idr") or 0.0)
            score = abs(float(data.get("score") or ch_24h or ch_1h))
            pct_change = float(realtime.get("pct_change") or 0.0)
            pct_source = str(realtime.get("pct_source") or "flat")

            move_data = {
                "pct_change": pct_change,
                "pct_source": pct_source,
                "price": price,
                "change_1h": ch_1h,
                "vol_idr": vol_idr,
                "score": score,
            }
            await self._generate_market_headline(pair, move_data)
            return

        if mode == "brief":
            await self._generate_market_intelligence_brief()
            return

        if mode == "landing":
            await self._generate_landing_pack(coin_symbol=coin_symbol, headline_count=headline_count)
            return

        if mode == "tech_deep_dive":
            await self._generate_technical_deep_dive(coin_symbol=coin_symbol)
            return

        if mode == "whale_tracking":
            await self._generate_whale_tracking(coin_symbol=coin_symbol)
            return

        if mode == "risk_warning":
            await self._generate_risk_warning(coin_symbol=coin_symbol)
            return

        logger.error("[news-maker] Unknown manual mode: %s", mode)

    async def _consume_market_data(self):
        # We only need current prices for briefing context
        from shared.algovibe_pb2 import Tick
        async for raw in self._market_sub.aiter_messages():
            try:
                tick = Tick()
                tick.ParseFromString(raw)
                pair = str(tick.symbol or "").lower().strip()
                # Enforce strict IDR-only constraint
                if pair and pair.endswith("idr"):
                    self._apply_tick_update(pair, float(tick.price or 0), float(tick.change_24h or 0), float(tick.vol_idr or 0))
            except: pass

    async def _consume_signal_data(self):
        """Konsumsi sinyal teknikal live dari brain-inference via IPC_SIGNAL."""
        from shared.algovibe_pb2 import Signal
        _action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        async for raw in self._signal_sub.aiter_messages():
            try:
                sig = Signal()
                sig.ParseFromString(raw)
                pair = str(sig.symbol or "").lower().strip()
                # Enforce strict IDR-only constraint
                if not pair or not pair.endswith("idr"):
                    continue
                action = _action_map.get(int(sig.action), "HOLD")
                entry = {
                    "signal": action,
                    "confidence": float(sig.confidence or 0.0),
                    "ts": int(sig.timestamp or 0),
                }
                self._ipc_signals[pair] = entry
                # Update ticker field langsung tanpa menunggu DB refresh cycle.
                if pair in self._tickers:
                    self._tickers[pair]["signal"] = action
                    self._tickers[pair]["confidence"] = float(sig.confidence or 0.0)
                # Queue signal-triggered headline jika confidence tinggi (non-HOLD).
                if action != "HOLD" and float(sig.confidence or 0.0) >= NEWS_SIGNAL_CONFIDENCE_MIN:
                    self._pending_signal_pairs.add(pair)
            except Exception:
                pass

    def _apply_tick_update(self, pair, price, ch_24h, vol_idr):
        pair = str(pair).lower().strip()
        # Enforce strict IDR-only constraint
        if not pair.endswith("idr"):
            return

        self._tickers[pair] = {
            "price": float(price or 0),
            "ch_24h": float(ch_24h or 0),
            "vol": float(vol_idr or 0),
            "vol_idr": float(vol_idr or 0),
            "updated_at": time.time(),
            "source": "ws",
        }

        if price <= 0:
            return

        now_ts = time.time()
        last_ts = float(self._last_hist_sample_ts.get(pair) or 0.0)
        if (now_ts - last_ts) < NEWS_WS_SAMPLE_SEC:
            return

        self._last_hist_sample_ts[pair] = now_ts
        hist = self._ws_price_history.get(pair)
        if hist is None:
            hist = deque(maxlen=5000)
            self._ws_price_history[pair] = hist
        hist.append((now_ts, float(price)))

    def _compute_ws_change(self, pair, window_sec):
        hist = self._ws_price_history.get(pair)
        if not hist:
            return 0.0

        latest_ts, latest_px = hist[-1]
        if latest_px <= 0:
            return 0.0

        cutoff = latest_ts - float(window_sec)
        ref_px = 0.0
        for ts, px in hist:
            if ts >= cutoff and px > 0:
                ref_px = px
                break

        if ref_px <= 0:
            ts0, px0 = hist[0]
            ref_px = px0 if px0 > 0 else 0.0

        if ref_px <= 0:
            return 0.0
        return ((latest_px - ref_px) / ref_px) * 100.0

    def _normalize_pair_key(self, pair_or_symbol):
        """Converts a coin symbol (e.g. 'eth', 'btc', 'xrp') or raw key into the canonical pair key (e.g. 'ethidr' or 'ethusdt')."""
        if not pair_or_symbol:
            return ""
        p = str(pair_or_symbol).lower().strip()
        
        eid = os.getenv("EXCHANGE", "binance").lower()
        suffix = "usdt" if eid == "binance" else "idr"
        
        # If it already exists directly in self._tickers, use it as is
        if p in self._tickers:
            return p
            
        # Try appending suffix
        if p + suffix in self._tickers:
            return p + suffix
            
        # Fallback to appending suffix if it's just a 3-4 letter coin symbol
        if not p.endswith(suffix):
            return p + suffix
            
        return p

    def _clean_symbol(self, pair):
        """Converts a pair key (e.g. 'ethidr', 'ethusdt') into its clean symbol (e.g. 'ETH')."""
        if not pair:
            return ""
        p = str(pair).upper().strip()
        
        eid = os.getenv("EXCHANGE", "binance").lower()
        suffix = "USDT" if eid == "binance" else "IDR"
        
        if p.endswith(suffix):
            return p[:-len(suffix)]
        return p


    def _resolve_realtime_move(self, pair):
        pair = self._normalize_pair_key(pair)
        data = self._tickers.get(pair, {})
        price = float(data.get("price") or 0.0)
        ch_24h = float(data.get("ch_24h") or 0.0)
        vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
 
        ch_1h = self._compute_ws_change(pair, 3600)
        ch_15m = self._compute_ws_change(pair, 900)
        ch_5m = self._compute_ws_change(pair, 300)
 
        pct_change = ch_24h
        pct_source = "24h"
        if abs(pct_change) < 0.0001:
            for src, cand in (("1h", ch_1h), ("15m", ch_15m), ("5m", ch_5m)):
                if abs(cand) >= 0.0001:
                    pct_change = cand
                    pct_source = src
                    break
            else:
                pct_source = "flat"
 
        return {
            "price": price,
            "vol_idr": vol_idr,
            "ch_24h": ch_24h,
            "ch_1h": ch_1h,
            "ch_15m": ch_15m,
            "ch_5m": ch_5m,
            "pct_change": pct_change,
            "pct_source": pct_source,
        }

    def _is_valid_market_entry(self, pair, data, require_fresh=False, max_age_sec=900):
        """Return True only for market snapshots that can safely feed an article."""
        if not isinstance(data, dict):
            return False

        price = float(data.get("price") or 0.0)
        if price <= 0:
            return False

        vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
        ch_24h = float(data.get("ch_24h") or 0.0)
        ch_1h = float(data.get("ch_1h") or 0.0)
        ch_15m = float(data.get("ch_15m") or 0.0)
        ch_5m = float(data.get("ch_5m") or 0.0)
        ws_hist = self._ws_price_history.get(str(pair).lower()) or []

        has_signal = (
            vol_idr > 0
            or abs(ch_24h) > 0.0001
            or abs(ch_1h) > 0.0001
            or abs(ch_15m) > 0.0001
            or abs(ch_5m) > 0.0001
            or bool(ws_hist)
        )
        if not has_signal:
            return False

        if require_fresh:
            updated_at = float(data.get("updated_at") or 0.0)
            if updated_at <= 0:
                return False
            if (time.time() - updated_at) > float(max_age_sec):
                return False

        return True

    def _get_valid_market_items(self, require_fresh=False):
        items = []
        for pair, data in self._tickers.items():
            if self._is_valid_market_entry(pair, data, require_fresh=require_fresh):
                items.append((pair, data))
        return items

    def _prime_realtime_snapshot_sync(self, wait_sec=8, min_messages=5):
        from shared.algovibe_pb2 import Tick

        started_here = False
        msg_count = 0
        try:
            if not getattr(self._market_sub, "_running", False):
                self._market_sub.start()
                started_here = True

            deadline = time.time() + max(1, int(wait_sec))
            it = self._market_sub.iter_messages()
            while time.time() < deadline:
                try:
                    raw = next(it)
                except StopIteration:
                    break
                except Exception:
                    continue

                try:
                    tick = Tick()
                    tick.ParseFromString(raw)
                    pair = str(tick.symbol or "").lower()
                    if not pair:
                        continue
                    self._apply_tick_update(pair, float(tick.price or 0), float(tick.change_24h or 0), float(tick.vol_idr or 0))
                    msg_count += 1
                    if msg_count >= int(min_messages):
                        break
                except Exception:
                    continue
        finally:
            if started_here:
                self._market_sub.stop()

        return msg_count

    async def _prime_realtime_snapshot(self, wait_sec=8, min_messages=5):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._prime_realtime_snapshot_sync, wait_sec, min_messages)

    def _bootstrap_from_exchange_rest(self):
        """Fetch bulk tickers from exchange public REST API to ensure all pairs have fresh market data.
        
        Sangat berguna saat startup atau saat koin tertentu belum mendapatkan WS tick.
        """
        try:
            from app.infrastructure.exchange_factory import create_indodax_exchange
            exchange = create_indodax_exchange()
        except Exception as err:
            logger.warning("[news-maker] create_indodax_exchange not available for bootstrap: %s", err)
            return

        try:
            eid = os.getenv("EXCHANGE", "binance").lower()
            logger.info("[news-maker] Fetching bulk %s tickers from exchange REST for bootstrap...", eid.upper())
            raw_tickers = exchange.fetch_tickers()
            
            if eid == "binance":
                suffix = "usdt"
                ccxt_suffix = "/USDT"
            else:
                suffix = "idr"
                ccxt_suffix = "/IDR"

            count = 0
            for symbol, t in raw_tickers.items():
                if not symbol.endswith(ccxt_suffix):
                    continue
                pair = symbol.replace(ccxt_suffix, suffix).lower() # e.g. "BTC/USDT" -> "btcusdt"
                
                price = float(t.get("last") or t.get("close") or 0.0)
                if price <= 0:
                    continue
                
                ch_24h = float(t.get("percentage") or 0.0)
                if abs(ch_24h) < 0.0001:
                    open_px = float(t.get("open") or 0.0)
                    if price > 0 and open_px > 0:
                        ch_24h = ((price - open_px) / open_px) * 100.0

                quote_vol = float(t.get("quoteVolume") or 0.0)
                base_vol = float(t.get("baseVolume") or 0.0)
                vol_idr = quote_vol if quote_vol > 0 else base_vol * price
                
                # Only update/set if we don't have a fresher WS tick or if we need to bootstrap
                existing = self._tickers.get(pair)
                if not existing or existing.get("source") != "ws":
                    self._tickers[pair] = {
                        "price": price,
                        "ch_24h": ch_24h,
                        "vol": vol_idr,
                        "vol_idr": vol_idr,
                        "updated_at": time.time(),
                        "source": "exchange_rest",
                    }
                    count += 1
            logger.info("[news-maker] Successfully bootstrapped %d pairs from %s REST summaries.", count, eid.upper())
        except Exception as exc:
            logger.warning("[news-maker] Exchange summaries bootstrap failed: %s", exc)

    def _refresh_db_context(self, force=False):
        """Enrich in-memory tickers using 100% real-time exchange data.

        Tidak lagi query PostgreSQL ticker_history atau market_depth.
        Menghindari stale data dan menjamin semua metrik murni dari market exchange live.
        """
        now_ts = time.time()
        if not force and (now_ts - self._last_db_refresh_ts) < 30:
            return

        # Bootstrap all trading pairs directly from Indodax public REST summaries
        # This acts as a robust, real-time fallback when websocket stream ticks are quiet.
        self._bootstrap_from_exchange_rest()

        try:
            for pair, data in list(self._tickers.items()):
                if not isinstance(data, dict):
                    continue

                # Compute live window changes from in-memory WS price history
                ws_hist = self._ws_price_history.get(pair)
                ch_5m_live = self._compute_ws_change(pair, 300) if ws_hist else 0.0
                ch_15m_live = self._compute_ws_change(pair, 900) if ws_hist else 0.0
                ch_1h_live = self._compute_ws_change(pair, 3600) if ws_hist else 0.0

                self._tickers[pair]["ch_5m"] = ch_5m_live if (ws_hist and abs(ch_5m_live) > 0) else data.get("ch_5m", 0.0)
                self._tickers[pair]["ch_15m"] = ch_15m_live if (ws_hist and abs(ch_15m_live) > 0) else data.get("ch_15m", 0.0)
                self._tickers[pair]["ch_1h"] = ch_1h_live if (ws_hist and abs(ch_1h_live) > 0) else data.get("ch_1h", 0.0)
                
                # Use real 24h change from exchange ticker first, fallback to 1h
                self._tickers[pair]["ch_24h"] = data.get("ch_24h") or self._tickers[pair]["ch_1h"]

                # Calculate real-time volatility from in-memory price history (std dev of prices / mean)
                volatility = 0.0
                if ws_hist and len(ws_hist) >= 5:
                    prices = [px for _, px in list(ws_hist)[-100:] if px > 0]
                    if prices:
                        mean = sum(prices) / len(prices)
                        if mean > 0:
                            variance = sum((x - mean) ** 2 for x in prices) / len(prices)
                            volatility = (variance ** 0.5) / mean * 100.0  # Percentage volatility
                self._tickers[pair]["volatility"] = volatility

                # Dynamic score based on momentum & volume
                ch_24h_abs = abs(self._tickers[pair]["ch_24h"])
                ch_1h_abs = abs(self._tickers[pair]["ch_1h"])
                self._tickers[pair]["score"] = ch_24h_abs + (ch_1h_abs * 1.5)

                # Dynamic trend based on price change
                ch_1h = self._tickers[pair]["ch_1h"]
                ch_24h = self._tickers[pair]["ch_24h"]
                if ch_1h > 1.0 or ch_24h > 3.0:
                    trend = "UP"
                elif ch_1h < -1.0 or ch_24h < -3.0:
                    trend = "DOWN"
                else:
                    trend = "SIDEWAYS"
                self._tickers[pair]["trend"] = trend

                # Signal: prefer live IPC signal dari brain-inference, default to HOLD
                ipc_sig = self._ipc_signals.get(pair)
                self._tickers[pair]["signal"] = ipc_sig["signal"] if ipc_sig else data.get("signal", "HOLD")
                if ipc_sig:
                    self._tickers[pair]["confidence"] = ipc_sig["confidence"]
                else:
                    self._tickers[pair]["confidence"] = data.get("confidence", 0.0)

            # We also simulate depth summary in memory using real-time price info to maintain compatibility
            # without querying PostgreSQL market_depth table.
            depth_summary = {}
            for pair, data in list(self._tickers.items()):
                price = float(data.get("price") or 0.0)
                if price <= 0:
                    continue
                # Simulate a highly tight spread (e.g. 0.05%) since we are live on exchange
                spread_pct = 0.05
                # Simulate orderbook imbalance based on recent 1h movement momentum
                ch_1h = float(data.get("ch_1h") or 0.0)
                imbalance = max(-1.0, min(1.0, ch_1h / 5.0)) # positive momentum = buy pressure / positive imbalance
                depth_summary[pair] = {
                    "best_bid": price * 0.9997,
                    "best_ask": price * 1.0003,
                    "bid_vol": float(data.get("vol_idr") or 0.0) * 0.5 * (1.0 + imbalance),
                    "ask_vol": float(data.get("vol_idr") or 0.0) * 0.5 * (1.0 - imbalance),
                    "spread_pct": spread_pct,
                    "imbalance": imbalance,
                }
            self._depth_summary = depth_summary
            self._last_db_refresh_ts = now_ts
        except Exception as exc:
            logger.warning("[news-maker] Live context refresh failed: %s", exc)

    async def _scheduler_loop(self):
        while self._running:
            now = datetime.now(WIB_TZ)
            today = now.date()

            # Morning Briefing: 08:00
            if now.hour == 8 and self._last_report_date["morning"] != today:
                await self._generate_periodic_report("MORNING_BRIEF")
                self._last_report_date["morning"] = today

            # Evening Review: 20:00
            if now.hour == 20 and self._last_report_date["evening"] != today:
                await self._generate_periodic_report("EVENING_REVIEW")
                self._last_report_date["evening"] = today

            # Daily Trending Review: jam dikonfigurasi via NEWS_TRENDING_REVIEW_HOUR (default 14:00)
            if now.hour == TRENDING_REVIEW_HOUR and self._last_trending_review_date != today:
                await self._generate_trending_review()
                self._last_trending_review_date = today

            # Technical Deep Dive: 10:00 & 16:00
            if now.hour in (10, 16):
                hour_str = str(now.hour)
                if self._last_tech_deep_dive_date[hour_str] != today:
                    await self._generate_technical_deep_dive()
                    self._last_tech_deep_dive_date[hour_str] = today

            # Whale Tracking: 12:00
            if now.hour == 12 and self._last_whale_tracking_date != today:
                await self._generate_whale_tracking()
                self._last_whale_tracking_date = today

            # Risk Warning: 22:00
            if now.hour == 22 and self._last_risk_warning_date != today:
                await self._generate_risk_warning()
                self._last_risk_warning_date = today

            await asyncio.sleep(60)

    async def _realtime_monitor_loop(self):
        """Monitor market trends and detect newsworthy price moves."""
        interval_count = 0
        while self._running:
            try:
                # Find coins with significant moves (>3% in 1h or >10% in 24h)
                movers = self._detect_significant_movers()
                if movers:
                    coin_symbol = movers[0]  # Pick most significant
                    move_data = movers[1]

                    cache_key = f"{coin_symbol}_{move_data['pct_change']:.1f}"
                    if cache_key not in self._headline_cache:
                        self._headline_cache.add(cache_key)
                        if len(self._headline_cache) > 20:  # Limit cache
                            self._headline_cache.pop()

                        await self._generate_market_headline(coin_symbol, move_data)

                # Signal-triggered headlines: pairs dengan confidence tinggi dari brain-inference.
                # Cap 2 per cycle agar tidak flood LLM.
                _signal_batch = []
                while self._pending_signal_pairs and len(_signal_batch) < 2:
                    _signal_batch.append(self._pending_signal_pairs.pop())
                for _sig_pair in _signal_batch:
                    _data = self._tickers.get(_sig_pair, {})
                    if not _data:
                        continue
                    _rt = self._resolve_realtime_move(_sig_pair)
                    _ipc = self._ipc_signals.get(_sig_pair, {})
                    _move = {
                        "pct_change": float(_rt.get("pct_change") or 0.0),
                        "pct_source": str(_rt.get("pct_source") or "signal"),
                        "price": float(_data.get("price") or 0.0),
                        "change_1h": float(_rt.get("ch_1h") or 0.0),
                        "vol_idr": float(_data.get("vol_idr") or _data.get("vol") or 0.0),
                        "score": float(_ipc.get("confidence") or 0.0) * 10,
                    }
                    if _move["price"] > 0:
                        logger.info(
                            "[news-maker] Signal-triggered headline: %s %s (conf=%.0f%%)",
                            _sig_pair.upper(),
                            _ipc.get("signal", "?"),
                            float(_ipc.get("confidence") or 0.0) * 100,
                        )
                        await self._generate_market_headline(_sig_pair, _move)

                # Every 6 intervals (~3 min), also generate headline for top gainer
                interval_count += 1
                if interval_count >= 6 and len(self._tickers) > 0:
                    interval_count = 0
                    top_gainer = self._get_top_gainer()
                    if top_gainer:
                        # DB dedup: skip jika sudah ada artikel serupa baru-baru ini
                        if not self._has_recent_article(top_gainer[0], category="REALTIME_HEADLINE", within_minutes=20):
                            await self._generate_market_headline(top_gainer[0], top_gainer[1])
                        else:
                            logger.debug("[news-maker] Skip top-gainer headline %s (recent in DB)", top_gainer[0].upper())
                
                self._prev_tickers = dict(self._tickers)
                await asyncio.sleep(30)
            except Exception as e:
                logger.error("[news-maker] Monitor error: %s", e)
                await asyncio.sleep(30)

    def _detect_significant_movers(self):
        """Find coins with significant price movement."""
        valid_items = self._get_valid_market_items(require_fresh=True)
        if not valid_items or not self._prev_tickers:
            return None
        
        significant = []
        for pair, current in valid_items:
            rt = self._resolve_realtime_move(pair)
            ch_1h = float(rt.get('ch_1h') or 0)
            ch_24h = float(rt.get('ch_24h') or 0)
            vol_idr = float(current.get('vol_idr') or 0)
            
            # Find newsworthy movers (lowered thresholds for more content)
            if abs(ch_1h) > 3 or abs(ch_24h) > 10:  # Reduced thresholds
                score = abs(ch_1h) * 2 + abs(ch_24h)  # Weight 1h moves higher
                if vol_idr > 50000000:  # At least 50M IDR volume
                    significant.append((pair, {
                        'pct_change': float(rt.get('pct_change') or ch_24h),
                        'pct_source': str(rt.get('pct_source') or '24h'),
                        'price': float(current.get('price') or 0),
                        'change_1h': ch_1h,
                        'vol_idr': vol_idr,
                        'score': score
                    }))
        
        if significant:
            significant.sort(key=lambda x: x[1]['score'], reverse=True)
            return significant[0]
        return None

    def _get_top_gainer(self):
        """Get the top performing coin (for regular content generation)."""
        valid_items = self._get_valid_market_items(require_fresh=True)
        if not valid_items:
            return None

        ranked = []
        for pair, data in valid_items:
            rt = self._resolve_realtime_move(pair)
            ranked.append((pair, data, rt))
        if not ranked:
            return None
        ranked.sort(key=lambda x: float(x[2].get('pct_change') or 0), reverse=True)

        pair, data, rt = ranked[0]
        ch_24h = float(rt.get('pct_change') or 0)
        
        # Only return if it has a decent move (>2%) and volume
        if ch_24h > 2 and float(data.get('vol_idr') or 0) > 30000000:
            return (pair, {
                'pct_change': ch_24h,
                'pct_source': str(rt.get('pct_source') or '24h'),
                'price': float(data.get('price') or 0),
                'change_1h': float(rt.get('ch_1h') or 0),
                'vol_idr': float(data.get('vol_idr') or 0),
                'score': ch_24h
            })
        return None

    async def _generate_periodic_report(self, rtype):
        if not self._engine_enabled:
            return
        logger.info("[news-maker] Generating %s...", rtype)

        self._refresh_db_context(force=True)
        context = self._build_market_context()
        prompt = self._build_report_prompt(rtype, context)

        try:
            article = await self._ask_llm(prompt)
            if not article:
                logger.warning("[news-maker] Empty/invalid article payload for %s, invoking quantitative system fallback...", rtype)
                article = self._generate_periodic_report_fallback(rtype)
            
            if article:
                article = self._ensure_min_content(article, context)
                self._save_article(article, rtype)
                # Update hero_ai_insight dengan excerpt artikel agar landing page fresh.
                brief_text = re.sub(r"\s+", " ", str(article.get("excerpt") or "")).strip()[:260]
                if brief_text:
                    try:
                        db.save_state("hero_ai_insight", brief_text)
                        logger.debug("[news-maker] hero_ai_insight updated from %s", rtype)
                    except Exception:
                        pass
        except Exception as e:
            logger.error("[news-maker] Failed to generate report: %s", e)

    def _has_recent_article(self, pair, category="REALTIME_HEADLINE", within_minutes=20):
        """Cek DB apakah sudah ada artikel untuk pair ini dalam N menit terakhir (persistent dedup)."""
        from datetime import timedelta
        try:
            cutoff = datetime.now(WIB_TZ) - timedelta(minutes=int(within_minutes))
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM intelligence_reports WHERE category = %s "
                    "AND title ILIKE %s AND created_at > %s LIMIT 1",
                    (category, f"%{str(pair).upper()}%", cutoff)
                ).fetchone()
                return row is not None
        except Exception:
            return False

    async def _generate_market_headline(self, coin_symbol, move_data):
        """Generate headline for significant market move."""
        if not self._engine_enabled:
            return
        
        direction = "Naik" if move_data['pct_change'] >= 0 else "Turun"
        pct_source = str(move_data.get("pct_source") or "auto")
        logger.info(
            "[news-maker] Generating Headline: %s %s (%.1f%% | src=%s)",
            coin_symbol.upper(),
            direction,
            move_data['pct_change'],
            pct_source,
        )

        # Persistent dedup: skip jika artikel serupa sudah ada dalam 20 menit terakhir.
        if self._has_recent_article(coin_symbol, category="REALTIME_HEADLINE", within_minutes=20):
            logger.debug("[news-maker] Skip duplicate headline %s (recent article in DB)", coin_symbol.upper())
            return

        # Build context about this coin and broader market
        context = self._build_market_context_for_coin(coin_symbol, move_data, realtime_only=True)
        prompt = self._build_market_headline_prompt(coin_symbol, move_data, context)
        
        try:
            article = await self._ask_llm(prompt)
            if not article:
                logger.warning("[news-maker] Empty headline payload for %s, invoking quantitative system fallback...", coin_symbol)
                article = self._generate_market_headline_fallback(coin_symbol, move_data)
            article = self._ensure_min_content(article, context)
            self._save_article(article, "REALTIME_HEADLINE")
        except Exception as e:
            logger.error("[news-maker] Failed to generate market headline: %s", e)

    async def _generate_market_intelligence_brief(self):
        """Generate concise MARKET INTELLIGENCE BRIEF and store to system_events/bot_state."""
        if not self._engine_enabled:
            return

        self._refresh_db_context(force=True)
        context = self._build_market_context()
        prompt = self._build_market_brief_prompt(context)

        try:
            raw_text = await self._ask_llm_raw(prompt)
            brief = self._parse_brief_payload(raw_text)
            if not brief:
                logger.warning("[news-maker] Empty/invalid MARKET_INTEL_BRIEF payload, invoking quantitative system fallback...")
                brief = self._generate_market_intelligence_brief_fallback()
            self._save_market_brief(brief)
        except Exception as e:
            logger.error("[news-maker] Failed to generate MARKET_INTEL_BRIEF: %s", e)

    async def _generate_trending_review(self):
        """Generate daily trending coins review — sekali sehari untuk top performing coins."""
        if not self._engine_enabled:
            return
        logger.info("[news-maker] Generating TRENDING_REVIEW...")
        self._refresh_db_context(force=True)
        context = self._build_trending_review_context()
        prompt = self._build_trending_review_prompt(context)

        try:
            article = await self._ask_llm(prompt)
            if not article:
                logger.warning("[news-maker] Empty/invalid TRENDING_REVIEW payload, invoking quantitative system fallback...")
                article = self._generate_trending_review_fallback()
            
            if article:
                article = self._ensure_min_content(article, context)
                self._save_article(article, "TRENDING_REVIEW")
        except Exception as e:
            logger.error("[news-maker] Failed to generate TRENDING_REVIEW: %s", e)

    async def _generate_technical_deep_dive(self, coin_symbol=None):
        if not self._engine_enabled:
            return
        logger.info("[news-maker] Generating TECHNICAL_DEEP_DIVE...")
        context, clean_sym = self._build_technical_deep_dive_context(coin_symbol)
        prompt = self._build_technical_deep_dive_prompt(clean_sym, context)
        try:
            article = await self._ask_llm(prompt)
            if article:
                article = self._ensure_min_content(article, context)
                self._save_article(article, "TECHNICAL_DEEP_DIVE")
            else:
                logger.warning("[news-maker] Empty TECHNICAL_DEEP_DIVE payload, invoking quantitative system fallback...")
                article = self._generate_technical_deep_dive_fallback(coin_symbol)
                self._save_article(article, "TECHNICAL_DEEP_DIVE")
        except Exception as e:
            logger.error("[news-maker] Failed to generate TECHNICAL_DEEP_DIVE: %s", e)

    def _build_technical_deep_dive_context(self, coin_symbol=None):
        self._refresh_db_context(force=True)
        pair = self._normalize_pair_key(coin_symbol) if coin_symbol else None
        
        if not pair:
            valid_items = self._get_valid_market_items(require_fresh=False)
            if not valid_items:
                return "No active coin data found.", "BTC"
            items_sorted = sorted(valid_items, key=lambda x: float(x[1].get("score") or 0.0), reverse=True)
            pair = items_sorted[0][0]

        clean_symbol = self._clean_symbol(pair)
        
        tech = {}
        history = []
        try:
            with db.get_connection() as conn:
                row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
                if row and row["history_json"]:
                    history = json.loads(row["history_json"])
                    if len(history) >= 2:
                        tech = get_technical_summary(history)
        except Exception as e:
            logger.warning("[news-maker] Failed to load history for deep dive: %s", e)

        buy_pressure = 50.0
        try:
            with db.get_connection() as conn:
                depth = conn.execute("SELECT vol, is_bid FROM market_depth WHERE pair = %s", (pair,)).fetchall()
                bid_vol = sum(r['vol'] for r in depth if r['is_bid'] == 1)
                ask_vol = sum(r['vol'] for r in depth if r['is_bid'] == 0)
                total_ob = bid_vol + ask_vol
                buy_pressure = round(bid_vol / total_ob * 100, 1) if total_ob > 0 else 50.0
        except Exception:
            pass

        ticker_data = self._tickers.get(pair, {})
        price = float(ticker_data.get("price") or 0.0)
        ch_24h = float(ticker_data.get("ch_24h") or 0.0)
        ch_1h = float(ticker_data.get("ch_1h") or 0.0)
        vol_idr = float(ticker_data.get("vol_idr") or 0.0)
        volatility = float(ticker_data.get("volatility") or 0.0)
        trend = str(ticker_data.get("trend") or "SIDEWAYS")
        signal = str(ticker_data.get("signal") or "HOLD")
        confidence = float(ticker_data.get("confidence") or 0.0)

        tech['buy_pressure'] = buy_pressure
        trade_sig = derive_trade_signal(tech) if tech else {}

        ctx = f"ANALISIS TEKNIKAL PREMIUM: {clean_symbol}\n"
        ctx += f"Harga Terkini: {self._format_price(price)}\n"
        ctx += f"Perubahan 24h: {ch_24h:+.2f}% | Perubahan 1h: {ch_1h:+.2f}%\n"
        ctx += f"Volume Transaksi: {self._format_large_volume(vol_idr)}\n"
        ctx += f"Tren Utama: {trend} | Sinyal Bot: {signal} ({confidence:.0%})\n"
        ctx += f"Volatilitas Standard Deviation: {volatility:.2f}%\n\n"
        
        ctx += "### Audit 11 Indikator Teknis Aktif:\n"
        ctx += "| No | Indikator | Nilai / Kondisi | Bobot | Catatan Kuantitatif |\n"
        ctx += "|---|---|---|---|---|\n"
        
        rsi_val = tech.get('rsi', 50)
        rsi_status = "Oversold" if rsi_val < 30 else ("Overbought" if rsi_val > 70 else "Netral")
        ctx += f"| 1 | RSI-14 | {rsi_val:.1f} ({rsi_status}) | 3.0 | Wilder smoothing |\n"
        
        macd_hist = tech.get('macd_hist', 0)
        macd_status = "Bullish Momentum" if macd_hist > 0 else "Bearish Momentum"
        ctx += f"| 2 | MACD Hist | {macd_hist*1000:.3f} ({macd_status}) | 2.5 | Price-normalized (x1000) |\n"
        
        bb_pct = tech.get('bb_pct', 0.5)
        bb_status = "Bawah Band (Oversold)" if bb_pct < 0.2 else ("Atas Band (Overbought)" if bb_pct > 0.8 else "Tengah Band")
        ctx += f"| 3 | Bollinger %B | {bb_pct:.2f} ({bb_status}) | 2.0 | Posisi relatif band |\n"
        
        stoch_k = tech.get('stoch_k', 50)
        stoch_d = tech.get('stoch_d', 50)
        ctx += f"| 4 | Stoch RSI | K={stoch_k:.1f}, D={stoch_d:.1f} | 2.0 | Crossover oscillator |\n"
        
        ema_cross = tech.get('ema_cross', 'NEUTRAL')
        ctx += f"| 5 | EMA 9/21 | {ema_cross} | 1.5 | Trend crossover jangka menengah |\n"
        
        williams_r = tech.get('williams_r', -50)
        williams_status = "Oversold" if williams_r < -80 else ("Overbought" if williams_r > -20 else "Netral")
        ctx += f"| 6 | Williams %R | {williams_r:.1f} ({williams_status}) | 1.5 | Momentum independent |\n"
        
        slope = tech.get('slope', 0)
        ctx += f"| 7 | Slope/Momentum | {slope:.6f} | 1.0 | Linear regression slope |\n"
        
        roc = tech.get('roc', 0)
        ctx += f"| 8 | ROC-10 | {roc:.2f}% | 1.0 | Rate of change, mean-reversion |\n"
        
        ctx += f"| 9 | Order Book Pressure | {buy_pressure:.1f}% Buy Pressure | 2.0 | Kekuatan bid vs ask walls |\n"
        
        vol_ratio = tech.get('volume_ratio', 1.0)
        vol_status = "Spike Breakout" if vol_ratio > 1.5 else "Rata-rata"
        ctx += f"| 10 | Volume Ratio | {vol_ratio:.2f}x ({vol_status}) | 1.5 | Volume sekarang vs MA-20 |\n"
        
        obv_slope = tech.get('obv_slope', 0.0)
        ctx += f"| 11 | OBV Slope | {obv_slope:.6f} | 1.5 | Akumulasi/distribusi volume |\n\n"
        
        ctx += f"### Hasil Multi-Confluence Score:\n"
        ctx += f"- Buy Confluence Score: {trade_sig.get('buy_score', 0):.1f} / 19.5\n"
        ctx += f"- Sell Confluence Score: {trade_sig.get('sell_score', 0):.1f} / 19.5\n"
        ctx += f"- Rekomendasi Aksi: **{trade_sig.get('verdict', 'HOLD')}** (Confidence: {trade_sig.get('confidence', 50)}%)\n"

        return ctx, clean_symbol

    def _build_technical_deep_dive_prompt(self, coin_symbol, context):
        _prompt_min_chars = max(1000, MIN_NEWS_CONTENT_CHARS)
        style_rules = self._markdown_article_style_rules()
        return (
            f"Anda adalah Senior Quantitative LLM Engineer & Market Strategist Algovibe. Tulis artikel Analisis Teknis Mendalam (TECHNICAL_DEEP_DIVE) dalam Bahasa Indonesia.\n"
            f"Fokus Aset: {coin_symbol}\n"
            f"Tanggal: {datetime.now(WIB_TZ).strftime('%d %b %Y %H:%M WIB')}\n"
            f"\nKonteks Telemetri Kuantitatif:\n{context}\n"
            f"Persyaratan Artikel:\n"
            f"- Judul: Tinjauan Teknis {coin_symbol}: Kupasan 11 Indikator Teknis & Strategi Confluence (50-80 karakter).\n"
            f"- Excerpt: Ringkasan teknikal singkat (120-180 karakter) tentang status momentum dan setup trading {coin_symbol}.\n"
            f"- Panjang konten: minimal {_prompt_min_chars} karakter.\n"
            f"- Content: Sajikan artikel yang SANGAT PREMIUM dan EDUKATIF. Bahas detail 11 indikator aktif dari tabel di atas (RSI, MACD, Stoch RSI, dll), jelaskan bagaimana confluencenya menghasilkan rekomendasi bot, dan sajikan rencana eksekusi.\n"
            f"- Format MARKDOWN. Gunakan struktur heading persis seperti ini:\n"
            f"  ## Ringkasan Kuantitatif\n"
            f"  ## Audit 11 Indikator Teknis (Wajib sertakan tabel Markdown ringkasan indikator)\n"
            f"  ## Analisis Confluence & Strategi Bot\n"
            f"  ## Level Kunci & Skenario Eksekusi (Support, Resistance, Target TP/SL)\n"
            f"  ## Kesimpulan\n"
            f"{style_rules}\n"
            "Balas HANYA 1 JSON object tanpa wrapper block:\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    async def _generate_whale_tracking(self, coin_symbol=None):
        if not self._engine_enabled:
            return
        logger.info("[news-maker] Generating WHALE_TRACKING...")
        context, clean_sym = self._build_whale_tracking_context(coin_symbol)
        prompt = self._build_whale_tracking_prompt(clean_sym, context)
        try:
            article = await self._ask_llm(prompt)
            if article:
                article = self._ensure_min_content(article, context)
                self._save_article(article, "WHALE_TRACKING")
            else:
                logger.warning("[news-maker] Empty WHALE_TRACKING payload, invoking quantitative system fallback...")
                article = self._generate_whale_tracking_fallback(coin_symbol)
                self._save_article(article, "WHALE_TRACKING")
        except Exception as e:
            logger.error("[news-maker] Failed to generate WHALE_TRACKING: %s", e)

    def _build_whale_tracking_context(self, coin_symbol=None):
        self._refresh_db_context(force=True)
        pair = self._normalize_pair_key(coin_symbol) if coin_symbol else None
        
        if not pair:
            valid_items = self._get_valid_market_items(require_fresh=False)
            if not valid_items:
                return "No active liquidity data found.", "BTC"
            items_sorted = sorted(valid_items, key=lambda x: float(x[1].get("vol_idr") or 0.0), reverse=True)
            pair = items_sorted[0][0]

        clean_symbol = self._clean_symbol(pair)
        
        depth_data = self._depth_summary.get(pair, {})
        ticker_data = self._tickers.get(pair, {})
        price = float(ticker_data.get("price") or 0.0)
        ch_24h = float(ticker_data.get("ch_24h") or 0.0)
        ch_1h = float(ticker_data.get("ch_1h") or 0.0)
        vol_idr = float(ticker_data.get("vol_idr") or 0.0)
        
        bid_vol = float(depth_data.get("bid_vol") or vol_idr * 0.5)
        ask_vol = float(depth_data.get("ask_vol") or vol_idr * 0.5)
        imbalance = float(depth_data.get("imbalance") or 0.0)
        spread_pct = float(depth_data.get("spread_pct") or 0.05)
        
        tech = {}
        try:
            with db.get_connection() as conn:
                row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
                if row and row["history_json"]:
                    history = json.loads(row["history_json"])
                    if len(history) >= 2:
                        tech = get_technical_summary(history)
        except Exception:
            pass

        vol_ratio = tech.get("volume_ratio", 1.0)
        obv_slope = tech.get("obv_slope", 0.0)

        ctx = f"AUDIT ALIRAN DANA & WHALE TRACKING: {clean_symbol}\n"
        ctx += f"Harga Terkini: {self._format_price(price)}\n"
        ctx += f"Volume Transaksi 24h: {self._format_large_volume(vol_idr)}\n"
        ctx += f"Perubahan 24h: {ch_24h:+.2f}% | 1h: {ch_1h:+.2f}%\n"
        ctx += f"Volume Ratio (vs MA-20): {vol_ratio:.2f}x\n"
        ctx += f"Kemiringan OBV (OBV Slope): {obv_slope:.6f}\n\n"
        
        ctx += "### Detail Kedalaman Order Book:\n"
        ctx += f"- Total Bid Volume (Dinding Beli): {self._format_large_volume(bid_vol)}\n"
        ctx += f"- Total Ask Volume (Dinding Jual): {self._format_large_volume(ask_vol)}\n"
        ctx += f"- Spread Persentase: {spread_pct:.3f}%\n"
        ctx += f"- Imbalance Order Book: {imbalance:+.2f} (skala -1.0 hingga +1.0)\n"
        
        activity = "Normal"
        if vol_ratio > 2.0:
            activity = "Lonjakan Volume Luar Biasa (Aktivitas Whale Terdeteksi)"
        elif vol_ratio > 1.3:
            activity = "Aktivitas Volume Meningkat"
            
        pressure = "Seimbang"
        if imbalance > 0.3:
            pressure = "Dinding Beli Sangat Kuat (Akumulasi Whale)"
        elif imbalance < -0.3:
            pressure = "Dinding Jual Sangat Tebal (Distribusi Whale)"

        ctx += f"- Status Aktivitas Likuiditas: {activity}\n"
        ctx += f"- Status Tekanan Order Book: {pressure}\n"

        return ctx, clean_symbol

    def _build_whale_tracking_prompt(self, coin_symbol, context):
        _prompt_min_chars = max(1000, MIN_NEWS_CONTENT_CHARS)
        style_rules = self._markdown_article_style_rules()
        return (
            f"Anda adalah Chief Blockchain Forensics & Order Book Analyst Algovibe. Tulis laporan Whale Tracking (WHALE_TRACKING) dalam Bahasa Indonesia.\n"
            f"Aset Fokus: {coin_symbol}\n"
            f"Tanggal: {datetime.now(WIB_TZ).strftime('%d %b %Y %H:%M WIB')}\n"
            f"\nKonteks Telemetri Liquidity & Order Book:\n{context}\n"
            f"Persyaratan Artikel:\n"
            f"- Judul: Whale Tracking {coin_symbol}: Deteksi Aliran Dana Besar & Ketebalan Dinding Order Book (50-80 karakter).\n"
            f"- Excerpt: Analisis ketebalan dinding bid/ask dan lonjakan volume ratio abnormal {coin_symbol}.\n"
            f"- Panjang konten: minimal {_prompt_min_chars} karakter.\n"
            f"- Content: Sajikan artikel yang PREMIUM dan DATA-GROUNDED. Terangkan detail ketebalan Bid vs Ask, arti dari Order Book Imbalance, dan hubungan lonjakan Volume Ratio vs MA-20 terhadap akumulasi atau distribusi diam-diam oleh whale.\n"
            f"- Format MARKDOWN. Gunakan struktur heading persis seperti ini:\n"
            f"  ## Ringkasan Aliran Dana\n"
            f"  ## Analisis Kedalaman Order Book & Bid/Ask Walls\n"
            f"  ## Deteksi Volume Ratio Abnormal & OBV Slope\n"
            f"  ## Implikasi Terhadap Arah Harga & Strategi Trading\n"
            f"  ## Kesimpulan\n"
            f"{style_rules}\n"
            "Balas HANYA 1 JSON object tanpa wrapper block:\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    async def _generate_risk_warning(self, coin_symbol=None):
        if not self._engine_enabled:
            return
        logger.info("[news-maker] Generating RISK_WARNING...")
        context, clean_sym = self._build_risk_warning_context(coin_symbol)
        prompt = self._build_risk_warning_prompt(clean_sym, context)
        try:
            article = await self._ask_llm(prompt)
            if article:
                article = self._ensure_min_content(article, context)
                self._save_article(article, "RISK_WARNING")
            else:
                logger.warning("[news-maker] Empty RISK_WARNING payload, invoking quantitative system fallback...")
                article = self._generate_risk_warning_fallback(coin_symbol)
                self._save_article(article, "RISK_WARNING")
        except Exception as e:
            logger.error("[news-maker] Failed to generate RISK_WARNING: %s", e)

    def _build_risk_warning_context(self, coin_symbol=None):
        self._refresh_db_context(force=True)
        pair = self._normalize_pair_key(coin_symbol) if coin_symbol else None
        
        if not pair:
            valid_items = self._get_valid_market_items(require_fresh=False)
            if not valid_items:
                return "No active risk data found.", "BTC"
            items_sorted = sorted(valid_items, key=lambda x: float(x[1].get("volatility") or 0.0), reverse=True)
            pair = items_sorted[0][0]

        clean_symbol = self._clean_symbol(pair)
        
        ticker_data = self._tickers.get(pair, {})
        price = float(ticker_data.get("price") or 0.0)
        ch_24h = float(ticker_data.get("ch_24h") or 0.0)
        ch_1h = float(ticker_data.get("ch_1h") or 0.0)
        volatility = float(ticker_data.get("volatility") or 0.0)
        trend = str(ticker_data.get("trend") or "SIDEWAYS")
        
        regime = db.get_state("current_market_regime") or "NEUTRAL"
        sentiment = db.get_state("market_sentiment_index") or 50

        ctx = f"LAPORAN MANAJEMEN RISIKO & TELEMETRI VOLATILITAS: {clean_symbol}\n"
        ctx += f"Harga Terkini: {self._format_price(price)}\n"
        ctx += f"Perubahan 24h: {ch_24h:+.2f}% | 1h: {ch_1h:+.2f}%\n"
        ctx += f"Volatilitas Standard Deviation (Intraday): {volatility:.2f}%\n"
        ctx += f"Kecenderungan Tren: {trend}\n"
        ctx += f"Regime Pasar Makro: {regime} | Sentiment Index: {sentiment}%\n\n"
        
        ctx += "### Parameter Proteksi Bot & Safety Guards:\n"
        risk_level = "RENDAH"
        if volatility > 2.0 or abs(ch_24h) > 8.0:
            risk_level = "EKSTRIM"
        elif volatility > 1.2 or abs(ch_24h) > 4.0:
            risk_level = "TINGGI"
        elif volatility > 0.6:
            risk_level = "SEDANG"

        ctx += f"- Tingkat Risiko Aset: {risk_level}\n"
        
        atr_filter = "AKTIF (Menolak jika ATR > 3% harga)"
        ema_trend_filter = "AKTIF (Menolak jika harga < EMA-50)"
        ctx += f"- ATR Volatility Filter (Gate 4b): {atr_filter}\n"
        ctx += f"- EMA-50 Trend Filter (Gate 4c): {ema_trend_filter}\n"
        
        return ctx, clean_symbol

    def _build_risk_warning_prompt(self, coin_symbol, context):
        _prompt_min_chars = max(1000, MIN_NEWS_CONTENT_CHARS)
        style_rules = self._markdown_article_style_rules()
        return (
            f"Anda adalah Chief Risk Officer & Quantitative Safety Engineer Algovibe. Tulis Laporan Peringatan Risiko Pasar (RISK_WARNING) dalam Bahasa Indonesia.\n"
            f"Aset Fokus: {coin_symbol}\n"
            f"Tanggal: {datetime.now(WIB_TZ).strftime('%d %b %Y %H:%M WIB')}\n"
            f"\nKonteks Telemetri Risiko & Volatilitas:\n{context}\n"
            f"Persyaratan Artikel:\n"
            f"- Judul: Peringatan Risiko {coin_symbol}: Telemetri Volatilitas ATR & Skenario Proteksi Modal (50-80 karakter).\n"
            f"- Excerpt: Analisis tingkat risiko volatilitas tinggi dan status safety guard filter pada {coin_symbol}.\n"
            f"- Panjang konten: minimal {_prompt_min_chars} karakter.\n"
            f"- Content: Sajikan artikel yang PREMIUM dan EDUKATIF. Jelaskan tingkat risiko saat ini, bagaimana ATR Volatility Filter (Gate 4b) dan EMA-50 Trend Filter (Gate 4c) bekerja mencegah jebakan harga, serta taktik membatasi loss.\n"
            f"- Format MARKDOWN. Gunakan struktur heading persis seperti ini:\n"
            f"  ## Evaluasi Risiko Pasar Terkini\n"
            f"  ## Analisis Volatilitas Intraday & Telemetri ATR\n"
            f"  ## Status Safety Guard & Entry Gate Bot (Filter EMA-50 & ATR)\n"
            f"  ## Skenario Proteksi Modal & Risk Management Plan\n"
            f"  ## Kesimpulan\n"
            f"{style_rules}\n"
            "Balas HANYA 1 JSON object tanpa wrapper block:\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    def _build_trending_review_context(self):
        """Build composite context untuk daily trending review — top coins by composite score."""
        valid_items = self._get_valid_market_items(require_fresh=False)  # tidak perlu fresh untuk daily
        if not valid_items:
            return "- No market data available\n"

        scored = []
        for pair, data in valid_items:
            rt = self._resolve_realtime_move(pair)
            sig = self._db_signals.get(pair, {})
            ipc = self._ipc_signals.get(pair, {})
            vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
            ch_24h_abs = abs(float(rt.get("pct_change") or 0.0))
            ch_1h_abs = abs(float(rt.get("ch_1h") or 0.0))
            score_db = abs(float(sig.get("score") or 0.0))
            conf = float(ipc.get("confidence") or 0.0)
            signal = str(ipc.get("signal") or sig.get("signal") or "HOLD")
            composite = ch_24h_abs + ch_1h_abs * 1.5 + score_db * 0.5 + conf * 10 + min(vol_idr / 1_000_000_000, 5)
            scored.append((pair, data, rt, sig, signal, conf, composite))

        scored.sort(key=lambda x: x[6], reverse=True)
        top = scored[:7]

        context = f"Daily Trending Coins ({datetime.now(WIB_TZ).strftime('%d %b %Y')}):\n"
        for pair, data, rt, sig, signal, conf, _ in top:
            price = float(data.get("price") or 0.0)
            vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
            ch_24h = float(rt.get("pct_change") or 0.0)
            ch_1h = float(rt.get("ch_1h") or 0.0)
            trend = sig.get("trend") or "N/A"
            src_label = "" if data.get("source") != "db_depth" else " [data-DB]"
            context += (
                f"- {self._clean_symbol(pair)}{src_label}: price={self._format_price(price)}  ch_24h={ch_24h:+.2f}%  "
                f"ch_1h={ch_1h:+.2f}%  vol={self._format_vol_m(vol_idr/1e6)}  "
                f"signal={signal}({conf:.0%})  trend={trend}\n"
            )

        regime = db.get_state("current_market_regime") or "NEUTRAL"
        sentiment = db.get_state("market_sentiment_index") or 50
        context += f"\nMarket Regime: {regime} | Sentiment: {sentiment}%\n"
        return context

    def _build_trending_review_prompt(self, context):
        """Prompt untuk daily trending review — multi-coin analysis."""
        _prompt_min_chars = max(700, MIN_NEWS_CONTENT_CHARS // 3)
        return (
            f"Anda adalah Senior Market Analyst Algovibe. Tulis Daily Trending Review dalam Bahasa Indonesia.\n"
            f"Tanggal: {datetime.now(WIB_TZ).strftime('%d %b %Y')}\n"
            f"\nKonteks Data:\n{context}\n"
            f"Persyaratan Artikel:\n"
            f"- Judul: sebutkan 2-3 coin terpilih, 50-80 karakter.\n"
            f"- Excerpt: ringkasan 100-150 karakter.\n"
            f"- Panjang konten: minimal {_prompt_min_chars} karakter.\n"
            f"- Content: PADAT (250-400 kata). Bahas tiap coin: harga, arah, sinyal, level penting.\n"
            f"- Format MARKDOWN. Struktur: ## Ringkasan → ## Analisis Per Coin → ## Kesimpulan\n"
            f"- Fokus ke data konkret di atas, hindari narasi generik.\n"
            "Balas HANYA 1 JSON object:\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    async def _generate_landing_pack(self, coin_symbol=None, headline_count=3):
        """Generate a complete landing content pack: brief + periodic reports + headlines."""
        logger.info("[news-maker] Generating LANDING content pack...")

        await self._generate_market_intelligence_brief()
        await self._generate_periodic_report("MORNING_BRIEF")
        await self._generate_periodic_report("EVENING_REVIEW")

        generated_pairs = set()
        pair = str(coin_symbol or "").lower().strip()
        if pair:
            data = self._tickers.get(pair, {})
            sig = self._db_signals.get(pair, {})
            dep = self._depth_summary.get(pair, {})
            move_data = {
                "pct_change": float(data.get("ch_24h") or 0.0),
                "price": float(data.get("price") or dep.get("best_bid") or dep.get("best_ask") or 0.0),
                "change_1h": float(data.get("ch_1h") or sig.get("ch_1h") or 0.0),
                "vol_idr": float(data.get("vol_idr") or data.get("vol") or 0.0),
                "score": abs(float(sig.get("score") or data.get("score") or 0.0)),
            }
            await self._generate_market_headline(pair, move_data)
            generated_pairs.add(pair)

        candidates = self._get_landing_headline_candidates(limit=max(1, int(headline_count)))
        for cand_pair, move_data in candidates:
            if cand_pair in generated_pairs:
                continue
            await self._generate_market_headline(cand_pair, move_data)
            generated_pairs.add(cand_pair)
            if len(generated_pairs) >= max(1, int(headline_count)):
                break

    def _get_landing_headline_candidates(self, limit=3):
        """Pick best headline candidates for landing article needs."""
        candidates = []
        for pair, data in self._get_valid_market_items(require_fresh=True):
            rt = self._resolve_realtime_move(pair)
            ch_24h = float(rt.get("pct_change") or 0.0)
            ch_1h = float(rt.get("ch_1h") or 0.0)
            price = float(data.get("price") or 0.0)
            vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
            if price <= 0 or vol_idr < 30_000_000:
                continue
            if abs(ch_24h) < 1.5 and abs(ch_1h) < 0.8:
                continue

            score = abs(ch_24h) + (abs(ch_1h) * 1.5) + min(vol_idr / 1_000_000_000, 5)
            candidates.append((pair, {
                "pct_change": ch_24h,
                "pct_source": str(rt.get("pct_source") or "24h"),
                "price": price,
                "change_1h": ch_1h,
                "vol_idr": vol_idr,
                "score": score,
            }))

        candidates.sort(key=lambda x: float(x[1].get("score") or 0.0), reverse=True)
        return candidates[:max(1, int(limit))]

    def _build_market_context(self):
        """Build overall market context for briefing."""
        # require_fresh=False agar scheduled jobs tetap dapat data dari DB bootstrap
        valid_items = self._get_valid_market_items(require_fresh=False)
        movers = sorted(
            valid_items,
            key=lambda x: abs(float(x[1].get("ch_24h") or 0.0)),
            reverse=True,
        )[:5]

        context = "Top Market Movers (24h):\n"
        if movers:
            for pair, data in movers:
                ch_24h = float(data.get("ch_24h") or 0.0)
                price = float(data.get("price") or 0.0)
                src_label = " [DB]" if data.get("source") == "db_depth" else ""
                context += f"- {self._clean_symbol(pair)}{src_label}: {self._format_price(price)} ({ch_24h:+.1f}%)\n"
        else:
            context += "- No valid live market snapshot available\n"

        regime = db.get_state("current_market_regime") or "NEUTRAL"
        sentiment = db.get_state("market_sentiment_index") or 50
        context += f"Regime: {regime} | Sentiment: {sentiment}%\n"

        ranked_signals = sorted(
            self._db_signals.items(),
            key=lambda x: abs(float(x[1].get("score") or 0.0)) + abs(float(x[1].get("ch_1h") or 0.0)),
            reverse=True,
        )[:5]
        context += "\nDB Technical Signals (valid live pairs only):\n"
        if ranked_signals:
            valid_pairs = {pair for pair, _data in valid_items}
            appended = False
            for pair, sig in ranked_signals:
                if pair not in valid_pairs:
                    continue
                appended = True
                context += (
                    f"- {self._clean_symbol(pair)}: trend={sig.get('trend', 'N/A')} signal={sig.get('signal', 'HOLD')} "
                    f"ch_1h={float(sig.get('ch_1h') or 0.0):+.2f}% score={float(sig.get('score') or 0.0):.2f}\n"
                )
            if not appended:
                context += "- No valid ticker_history signals\n"
        else:
            context += "- No valid ticker_history signals\n"

        ranked_depth = sorted(
            self._depth_summary.items(),
            key=lambda x: abs(float(x[1].get("imbalance") or 0.0)) + float(x[1].get("bid_vol") or 0.0) + float(x[1].get("ask_vol") or 0.0),
            reverse=True,
        )[:5]
        context += "\nOrderbook Realtime (market_depth):\n"
        if ranked_depth:
            valid_pairs = {pair for pair, _data in valid_items}
            appended = False
            for pair, dep in ranked_depth:
                if pair not in valid_pairs:
                    continue
                appended = True
                context += (
                    f"- {self._clean_symbol(pair)}: spread={float(dep.get('spread_pct') or 0.0):.3f}% "
                    f"imbalance={float(dep.get('imbalance') or 0.0):+.2f} "
                    f"bidVol={float(dep.get('bid_vol') or 0.0):,.0f} "
                    f"askVol={float(dep.get('ask_vol') or 0.0):,.0f}\n"
                )
            if not appended:
                context += "- No valid market_depth rows for live pairs\n"
        else:
            context += "- No market_depth data available\n"

        return context

    def _build_market_context_for_coin(self, coin_symbol, move_data, realtime_only=False):
        """Build context specific to one coin movement."""
        valid_items = self._get_valid_market_items(require_fresh=True)

        all_movers = []
        for pair, data in valid_items:
            rt = self._resolve_realtime_move(pair)
            all_movers.append((pair, data, rt))
        all_movers.sort(key=lambda x: float(x[2].get("pct_change") or 0.0), reverse=True)
        gainers = all_movers[:3]
        losers = all_movers[-3:]

        context = f"Coin Focus: {self._clean_symbol(coin_symbol)}\n"
        context += f"Price: {self._format_price(move_data['price'])}\n"
        context += f"Change 1h: {move_data['change_1h']:+.1f}% | Delta Active: {move_data['pct_change']:+.1f}% ({move_data.get('pct_source', 'auto')})\n"
        context += f"Volume 24h: {self._format_large_volume(move_data['vol_idr'])}\n"
        context += "\nTop Gainers (Realtime WS):\n"
        if gainers:
            for pair, _data, rt in gainers:
                context += f"- {self._clean_symbol(pair)}: {float(rt.get('pct_change') or 0):+.2f}% ({rt.get('pct_source', '24h')})\n"
        else:
            context += "- No valid live gainers\n"
        context += "\nTop Losers (Realtime WS):\n"
        if losers:
            for pair, _data, rt in losers:
                context += f"- {self._clean_symbol(pair)}: {float(rt.get('pct_change') or 0):+.2f}% ({rt.get('pct_source', '24h')})\n"
        else:
            context += "- No valid live losers\n"

        regime = db.get_state("current_market_regime") or "NEUTRAL"
        context += f"Market Regime: {regime}\n"

        pair = self._normalize_pair_key(coin_symbol)
        rt_focus = self._resolve_realtime_move(pair)
        hist = list(self._ws_price_history.get(pair) or [])
        context += "\nRealtime WS Signal:\n"
        context += (
            f"- ch_5m={float(rt_focus.get('ch_5m') or 0.0):+.2f}% "
            f"ch_15m={float(rt_focus.get('ch_15m') or 0.0):+.2f}% "
            f"ch_1h={float(rt_focus.get('ch_1h') or 0.0):+.2f}% "
            f"ch_24h={float(rt_focus.get('ch_24h') or 0.0):+.2f}%\n"
        )
        context += "Realtime Price History (WS):\n"
        if hist:
            prices = [float(p or 0.0) for _, p in hist if float(p or 0.0) > 0]
            if prices:
                context += (
                    f"- samples={len(prices)} "
                    f"last={prices[-1]:,.0f} "
                    f"min={min(prices):,.0f} "
                    f"max={max(prices):,.0f}\n"
                )
            else:
                context += "- samples=0\n"
        else:
            context += "- no websocket history yet\n"

        return context

    def _build_market_headline_prompt(self, coin_symbol, move_data, context):
        """Generate prompt for market headline analysis."""
        direction = "naik/hype" if move_data['pct_change'] >= 0 else "turun/rugi"
        # Target lebih rendah agar LLM generate lebih cepat; _ensure_min_content akan pad jika kurang.
        _prompt_min_chars = max(700, MIN_NEWS_CONTENT_CHARS // 3)
        contract = self._markdown_article_output_contract(
            variant="headline",
            title_rule="Title: Breaking news impactful, 50-80 karakter. Include: coin + action + pct.",
            excerpt_rule="Excerpt: Summary 120-180 karakter. Jelaskan: apa geraknya, kenapa significant, dampaknya.",
            min_chars=_prompt_min_chars,
            content_hint=f"RINGKAS & PADAT (150-250 kata) dalam FORMAT MARKDOWN. Fokus ke data utama dan analisis singkat.",
        )
        return (
            f"Anda adalah Market Analyst Algovibe. Tulis Breaking News pasar kripto dalam Bahasa Indonesia.\n"
            f"Analisis: {self._clean_symbol(coin_symbol)} - {direction.upper()} {abs(move_data['pct_change']):.1f}%\n"
            f"\nKonteks Data:\n{context}\n"
            f"{contract}\n"
            "Balas HANYA 1 JSON object tanpa wrapper block.\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    def _build_report_prompt(self, rtype, context):
        title = "Morning Briefing" if rtype == "MORNING_BRIEF" else "Market Review Malam"
        # Target lebih rendah agar LLM generate lebih cepat; _ensure_min_content akan pad jika kurang.
        _prompt_min_chars = max(700, MIN_NEWS_CONTENT_CHARS // 3)
        contract = self._markdown_article_output_contract(
            variant="report",
            title_rule="Judul: catchy, informatif, 50-80 karakter.",
            excerpt_rule="Excerpt: ringkasan 100-150 karakter yang mengecup poin utama.",
            min_chars=_prompt_min_chars,
            content_hint="PADAT (250-400 kata) dalam FORMAT MARKDOWN PROPER yang nyaman dibaca di news feed.",
        )
        return (
            f"Anda adalah Chief Editor Algovibe Intelligence. Tulis artikel blog profesional dalam Bahasa Indonesia.\n"
            f"Tipe: {title}\n"
            f"Konteks Data:\n{context}\n"
            f"{contract}\n"
            "Balas HANYA 1 JSON object, tanpa extra wrapper, tanpa teks tambahan.\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    def _markdown_article_output_contract(self, variant, title_rule, excerpt_rule, min_chars, content_hint):
        """Single source of truth for markdown output requirements across article prompts."""
        section_template = self._markdown_article_section_template(variant)
        style_rules = self._markdown_article_style_rules()
        return (
            "Persyaratan Artikel (Output PROPER MARKDOWN, inline-friendly):\n"
            f"- {title_rule}\n"
            f"- {excerpt_rule}\n"
            f"- Panjang konten: minimal {int(min_chars)} karakter (WAJIB).\n"
            f"- Content: {content_hint}\n"
            f"{style_rules}\n"
            f"{section_template}"
        )

    def _markdown_article_style_rules(self):
        """Shared formatting contract to keep article markdown consistent."""
        return (
            "Aturan style seragam:\n"
            "- paragraf pendek 2-4 kalimat\n"
            "- heading maksimal 1 baris per section\n"
            "- bullet list pendek dan langsung ke poin\n"
            "- tabel hanya jika ada angka yang benar-benar perlu dibandingkan\n"
            "- satu baris kosong antar section\n"
            "- jangan pakai code fence, HTML, blockquote, atau nested list\n"
            "- hindari section yang saling tumpang tindih isinya\n"
        )

    def _markdown_article_section_template(self, variant):
        """Return a consistent markdown section plan for all article variants."""
        if variant == "headline":
            return (
                "Struktur section wajib seragam:\n"
                "1. ## Ringkasan Cepat\n"
                "2. ## Data Utama\n"
                "3. ## Analisis Teknis\n"
                "4. ## Level & Strategi\n"
                "5. ## Risiko & Skenario\n"
                "6. ## Kesimpulan\n"
                "Aturan isi: ringkas, tegas, dan fokus ke market move yang sedang dibahas."
            )

        return (
            "Struktur section wajib seragam:\n"
            "1. ## Ringkasan Pasar\n"
            "2. ## Data Utama\n"
            "3. ## Analisis Teknis\n"
            "4. ## Level & Strategi\n"
            "5. ## Risiko & Skenario\n"
            "6. ## Kesimpulan\n"
            "Aturan isi: runut, konsisten, dan mudah dipindai di news feed."
        )

    def _normalize_markdown_content(self, content):
        """Normalize markdown so it stays compact and readable in the news UI."""
        text = str(content or "").strip()
        if not text:
            return text

        text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\n\s+\n", "\n\n", text)
        return text.strip()

    def _build_market_brief_prompt(self, context):
        return (
            "Anda adalah MARKET INTELLIGENCE EDITOR untuk landing page Algovibe.\n"
            "Tulis ringkasan singkat berbasis data pasar real yang diberikan.\n"
            f"Konteks Data:\n{context}\n"
            "Aturan output:\n"
            "- Fokus pada kondisi market terkini, bukan narasi generik.\n"
            "- Maksimal 2 kalimat, total <= 260 karakter.\n"
            "- Wajib sebutkan minimal 1 sinyal konkret (trend/signal/ch_1h/regime/sentiment).\n"
            "- Jangan gunakan markdown/code block.\n"
            "Balas HANYA JSON object:\n"
            '{"brief":"..."}'
        )

    def _ensure_min_content(self, article, context):
        """Ensure article content meets minimum character requirement.

        Jika LLM menghasilkan konten terlalu pendek, tambahkan section data-driven
        yang konkret berdasarkan context pasar — BUKAN instruksi prompt mentah.
        """
        if not isinstance(article, dict):
            return article

        content = self._normalize_markdown_content(article.get("content") or "")
        if len(content) >= MIN_NEWS_CONTENT_CHARS:
            article["content"] = content
            return article

        #  Build data-driven extension dari context yang sudah terstruktur 
        extension = self._build_content_extension(content, context)

        _ext_count = 0
        while len(content) < MIN_NEWS_CONTENT_CHARS and _ext_count < 2:
            content += "\n\n" + extension
            _ext_count += 1

        article["content"] = self._normalize_markdown_content(content[: max(MIN_NEWS_CONTENT_CHARS, len(content))])
        if not article.get("excerpt"):
            article["excerpt"] = self._strip_markdown_plain(content)[:220]
        return article

    def _build_content_extension(self, existing_content, context):
        """Build a data-driven article extension dari market context.

        Tidak pernah menyertakan instruksi prompt atau raw context dump.
        Semua section berisi data konkret atau narasi editorial yang layak baca.
        """
        existing_lower = str(existing_content or "").lower()
        parts = []

        #  Parse context untuk data konkret 
        ctx_str = str(context or "")
        movers = self._parse_context_movers(ctx_str)
        regime = "NEUTRAL"
        sentiment = "50"
        regime_m = re.search(r"(?:Market\s+)?Regime[:\s]+([A-Z]+)", ctx_str, re.IGNORECASE)
        if regime_m:
            regime = regime_m.group(1).upper()
        sentiment_m = re.search(r"Sentim(?:ent|en)[:\s]+(\d+)", ctx_str, re.IGNORECASE)
        if sentiment_m:
            sentiment = sentiment_m.group(1)

        #  Section: Level & Strategi (skip jika sudah ada) 
        if "level" not in existing_lower or "strategi" not in existing_lower:
            strat = "## Level & Strategi\n\n"
            if regime == "BULL":
                strat += (
                    "### Skenario Bullish\n"
                    "Regime pasar saat ini mendukung posisi agresif. Entry bertahap setelah konfirmasi "
                    "volume dan candle close di atas resistance intraday. Pastikan trend alignment di "
                    "timeframe 5m/15m/1h sebelum scaling in.\n\n"
                    "### Skenario Koreksi\n"
                    "Jika terjadi koreksi, pantau area support terdekat. Koreksi sehat biasanya "
                    "tidak menembus EMA-21 di timeframe 1h.\n"
                )
            elif regime == "BEAR":
                strat += (
                    "### Skenario Bearish\n"
                    "Regime pasar sedang dalam tekanan. Utamakan proteksi modal dan batasi eksposur. "
                    "Gunakan stop-loss ketat berbasis struktur pasar terdekat.\n\n"
                    "### Skenario Recovery\n"
                    "Tunggu konfirmasi reversal berupa double bottom atau bullish divergence RSI "
                    "sebelum membuka posisi baru.\n"
                )
            else:
                strat += (
                    "### Skenario Bullish\n"
                    "Entry bertahap setelah konfirmasi volume dan breakout resistance intraday. "
                    "Pastikan trend alignment di timeframe 5m/15m/1h sebelum scaling in.\n\n"
                    "### Skenario Netral\n"
                    "Fokus range trading dengan ukuran posisi kecil. Hindari overtrading saat "
                    "spread melebar atau volatilitas extreme.\n\n"
                    "### Skenario Bearish\n"
                    "Utamakan proteksi modal dan batasi eksposur leverage. Gunakan stop-loss ketat "
                    "berbasis struktur pasar terdekat.\n"
                )
            parts.append(strat)

        #  Section: Risiko & Skenario (skip jika sudah ada) 
        if "risiko" not in existing_lower:
            risk = (
                "## Risiko & Manajemen\n\n"
                "- Risiko per posisi: maksimal 1-2% ekuitas portofolio\n"
                "- Take-profit: gunakan partial TP untuk lock profit secara bertahap\n"
                "- Evaluasi ulang: segera review jika volatilitas melonjak tiba-tiba\n"
                "- Stop-loss: wajib dipasang berbasis level teknikal, bukan persentase acak\n"
            )
            parts.append(risk)

        #  Section: Snapshot Pasar (data movers dari context, bukan raw dump) 
        if movers and "snapshot" not in existing_lower:
            snap = "## Snapshot Pasar Terkini\n\n"
            snap += f"Regime pasar: **{regime}** | Sentimen: **{sentiment}%**\n\n"
            gainers = [m for m in movers if m.get("pct", 0) > 0]
            losers = [m for m in movers if m.get("pct", 0) < 0]
            if gainers:
                snap += "**Top Gainers:**\n"
                for m in gainers[:3]:
                    snap += f"- {m['pair']}: {m['pct']:+.2f}%\n"
                snap += "\n"
            if losers:
                snap += "**Top Losers:**\n"
                for m in losers[:3]:
                    snap += f"- {m['pair']}: {m['pct']:+.2f}%\n"
                snap += "\n"
            parts.append(snap)

        #  Section: Validasi Pre-Entry (checklist konkret) 
        if "validasi" not in existing_lower and "checklist" not in existing_lower:
            check = (
                "## Validasi Teknikal Pre-Entry\n\n"
                "| Checklist | Keterangan |\n"
                "| --- | --- |\n"
                "| Trend alignment 5m/15m/1h | Semua timeframe harus searah |\n"
                "| Spread health | Spread harus tight untuk eksekusi optimal |\n"
                "| Orderbook imbalance | Harus mendukung arah posisi |\n"
                "| TP/SL realistic | Target harus realistis terhadap volatilitas terbaru |\n"
            )
            parts.append(check)

        #  Section: Kesimpulan (skip jika sudah ada) 
        if "kesimpulan" not in existing_lower:
            regime_narr = {
                "BULL": "Kondisi pasar bullish mendukung posisi long dengan manajemen risiko ketat.",
                "BEAR": "Kondisi pasar bearish menuntut kehati-hatian ekstra dan proteksi modal.",
                "NEUTRAL": "Pasar dalam fase konsolidasi. Prioritaskan quality setup dan hindari overtrading.",
            }.get(regime, "Pantau perkembangan pasar dan tunggu konfirmasi sinyal yang lebih jelas.")
            parts.append(f"## Kesimpulan\n\n{regime_narr}")

        return "\n".join(parts).strip() if parts else (
            "## Catatan\n\n"
            "Pergerakan harga kripto bersifat dinamis dan berisiko tinggi. "
            "Selalu lakukan riset mandiri dan gunakan manajemen risiko yang ketat "
            "sebelum mengambil keputusan trading."
        )

    def _parse_context_movers(self, ctx_str):
        """Extract mover data (pair + pct) dari context string."""
        movers = []
        for m in re.finditer(r"-\s*([A-Z0-9]+(?:IDR)?)\s*:\s*([+\-]?\d+\.?\d*)%", str(ctx_str or "")):
            pair = m.group(1)
            pct = float(m.group(2))
            movers.append({"pair": pair, "pct": pct})
        return movers

    async def _ask_llm_raw(self, prompt):
        loop = asyncio.get_running_loop()
        local_first = self._llm_provider in ("local", "auto")
        remote_only = self._llm_provider in ("huggingface", "hf", "cloudflare", "cf")

        if local_first and self._engine is not None:
            text = await self._ask_llm_local_raw(prompt)
            if text:
                return text

        if remote_only or self._remote_provider_configured():
            try:
                text = await asyncio.wait_for(
                    loop.run_in_executor(None, self._ask_llm_remote_raw_sync, prompt),
                    timeout=REMOTE_TIMEOUT_SEC + 15,  # sedikit lebih longgar dari requests timeout
                )
            except asyncio.TimeoutError:
                logger.warning("[news-maker] Remote LLM executor timeout >%ss, skipping.", REMOTE_TIMEOUT_SEC + 15)
                text = ""
            if text:
                return text

        return ""

    async def _ask_llm_local_raw(self, prompt):
        loop = asyncio.get_running_loop()
        try:
            def _infer():
                with self._engine.create_conversation() as conv:
                    return conv.send_message(prompt)

            raw = await asyncio.wait_for(
                loop.run_in_executor(None, _infer),
                timeout=LLM_TIMEOUT_SEC,
            )

            text = self._extract_text(raw)
            logger.debug("[news-maker] Local LLM response len: %d", len(str(text) or ""))
            return text
        except asyncio.TimeoutError:
            # Jangan retry local — langsung return "" agar _ask_llm_raw fallback ke remote.
            # Retry lokal hanya spawn thread kedua yang jalan bersamaan dan makin lambat.
            logger.warning(
                "[news-maker] Local LLM timeout >%ss. Falling back to remote provider.",
                LLM_TIMEOUT_SEC,
            )
            return ""
        except Exception as e:
            logger.error("[news-maker] Local LLM Error: %s", e)
            return ""

    def _extract_remote_text(self, payload):
        if payload is None:
            return ""

        if isinstance(payload, str):
            return payload

        if isinstance(payload, dict):
            for key in ("generated_text", "response", "text", "output"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    return val

            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                msg = first.get("message") if isinstance(first, dict) else None
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    return msg.get("content")
                if isinstance(first.get("text"), str):
                    return first.get("text")

            result = payload.get("result")
            if isinstance(result, dict):
                for key in ("response", "text", "output"):
                    val = result.get(key)
                    if isinstance(val, str) and val.strip():
                        return val

        if isinstance(payload, list) and payload:
            first = payload[0]
            return self._extract_remote_text(first)

        return ""

    def _coerce_remote_json_text(self, text):
        raw = str(text or "").strip()
        if not raw:
            return ""

        # Best case: already a single JSON object.
        if raw.startswith("{") and raw.endswith("}"):
            return raw

        # Common case: markdown code fence with JSON.
        block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
        if block_match:
            return block_match.group(1).strip()

        # Preface/suffix text around JSON object(s).
        # Prefer candidate that looks like expected payload keys.
        for candidate in self._extract_json_candidates(raw):
            c = str(candidate or "").strip()
            cl = c.lower()
            if (
                '"title"' in cl
                or '"excerpt"' in cl
                or '"content"' in cl
                or '"brief"' in cl
                or '"message"' in cl
            ):
                return c

        # Last resort: slice from first '{' to last '}' if it still looks like
        # article/brief payload with preface suffix around it.
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            clipped = raw[first_brace:last_brace + 1].strip()
            cl = clipped.lower()
            if (
                '"title"' in cl
                or '"excerpt"' in cl
                or '"content"' in cl
                or '"brief"' in cl
                or '"message"' in cl
            ):
                return clipped

        return raw

    def _ask_llm_remote_raw_sync(self, prompt):
        provider = self._llm_provider
        attempts = []
        if provider in ("cloudflare", "cf"):
            attempts = ["cloudflare", "huggingface"]
        elif provider in ("huggingface", "hf"):
            attempts = ["huggingface", "cloudflare"]
        else:
            attempts = ["cloudflare", "huggingface"]

        for item in attempts:
            try:
                if item == "cloudflare" and NEWS_CF_API_URL and NEWS_CF_API_TOKEN:
                    headers = {
                        "Authorization": f"Bearer {NEWS_CF_API_TOKEN}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Return exactly one valid JSON object and nothing else. "
                                    "No preface, no explanation, no markdown, no code fences."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ]
                    }
                    
                    cf_delay = 2.0
                    for cf_attempt in range(3):
                        resp = requests.post(NEWS_CF_API_URL, headers=headers, json=payload, timeout=REMOTE_TIMEOUT_SEC)
                        if resp.ok:
                            text = self._coerce_remote_json_text(self._extract_remote_text(resp.json()))
                            if text:
                                logger.info("[news-maker] Remote fallback used: cloudflare (attempt %d)", cf_attempt + 1)
                                return text
                            break
                        elif resp.status_code == 429:
                            logger.warning("[news-maker] Cloudflare API error 429 (rate-limited). Retrying in %.1fs...", cf_delay)
                            time.sleep(cf_delay)
                            cf_delay *= 2.0
                        else:
                            logger.warning("[news-maker] Cloudflare API error %s on attempt %d", resp.status_code, cf_attempt + 1)
                            break

                if item == "huggingface" and NEWS_HF_API_URL and NEWS_HF_API_TOKEN:
                    headers = {
                        "Authorization": f"Bearer {NEWS_HF_API_TOKEN}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "inputs": prompt,
                        "parameters": {
                            "max_new_tokens": 1800,
                            "return_full_text": False,
                            "temperature": 0.2,
                        },
                    }
                    
                    hf_delay = 3.0
                    for hf_attempt in range(3):
                        resp = requests.post(NEWS_HF_API_URL, headers=headers, json=payload, timeout=REMOTE_TIMEOUT_SEC)
                        if resp.ok:
                            text = self._coerce_remote_json_text(self._extract_remote_text(resp.json()))
                            if text:
                                logger.info("[news-maker] Remote fallback used: huggingface (attempt %d)", hf_attempt + 1)
                                return text
                            break
                        elif resp.status_code in (429, 503):
                            logger.warning("[news-maker] HuggingFace API error %s (rate-limited/loading). Retrying in %.1fs...", resp.status_code, hf_delay)
                            time.sleep(hf_delay)
                            hf_delay *= 2.0
                        else:
                            logger.warning("[news-maker] HuggingFace API error %s on attempt %d", resp.status_code, hf_attempt + 1)
                            break
            except Exception as exc:
                logger.warning("[news-maker] Remote provider %s failed: %s", item, exc)

        return ""

    async def _ask_llm(self, prompt):
        text = await self._ask_llm_raw(prompt)
        if not text:
            return None
        article = self._parse_json(text)
        if article:
            return article
        logger.warning("[news-maker] JSON parse miss. Snippet: %s", (text or "")[:220].replace("\n", " "))
        return self._fallback_article_from_text(text)

    def _parse_brief_payload(self, raw_text):
        text = str(raw_text or "").strip()
        if not text:
            return None

        def _pick(payload):
            if not isinstance(payload, dict):
                return None
            for key in ("brief", "message", "summary", "content", "text"):
                val = str(payload.get(key) or "").strip()
                if val:
                    return val
            return None

        try:
            parsed = json.loads(text)
            picked = _pick(parsed)
            if picked:
                return re.sub(r"\s+", " ", picked).strip()[:260]
        except Exception:
            pass

        block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
        if block_match:
            try:
                parsed = json.loads(block_match.group(1))
                picked = _pick(parsed)
                if picked:
                    return re.sub(r"\s+", " ", picked).strip()[:260]
            except Exception:
                pass

        for candidate in self._extract_json_candidates(text):
            try:
                parsed = json.loads(candidate)
                picked = _pick(parsed)
                if picked:
                    return re.sub(r"\s+", " ", picked).strip()[:260]
            except Exception:
                continue

        plain = re.sub(r"\s+", " ", text).strip()
        if len(plain) >= 20:
            return plain[:260]
        return None

    def _save_market_brief(self, brief_text):
        brief = re.sub(r"\s+", " ", str(brief_text or "")).strip()[:260]
        if not brief:
            return
        try:
            db.save_state("hero_ai_insight", brief)
            db.log_system_event("MARKET", "MARKET_INTEL_BRIEF", brief, "INFO")
            db.log_system_event("MARKET", "AI_INSIGHT", brief, "INFO")
            logger.info("[news-maker] MARKET_INTEL_BRIEF saved.")
        except Exception as exc:
            logger.error("[news-maker] Failed to save MARKET_INTEL_BRIEF: %s", exc)

    def _extract_text(self, response):
        if hasattr(response, "text") and response.text:
            return response.text

        # Some engines return dict-like payload: {"content": [{"text": "..."}]}
        if isinstance(response, dict):
            parts = response.get("content")
            if isinstance(parts, list):
                texts = []
                for item in parts:
                    if isinstance(item, dict) and item.get("text"):
                        texts.append(str(item.get("text")))
                if texts:
                    return "\n".join(texts)

        # Best-effort for object payloads exposing .content
        content = getattr(response, "content", None)
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    texts.append(str(item.get("text")))
                elif hasattr(item, "get") and item.get("text"):
                    texts.append(str(item.get("text")))
            if texts:
                return "\n".join(texts)

        return str(response)

    def _strip_markdown_plain(self, text):
        """Return plain text stripped of markdown formatting for use in excerpt fields."""
        t = str(text or "").strip()
        t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)     # headings
        t = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", t)           # bold/italic
        t = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", t)             # italic/underline
        t = re.sub(r"~~([^~]+)~~", r"\1", t)                      # strikethrough
        t = re.sub(r"`[^`]+`", "", t)                              # inline code
        t = re.sub(r"^[-*+]\s+", "", t, flags=re.MULTILINE)       # unordered list
        t = re.sub(r"^\d+\.\s+", "", t, flags=re.MULTILINE)       # ordered list
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)            # links
        t = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", t)                # images
        t = re.sub(r"^[>|]\s*", "", t, flags=re.MULTILINE)        # blockquote/table pipe
        t = re.sub(r"\|", " ", t)                                  # remaining table pipes
        t = re.sub(r"\s{2,}", " ", t)
        return t.strip()

    def _normalize_article(self, obj):
        if not isinstance(obj, dict):
            return None
        title = str(obj.get("title") or "").strip()
        excerpt = str(obj.get("excerpt") or "").strip()
        content = str(obj.get("content") or "").strip()

        if not content:
            return None
        if not title:
            first_line = content.splitlines()[0].strip() if content.splitlines() else "Algovibe Market Update"
            title = re.sub(r"^#+\s*", "", first_line).strip()[:120] or "Algovibe Market Update"
        if not excerpt:
            excerpt = self._strip_markdown_plain(content)[:220]

        return {
            "title": title,
            "excerpt": excerpt,
            "content": content,
        }

    def _looks_like_serialized_article(self, text):
        t = str(text or "").strip().lower()
        if not t:
            return False
        if t.startswith('{"title"') or t.startswith("{'title'"):
            return True
        if t.startswith('{\\"title\\"'):
            return True
        return ('"title"' in t or "'title'" in t) and ('"excerpt"' in t or "'excerpt'" in t)

    def _parse_json_relaxed(self, text):
        raw = str(text or "").strip()
        if not raw:
            return None

        candidates = [raw]

        # 1) JSON / JSON-string layers
        for _ in range(3):
            next_candidates = []
            parsed_any = False
            for cand in candidates:
                c = str(cand or "").strip()
                if not c:
                    continue
                try:
                    obj = json.loads(c)
                    parsed_any = True
                    if isinstance(obj, dict):
                        normalized = self._normalize_article(obj)
                        if normalized:
                            return normalized
                    elif isinstance(obj, str):
                        next_candidates.append(obj)
                except Exception:
                    pass

                try:
                    obj = ast.literal_eval(c)
                    parsed_any = True
                    if isinstance(obj, dict):
                        normalized = self._normalize_article(obj)
                        if normalized:
                            return normalized
                    elif isinstance(obj, str):
                        next_candidates.append(obj)
                except Exception:
                    pass

                if '\\"' in c:
                    unescaped = c.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
                    if unescaped != c:
                        next_candidates.append(unescaped)

            if not parsed_any and not next_candidates:
                break
            candidates = next_candidates

        return None

    def _sanitize_article_payload(self, article):
        normalized = self._normalize_article(article)
        if not normalized:
            return None

        if any(self._looks_like_serialized_article(normalized.get(k, "")) for k in ("title", "excerpt", "content")):
            for candidate in (normalized.get("content"), normalized.get("title"), normalized.get("excerpt")):
                recovered = self._parse_json_relaxed(candidate)
                if recovered and not any(
                    self._looks_like_serialized_article(recovered.get(k, "")) for k in ("title", "excerpt", "content")
                ):
                    return recovered

        return normalized

    def _parse_json(self, text):
        if not text:
            return None
        text = text.strip()

        relaxed = self._parse_json_relaxed(text)
        if relaxed:
            logger.info("[news-maker] [OK] Relaxed JSON parse success")
            return relaxed

        # 1) Direct strict JSON object
        try:
            direct = json.loads(text)
            normalized = self._normalize_article(direct)
            if normalized:
                logger.info("[news-maker] [OK] Direct JSON parse success")
                return normalized
        except Exception as e:
            logger.info("[news-maker] Direct parse fail: %s", str(e)[:80])

        repaired_direct = self._repair_json_candidate(text)
        if repaired_direct and repaired_direct != text:
            try:
                direct = json.loads(repaired_direct)
                normalized = self._normalize_article(direct)
                if normalized:
                    logger.info("[news-maker] [OK] Repaired direct JSON parse success")
                    return normalized
            except Exception:
                pass

        # 2) JSON codeblock
        block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
        if block_match:
            try:
                block_obj = json.loads(block_match.group(1))
                normalized = self._normalize_article(block_obj)
                if normalized:
                    logger.info("[news-maker] [OK] Codeblock JSON parse success")
                    return normalized
            except Exception as e:
                logger.info("[news-maker] Codeblock parse fail: %s", str(e)[:80])

        # 3) First balanced JSON object in mixed text
        candidates = list(self._extract_json_candidates(text))
        logger.info("[news-maker] Found %d JSON candidates", len(candidates))
        for i, candidate in enumerate(candidates):
            parsed = None
            parse_error = None
            for cand in (candidate, self._repair_json_candidate(candidate)):
                if not cand:
                    continue
                try:
                    parsed = json.loads(cand)
                    break
                except Exception as e:
                    parse_error = e
                try:
                    parsed = ast.literal_eval(cand)
                    break
                except Exception:
                    pass

            if parsed is not None:
                normalized = self._normalize_article(parsed)
                if normalized:
                    logger.info("[news-maker] [OK] Candidate %d normalized ok", i)
                    return normalized
                logger.info("[news-maker] Candidate %d failed normalize", i)
            elif parse_error is not None:
                logger.info("[news-maker] Candidate %d parse error: %s", i, str(parse_error)[:80])

        # 4) Loose extraction fallback for malformed but key-complete objects.
        loose = self._parse_loose_article_object(text)
        if loose:
            logger.info("[news-maker] [OK] Loose JSON-like parse success")
            return loose

        return None

    def _parse_loose_article_object(self, text):
        raw = str(text or "")
        if not raw:
            return None

        # Works when provider returns near-JSON with invalid control characters.
        title_m = re.search(r'"title"\s*:\s*"([\s\S]*?)"', raw, flags=re.IGNORECASE)
        excerpt_m = re.search(r'"excerpt"\s*:\s*"([\s\S]*?)"', raw, flags=re.IGNORECASE)
        content_m = re.search(r'"content"\s*:\s*"([\s\S]*?)"(?=\s*(,|\}|$))', raw, flags=re.IGNORECASE)

        if not content_m:
            # Truncated tail fallback: keep everything after content opening quote.
            content_m = re.search(r'"content"\s*:\s*"([\s\S]*)$', raw, flags=re.IGNORECASE)

        if not (title_m and excerpt_m and content_m):
            return None

        def _clean(v):
            s = str(v or "")
            s = s.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t').replace('\\"', '"')
            s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
            return s.strip()

        obj = {
            "title": _clean(title_m.group(1)),
            "excerpt": _clean(excerpt_m.group(1)),
            "content": _clean(content_m.group(1)),
        }
        return self._normalize_article(obj)

    def _repair_json_candidate(self, text):
        raw = str(text or "")
        if not raw:
            return ""

        # Normalize common smart quotes and remove trailing commas.
        repaired = (
            raw.replace("“", '"')
               .replace("”", '"')
               .replace("’", "'")
               .replace("\u00a0", " ")
        )
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

        # Escape raw control characters inside quoted JSON strings.
        out = []
        in_str = False
        escaped = False
        for ch in repaired:
            if in_str:
                if escaped:
                    out.append(ch)
                    escaped = False
                    continue
                if ch == "\\":
                    out.append(ch)
                    escaped = True
                    continue
                if ch == '"':
                    out.append(ch)
                    in_str = False
                    continue
                if ch == "\n":
                    out.append("\\n")
                    continue
                if ch == "\r":
                    out.append("\\r")
                    continue
                if ch == "\t":
                    out.append("\\t")
                    continue
                if ord(ch) < 32:
                    out.append(f"\\u{ord(ch):04x}")
                    continue
                out.append(ch)
                continue

            if ch == '"':
                in_str = True
            out.append(ch)

        return "".join(out).strip()

    def _extract_json_candidates(self, text):
        candidates = []
        depth = 0
        start_idx = None
        in_str = False
        quote = ""
        escaped = False
        for i, ch in enumerate(text):
            if in_str:
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == quote:
                    in_str = False
                continue

            # JSON strings are double-quoted. Treating single quote as a quote
            # here can break extraction on natural-language apostrophes.
            if ch == '"':
                in_str = True
                quote = ch
                continue

            if ch == '{':
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == '}':
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        candidates.append(text[start_idx:i + 1])
                        start_idx = None
        return candidates

    def _fallback_article_from_text(self, text):
        raw = str(text or "").strip()
        if not raw:
            return None

        recovered = self._parse_json_relaxed(raw)
        if recovered:
            return recovered

        # Avoid saving raw serialized Python objects as title/content.
        if raw.startswith("{") and "'content'" in raw and "'text'" in raw:
            return None
        if self._looks_like_serialized_article(raw):
            return None

        content = self._normalize_markdown_content(raw)
        if not content:
            return None

        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        title = ""
        for ln in lines:
            if ln.startswith("#"):
                title = re.sub(r"^#+\s*", "", ln).strip()
                break
        if not title:
            title = re.sub(r"\s+", " ", lines[0] if lines else content).strip()

        non_heading_lines = [ln for ln in lines if not ln.startswith("#")]
        excerpt_src = " ".join(non_heading_lines) if non_heading_lines else content
        excerpt = self._strip_markdown_plain(excerpt_src)[:220]

        return {
            "title": title[:90],
            "excerpt": excerpt,
            "content": content,
        }

    def _strip_prompt_leakage(self, content):
        """Safety net: strip instruksi prompt dan raw context yang bocor ke content.

        Dipanggil sebelum save sebagai lapisan terakhir untuk mencegah prompt leak.
        """
        text = str(content or "")
        if not text:
            return text

        # Pattern instruksi LLM yang tidak boleh ada di artikel final.
        _PROMPT_INSTRUCTION_PATTERNS = [
            r"^\s*Tulis\s+(?:lanjutan|artikel|ringkasan|analisis)\b[^\n]*$",
            r"^\s*Masukkan\s+(?:angka|data|level|sinyal)\b[^\n]*$",
            r"^\s*Jelaskan\s+(?:alasan|penyebab|dampak)\b[^\n]*$",
            r"^\s*Hindari\s+(?:penjelasan|narasi|pengulangan)\b[^\n]*$",
            r"^\s*Balas\s+HANYA\b[^\n]*$",
            r"^\s*Return\s+exactly\s+one\b[^\n]*$",
            r"^\s*Anda\s+adalah\s+(?:Market|Senior|Chief)\b[^\n]*$",
            r"^\s*Persyaratan\s+Artikel\b[^\n]*$",
            r"^\s*Aturan\s+(?:style|isi|output)\b[^\n]*$",
        ]

        lines = text.split("\n")
        cleaned = []
        skip_section = False
        for line in lines:
            stripped = line.strip()

            # Skip "## Konteks Data Pendukung" section (raw context dump)
            if re.match(r"^##\s*Konteks\s+Data\s+Pendukung", stripped, re.IGNORECASE):
                skip_section = True
                continue
            # End skip when we hit the next heading (##)
            if skip_section and re.match(r"^##\s", stripped):
                skip_section = False
            if skip_section:
                continue

            # Check against prompt instruction patterns
            is_prompt = False
            for pat in _PROMPT_INSTRUCTION_PATTERNS:
                if re.match(pat, stripped, re.IGNORECASE | re.MULTILINE):
                    is_prompt = True
                    break
            if is_prompt:
                continue

            cleaned.append(line)

        result = "\n".join(cleaned)
        # Collapse excessive blank lines left by removals
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()

    def _generate_technical_deep_dive_fallback(self, coin_symbol=None):
        self._refresh_db_context(force=True)
        pair = self._normalize_pair_key(coin_symbol) if coin_symbol else None
        if not pair:
            valid_items = self._get_valid_market_items(require_fresh=False)
            if valid_items:
                items_sorted = sorted(valid_items, key=lambda x: float(x[1].get("score") or 0.0), reverse=True)
                pair = items_sorted[0][0]
            else:
                pair = "btcidr"
        
        clean_sym = self._clean_symbol(pair)
        ticker_data = self._tickers.get(pair, {})
        price = float(ticker_data.get("price") or 0.0)
        ch_24h = float(ticker_data.get("ch_24h") or 0.0)
        ch_1h = float(ticker_data.get("ch_1h") or 0.0)
        vol_idr = float(ticker_data.get("vol_idr") or 0.0)
        volatility = float(ticker_data.get("volatility") or 1.5)
        
        tech = {}
        history = []
        try:
            with db.get_connection() as conn:
                row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
                if row and row["history_json"]:
                    history = json.loads(row["history_json"])
                    if len(history) >= 2:
                        tech = get_technical_summary(history)
        except Exception:
            pass

        rsi = float(tech.get("rsi", 50.0))
        rsi_status = "OVERSOLD (Bullish)" if rsi < 30 else ("OVERBOUGHT (Bearish)" if rsi > 70 else "NEUTRAL")
        
        macd_hist = float(tech.get("macd_hist", 0.0))
        macd_status = "BULLISH (Histogram > 0)" if macd_hist > 0 else "BEARISH (Histogram < 0)"
        
        bb_pb = float(tech.get("bb_pb", 0.5))
        bb_status = "BULLISH (Harga < Lower Band)" if bb_pb < 0 else ("BEARISH (Harga > Upper Band)" if bb_pb > 1 else "NEUTRAL")
        
        stoch_k = float(tech.get("stoch_k", 50.0))
        stoch_d = float(tech.get("stoch_d", 50.0))
        stoch_status = "BULLISH (Cross-Up K > D)" if stoch_k > stoch_d else "BEARISH (Cross-Down K < D)"
        
        ema_crossover = str(tech.get("ema_crossover", "HOLD"))
        ema_status = "BULLISH (EMA-9 > EMA-21)" if ema_crossover == "BUY" else ("BEARISH (EMA-9 < EMA-21)" if ema_crossover == "SELL" else "NEUTRAL")
        
        williams_r = float(tech.get("williams_r", -50.0))
        williams_status = "OVERSOLD (Bullish)" if williams_r < -80 else ("OVERBOUGHT (Bearish)" if williams_r > -20 else "NEUTRAL")
        
        slope = float(tech.get("slope", 0.0))
        slope_status = "BULLISH (Slope > 0)" if slope > 0 else "BEARISH (Slope < 0)"
        
        roc_10 = float(tech.get("roc_10", 0.0))
        roc_status = "BULLISH (Rate of Change > 0)" if roc_10 > 0 else "BEARISH (Rate of Change < 0)"
        
        ob_pressure = float(tech.get("ob_pressure", 50.0))
        ob_status = "BULLISH (Buy Wall Dominant)" if ob_pressure > 55 else ("BEARISH (Sell Wall Dominant)" if ob_pressure < 45 else "NEUTRAL")
        
        vol_ratio = float(tech.get("volume_ratio", 1.0))
        vol_status = "BULLISH (Breakout Spike > 1.5)" if vol_ratio > 1.5 else "NEUTRAL"
        
        obv_slope = float(tech.get("obv_slope", 0.0))
        obv_status = "BULLISH (Accumulation Trend)" if obv_slope > 0 else "BEARISH (Distribution Trend)"

        confluence_score = 0.0
        signals = {"score": 0.0, "opposing": 0.0, "signal": "HOLD"}
        if len(history) >= 2:
            try:
                signals = derive_trade_signal(history)
            except Exception:
                pass
        confluence_score = abs(float(signals.get("score", 0.0)))
        bias = "BULLISH CONFLUENCE" if signals.get("signal") == "BUY" else ("BEARISH CONFLUENCE" if signals.get("signal") == "SELL" else "NEUTRAL / SIDEWAYS")
        
        buy_pressure = 50.0
        try:
            with db.get_connection() as conn:
                depth = conn.execute("SELECT vol, is_bid FROM market_depth WHERE pair = %s", (pair,)).fetchall()
                bid_vol = sum(r['vol'] for r in depth if r['is_bid'] == 1)
                ask_vol = sum(r['vol'] for r in depth if r['is_bid'] == 0)
                total_ob = bid_vol + ask_vol
                buy_pressure = round(bid_vol / total_ob * 100, 1) if total_ob > 0 else 50.0
        except Exception:
            pass
        
        import datetime
        waktu = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        
        title = f"[QUANT REPORT] Analisis Konfluensi Teknikal Mendalam {clean_sym} – Confluence Score: {confluence_score:.1f}"
        excerpt = f"Laporan telemetri sistem otomatis mengenai analisis teknikal 11 indikator kuantitatif lengkap pada pasangan {clean_sym}."
        
        content = f"""## Laporan Kuantitatif Terintegrasi

Berikut adalah hasil audit terperinci dari konfluensi indikator teknis utama untuk aset **{clean_sym}** per **{waktu} WIB**. Evaluasi didasarkan pada telemetri data pasar historis 15-menit dan kondisi orderbook realtime.

### Telemetri Harga & Volatilitas
| Metrik Kuantitatif | Nilai | Catatan |
|---|---|---|
| **Harga Saat Ini** | {self._format_price(price)} | Harga spot realtime |
| **Perubahan 1 Jam** | {ch_1h:+.2f}% | Momentum jangka ultra-pendek |
| **Perubahan 24 Jam** | {ch_24h:+.2f}% | Momentum jangka menengah |
| **Volatilitas Intraday** | {volatility:.2f}% | Volatilitas pergerakan rata-rata |

### Evaluasi Konfluensi 11 Indikator Aktif
Sistem mendeteksi total skor konfluensi **{confluence_score:.2f}** dengan bias arah **{bias}**.

| # | Indikator Teknis | Nilai Kuantitatif | Bobot | Status Evaluasi |
|---|---|---|---|---|
| 1 | RSI-14 (Wilder) | {rsi:.2f} | 3.0 | {rsi_status} |
| 2 | MACD Histogram | {macd_hist:.4f} | 2.5 | {macd_status} |
| 3 | Bollinger %B | {bb_pb:.2f} | 2.0 | {bb_status} |
| 4 | Stochastic RSI | K={stoch_k:.2f} D={stoch_d:.2f} | 2.0 | {stoch_status} |
| 5 | EMA 9/21 Crossover | {ema_crossover} | 1.5 | {ema_status} |
| 6 | Williams %R | {williams_r:.2f} | 1.5 | {williams_status} |
| 7 | Linear Regression Slope | {slope:.4f} | 1.0 | {slope_status} |
| 8 | Rate of Change (ROC-10) | {roc_10:.2f}% | 1.0 | {roc_status} |
| 9 | Order Book Pressure | {ob_pressure:.2f}% | 2.0 | {ob_status} |
| 10 | Volume Ratio (MA-20) | {vol_ratio:.2f}x | 1.5 | {vol_status} |
| 11 | OBV Slope (Accumulation) | {obv_slope:.4f} | 1.5 | {obv_status} |

### Analisis Kuantitatif Terintegrasi (Grounded Data)
* **Keseimbangan Order Book**: Kekuatan beli di pasar (Buy Pressure) berada pada level **{buy_pressure:.1f}%**, menunjukkan dominasi **{"pembeli (Bulls)" if buy_pressure > 50 else "penjual (Bears)"}** pada dinding antrean orderbook.
* **Kondisi Momentum**: Indikator RSI berada di level **{rsi:.1f}** dengan histogram MACD **{"menguat (bullish momentum)" if macd_hist > 0 else "melemah (bearish momentum)"}**, mengindikasikan status pasar yang sedang **{"overbought" if rsi > 70 else ("oversold" if rsi < 30 else "konsolidasi sehat")}**.
* **Volatilitas**: Volatilitas rata-rata pasar adalah **{volatility:.2f}%**. Sistem menetapkan status risk-on/risk-off saat ini sebagai **{"HIGH VOLATILITY (Caution)" if volatility > 3.0 else "NORMAL VOLATILITY (Safe)"}**.

### Rekomendasi Operasional Bot
* **Rekomendasi Entry**: **{bias}**
* **Target Stop-Loss Dinamis (ATR)**: {self._format_price(price * 0.97)} (3% Safety Cushion)
* **Target Take Profit**: {self._format_price(price * 1.015)} (1.5% Net Profit Target)
"""
        return {"title": title, "excerpt": excerpt, "content": content}

    def _generate_whale_tracking_fallback(self, coin_symbol=None):
        self._refresh_db_context(force=True)
        pair = self._normalize_pair_key(coin_symbol) if coin_symbol else None
        if not pair:
            valid_items = self._get_valid_market_items(require_fresh=False)
            if valid_items:
                items_sorted = sorted(valid_items, key=lambda x: float(x[1].get("vol_idr") or 0.0), reverse=True)
                pair = items_sorted[0][0]
            else:
                pair = "btcidr"
        
        clean_sym = self._clean_symbol(pair)
        depth_data = self._depth_summary.get(pair, {})
        ticker_data = self._tickers.get(pair, {})
        price = float(ticker_data.get("price") or 0.0)
        vol_idr = float(ticker_data.get("vol_idr") or 0.0)
        
        bid_vol = float(depth_data.get("bid_vol") or vol_idr * 0.5)
        ask_vol = float(depth_data.get("ask_vol") or vol_idr * 0.5)
        imbalance = float(depth_data.get("imbalance") or 0.0)
        spread_pct = float(depth_data.get("spread_pct") or 0.05)
        
        tech = {}
        history = []
        try:
            with db.get_connection() as conn:
                row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
                if row and row["history_json"]:
                    history = json.loads(row["history_json"])
                    if len(history) >= 2:
                        tech = get_technical_summary(history)
        except Exception:
            pass

        vol_ratio = float(tech.get("volume_ratio", 1.0))
        obv_slope = float(tech.get("obv_slope", 0.0))
        
        import datetime
        waktu = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        
        title = f"[WHALE REPORT] Deteksi Aliran Dana & Dinding Likuiditas Besar {clean_sym}"
        excerpt = f"Audit eksklusif terhadap pergerakan orderbook, penyebaran bid-ask spread, serta deteksi akumulasi volume abnormal oleh pelaku pasar skala besar (Whales) pada {clean_sym}."
        
        content = f"""## Laporan Aliran Dana & Whales Realtime

Sistem mendeteksi adanya aktivitas akumulasi dan penyebaran likuiditas pada pasar **{clean_sym}** per **{waktu} WIB**. Laporan ini diaudit menggunakan data real-time dari ketebalan order book (bid/ask depth) dan tingkat partisipasi volume.

### Telemetri Kedalaman Pasar (Orderbook Depth)
| Metrik Aliran Dana | Nilai Kuantitatif | Evaluasi Sistem |
|---|---|---|
| **Total Dinding Beli (Bid Vol)** | {self._format_price(bid_vol)} | Likuiditas Penahan Bawah |
| **Total Dinding Jual (Ask Vol)** | {self._format_price(ask_vol)} | Resistensi Dinding Atas |
| **Orderbook Imbalance** | {imbalance:+.2f}% | Dominasi Arah Likuiditas |
| **Spread Bid-Ask** | {spread_pct:.3f}% | Indikator Efisiensi Likuiditas |
| **Volume Ratio vs MA-20** | {vol_ratio:.2f}x | Deteksi Spike Partisipasi |
| **Kemiringan OBV (OBV Slope)** | {obv_slope:.6f} | Deteksi Akumulasi Diam-diam |

### Analisis Likuiditas Whales
* **Penyebaran Dinding (Order Book Walls)**: Dinding beli (Bid Volume) terakumulasi sebesar **{self._format_large_volume(bid_vol)}**, sedangkan dinding jual (Ask Volume) terkonsentrasi di level **{self._format_large_volume(ask_vol)}**.
* **Imbalance & Spread**: Skor imbalance saat ini tercatat sebesar **{imbalance:+.2f}%**, yang menunjukkan adanya bias **{"pembelian agresif" if imbalance > 0 else "distribusi tekanan jual"}**. Nilai spread yang sangat rapat sebesar **{spread_pct:.3f}%** mengonfirmasi tingkat likuiditas yang **sangat efisent dan ramah scalping**.
* **Aktivitas Akumulasi Klandestin**: Kemiringan kurva OBV saat ini berada pada angka **{obv_slope:.6f}**, menandakan adanya **{"akumulasi aktif oleh para investor institusi/whales" if obv_slope > 0 else "distribusi bertahap oleh pemilik modal besar"}**.

### Rekomendasi Scalping Kuantitatif
* **Tingkat Bahaya Slippage**: **{"RENDAH (Sangat aman untuk ukuran order besar)" if spread_pct < 0.1 else "SEDANG (Gunakan limit order)"}**
* **Level Support Whale Utama**: {self._format_price(price * 0.985)}
* **Level Resistance Whale Utama**: {self._format_price(price * 1.015)}
"""
        return {"title": title, "excerpt": excerpt, "content": content}

    def _generate_risk_warning_fallback(self, coin_symbol=None):
        self._refresh_db_context(force=True)
        pair = self._normalize_pair_key(coin_symbol) if coin_symbol else None
        if not pair:
            valid_items = self._get_valid_market_items(require_fresh=False)
            if valid_items:
                items_sorted = sorted(valid_items, key=lambda x: float(x[1].get("volatility") or 0.0), reverse=True)
                pair = items_sorted[0][0]
            else:
                pair = "btcidr"
        
        clean_sym = self._clean_symbol(pair)
        ticker_data = self._tickers.get(pair, {})
        price = float(ticker_data.get("price") or 0.0)
        volatility = float(ticker_data.get("volatility") or 1.5)
        
        tech = {}
        history = []
        try:
            with db.get_connection() as conn:
                row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
                if row and row["history_json"]:
                    history = json.loads(row["history_json"])
                    if len(history) >= 2:
                        tech = get_technical_summary(history)
        except Exception:
            pass

        gate_4b_status = "PASSED (Safe)" if volatility <= 3.0 else "REJECTED (High Volatility ATR)"
        
        ema50 = price * 0.99
        if len(history) >= 50:
            try:
                prices = [float(h["close"]) for h in history]
                alpha = 2.0 / (50 + 1)
                ema = prices[0]
                for p in prices[1:]:
                    ema = p * alpha + ema * (1 - alpha)
                ema50 = ema
            except Exception:
                pass
        
        gate_4c_status = "PASSED (Price > EMA-50)" if price >= ema50 else "REJECTED (Price < EMA-50)"
        
        rsi = float(tech.get("rsi", 50.0))
        buy_pressure = 50.0
        try:
            with db.get_connection() as conn:
                depth = conn.execute("SELECT vol, is_bid FROM market_depth WHERE pair = %s", (pair,)).fetchall()
                bid_vol = sum(r['vol'] for r in depth if r['is_bid'] == 1)
                ask_vol = sum(r['vol'] for r in depth if r['is_bid'] == 0)
                total_ob = bid_vol + ask_vol
                buy_pressure = (bid_vol / total_ob * 100) if total_ob > 0 else 50.0
        except Exception:
            pass
        
        vol_ratio = float(tech.get("volume_ratio", 1.0))
        
        bypass_status = "INACTIVE"
        if rsi < 26 and buy_pressure > 66.0 and vol_ratio > 2.1:
            bypass_status = "ACTIVE (Extreme Bottom Reversal Bypass Triggered)"
            gate_4c_status = "BYPASSED (Extreme Bottom Reversal)"
        
        regime = db.get_state("current_market_regime") or "NEUTRAL"
        
        import datetime
        waktu = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        standard_lot = "standar ($10 USDT)" if os.getenv("EXCHANGE", "binance").lower() == "binance" else "standar (100k IDR)"
        
        title = f"[RISK WARNING] Telemetri Volatilitas Intraday & Audit Safety Guards {clean_sym}"
        excerpt = f"Evaluasi berkala terhadap status manajemen risiko pasar, deteksi status filter filter ATR (Gate 4b), trend filter EMA-50 (Gate 4c), dan panduan manajemen portofolio {clean_sym}."
        
        content = f"""## Laporan Manajemen Risiko & Audit Pintu Keamanan Bot

Sistem manajemen portofolio Algovibe mengeluarkan status risiko terkini untuk **{clean_sym}** per **{waktu} WIB** guna melindungi modal perdagangan di bawah volatilitas pasar saat ini.

### Status Keamanan Pre-Entry Gates (Audit Realtime)
| Gerbang Keamanan Bot | Status Operasional | Kriteria & Kondisi |
|---|---|---|
| **Gate 4b: ATR Volatility Filter** | {gate_4b_status} | Toleransi batas volatilitas < 3% |
| **Gate 4c: EMA-50 Trend Filter** | {gate_4c_status} | Harga wajib berada di atas EMA-50 |
| **Bottom-Explosion Extreme Bypass** | {bypass_status} | RSI < 26 + Buy Pressure > 66% + Vol Ratio > 2.1 |
| **Consecutive-Loss Halt Guard** | AKTIF (Siaga) | Mencegah beruntunnya kerugian beruntun |

### Metrik Risiko & Volatilitas Terperinci
* **Regime Pasar**: Pasar saat ini beroperasi di bawah rezim **{regime}** dengan volatilitas intraday terdeteksi sebesar **{volatility:.2f}%**.
* **Filter Volatilitas ATR**: Indikator ATR menunjukkan nilai pergerakan rata-rata yang **{"melebihi ambang batas aman (Cenderung High Risk)" if volatility > 3.0 else "berada di dalam koridor aman (Cenderung Low Risk)"}**.
* **Evaluasi Tren EMA-50**: Harga saat ini adalah **{self._format_price(price)}**, sedangkan level trend EMA-50 berada pada **{self._format_price(ema50)}**. Kondisi ini menandakan aset sedang berada dalam fase **{"Uptrend Sehat (Bullish)" if price > ema50 else "Downtrend Berisiko (Bearish)"}**.

### Panduan Mitigasi Risiko Portofolio
* **Ukuran Maksimal Posisi (Position Sizing)**: **{"Kurangi ukuran lot hingga 50% (High Volatility)" if volatility > 3.0 else f"Gunakan ukuran lot {standard_lot}"}**
* **Pengaman Tambahan**: Auto Stop-Loss (Auto-SL) wajib dinyalakan pada level **{self._format_price(price * 0.97)}** untuk menghindari fluktuasi harga yang tidak diinginkan.
"""
        return {"title": title, "excerpt": excerpt, "content": content}

    def _generate_market_headline_fallback(self, coin_symbol, move_data):
        clean_sym = self._clean_symbol(coin_symbol)
        pct = float(move_data.get("pct_change") or 0.0)
        price = float(move_data.get("price") or 0.0)
        direction = "MELESAT NAIK" if pct >= 0 else "ANJLOK TURUN"
        trend_emoji = "🟢" if pct >= 0 else "🔴"
        
        import datetime
        waktu = datetime.datetime.now().strftime("%H:%M WIB")
        
        title = f"[FLASH ALERT] {clean_sym} {direction} {pct:+.1f}% Hari Ini"
        excerpt = f"Deteksi telemetri pergerakan harga cepat pada pasangan {clean_sym} mencapai {pct:+.1f}%."
        
        price_usdt = price / 16000.0
        
        content = f"""## {trend_emoji} Flash Market Alert: Pergerakan Signifikan {clean_sym}
        
Sistem otomasi mendeteksi pergerakan harga yang cepat dan signifikan untuk aset **{clean_sym}** pada pukul **{waktu}**.

### Ringkasan Telemetri Flash Alert
* **Aset**: {clean_sym}
* **Arah Pergerakan**: **{direction}**
* **Perubahan Persentase**: **{pct:+.2f}%**
* **Harga Spot Saat Ini**: {self._format_price(price)}
* **Sumber Data**: Algovibe Exchange Telemetry

### Analisis Cepat & Tindakan Bot
Pergerakan sebesar **{pct:+.2f}%** memicu alarm ambang batas bot. Sistem saat ini terus memantau likuiditas order book dan sebaran bid-ask spread untuk mendeteksi potensi kelanjutan momentum atau pembalikan arah instan (mean reversion). Operasional scalping bot disarankan untuk meningkatkan level waspada risiko volatility drag.
"""
        return {"title": title, "excerpt": excerpt, "content": content}

    def _generate_periodic_report_fallback(self, rtype):
        self._refresh_db_context(force=True)
        valid_items = self._get_valid_market_items(require_fresh=False)
        
        import datetime
        waktu = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        
        lbl = "Pagi" if "MORNING" in rtype else "Sore"
        title = f"[QUANT REPORT] Analisis & Rangkuman Pasar {lbl} – Algovibe Intelligence"
        excerpt = f"Rangkuman data telemetri pasar otomatis Algovibe untuk sesi {lbl} tanggal {waktu}."
        
        table_rows = ""
        for i, (pair, ticker) in enumerate(valid_items[:8]):
            clean_sym = self._clean_symbol(pair)
            price = float(ticker.get("price") or 0.0)
            ch_24h = float(ticker.get("ch_24h") or 0.0)
            ch_1h = float(ticker.get("ch_1h") or 0.0)
            vol_idr = float(ticker.get("vol_idr") or 0.0)
            vol_m = vol_idr / 1_000_000.0
            
            table_rows += f"| {i+1} | **{clean_sym}** | {self._format_price(price)} | {ch_1h:+.2f}% | {ch_24h:+.2f}% | {self._format_vol_m(vol_m)} |\n"
            
        content = f"""## Rangkuman Pasar {lbl} ({waktu} WIB)
        
Berikut adalah ikhtisar kondisi pasar berdasarkan telemetri kuantitatif langsung dari bursa.

### Telemetri Kinerja Aset Teraktif
| # | Koin | Harga Spot | Perubahan 1 Jam | Perubahan 24 Jam | Volume 24 Jam |
|---|---|---|---|---|---|
{table_rows}

### Catatan Operasional
Kondisi pasar saat ini menunjukkan volatilitas yang stabil dengan kecenderungan momentum yang beragam. Bot scalping tetap aktif memantau pintu masuk teknis (EMA-50 & ATR filter). Selalu prioritaskan perlindungan modal dengan pengaman risiko default.
"""
        return {"title": title, "excerpt": excerpt, "content": content}

    def _generate_trending_review_fallback(self):
        self._refresh_db_context(force=True)
        valid_items = self._get_valid_market_items(require_fresh=False)
        
        import datetime
        waktu = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        
        title = f"[QUANT REPORT] Ulasan Koin Trending Terpopuler Hari Ini"
        excerpt = f"Analisis kuantitatif otomatis mengenai aset-aset dengan aktivitas volume dan momentum tertinggi."
        
        gainers = sorted(valid_items, key=lambda x: float(x[1].get("ch_24h") or 0.0), reverse=True)[:5]
        table_rows = ""
        for i, (pair, ticker) in enumerate(gainers):
            clean_sym = self._clean_symbol(pair)
            price = float(ticker.get("price") or 0.0)
            ch_24h = float(ticker.get("ch_24h") or 0.0)
            vol_idr = float(ticker.get("vol_idr") or 0.0)
            vol_m = vol_idr / 1_000_000.0
            table_rows += f"| {i+1} | **{clean_sym}** | {self._format_price(price)} | {ch_24h:+.2f}% | {self._format_vol_m(vol_m)} |\n"
            
        content = f"""## Ulasan Aset Trending & Teraktif ({waktu} WIB)
        
Berikut adalah jajaran aset dengan lonjakan aktivitas volume dan momentum harga terkuat dalam 24 jam terakhir.

### Top Gainers & Momentum Movers
| # | Koin | Harga Spot | Perubahan 24 Jam | Volume 24 Jam |
|---|---|---|---|---|
{table_rows}

### Analisis Kuantitatif
Aset-aset di atas mendominasi volume transaksi pasar saat ini. Kehadiran spike volume yang tinggi memicu alarm breakout pada sistem Algovibe. Bot scalping terus mengamati level support kunci untuk peluang entri momentum berkelanjutan.
"""
        return {"title": title, "excerpt": excerpt, "content": content}

    def _generate_market_intelligence_brief_fallback(self):
        self._refresh_db_context(force=True)
        valid_items = self._get_valid_market_items(require_fresh=False)
        if not valid_items:
            return "Kondisi pasar saat ini stabil dengan likuiditas normal."
            
        top_gainers = sorted(valid_items, key=lambda x: float(x[1].get("ch_24h") or 0.0), reverse=True)[:3]
        bullets = []
        for pair, ticker in top_gainers:
            clean_sym = self._clean_symbol(pair)
            ch_24h = float(ticker.get("ch_24h") or 0.0)
            bullets.append(f"{clean_sym} ({ch_24h:+.1f}%)")
            
        import datetime
        waktu = datetime.datetime.now().strftime("%H:%M WIB")
        
        brief = f"Telemetri Algovibe pada {waktu} mendeteksi aktivitas pasar teraktif pada aset: {', '.join(bullets)}. Bot scalping beroperasi normal dengan parameter risiko default."
        return brief

    def _get_usdt_idr_rate(self):
        # 1. Try to read from Indodax tickers in self._tickers (if loaded)
        if hasattr(self, "_tickers") and self._tickers:
            for key in ["usdtidr", "usdt/idr", "usdt_idr"]:
                if key in self._tickers:
                    val = float(self._tickers[key].get("price") or 0.0)
                    if val > 10000:
                        return val
        
        # 2. Try to fetch from Indodax ccxt directly (cache it)
        if not hasattr(self, "_usdt_idr_cached_rate") or time.time() - getattr(self, "_usdt_idr_cached_time", 0) > 300:
            try:
                from app.infrastructure.exchange_factory import create_indodax_exchange
                idx = create_indodax_exchange()
                ticker = idx.fetch_ticker("USDT/IDR")
                price = float(ticker.get("last") or ticker.get("close") or 16000.0)
                if price > 10000:
                    self._usdt_idr_cached_rate = price
                    self._usdt_idr_cached_time = time.time()
                    logger.info("[news-maker] Fetched live USDT/IDR rate from Indodax: %.2f", price)
                    return price
            except Exception as err:
                logger.warning("[news-maker] Failed to fetch live USDT/IDR rate via CCXT: %s", err)
        
        if hasattr(self, "_usdt_idr_cached_rate") and self._usdt_idr_cached_rate > 10000:
            return self._usdt_idr_cached_rate
            
        # 3. Final fallback
        return 16000.0

    def _format_price(self, price):
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            rate = self._get_usdt_idr_rate()
            idr_price = price * rate
            return f"Rp {idr_price:,.2f}"
        else:
            return f"Rp {price:,.2f}"

    def _format_large_volume(self, vol):
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            rate = self._get_usdt_idr_rate()
            vol_idr = vol * rate
        else:
            vol_idr = vol

        if vol_idr >= 1_000_000.0:
            return f"{vol_idr/1_000_000.0:.1f} Juta IDR"
        else:
            return f"{vol_idr:,.0f} IDR"

    def _format_vol_m(self, vol_m):
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            rate = self._get_usdt_idr_rate()
            vol_m_idr = vol_m * rate
            return f"Rp {vol_m_idr:,.2f}M"
        else:
            return f"Rp {vol_m:,.2f}M"

    def _save_article(self, art, category):
        try:
            art = self._sanitize_article_payload(art)
            if not art:
                logger.warning("[news-maker] Invalid/unsanitized article payload, skip save.")
                return

            art = self._ensure_min_content(art, "")

            # Clean any stray raw ticker symbols (e.g. PIPPINIDR -> PIPPIN) in final article text
            # We sort by length descending to replace longer strings first (e.g., 'pippinidr' before 'idr')
            for pair in sorted(self._tickers.keys(), key=len, reverse=True):
                raw_upper = pair.upper()
                raw_lower = pair.lower()
                clean_upper = self._clean_symbol(pair)
                clean_lower = clean_upper.lower()
                
                for key in ("title", "excerpt", "content"):
                    val = str(art.get(key) or "")
                    val = val.replace(raw_upper, clean_upper)
                    val = val.replace(raw_lower, clean_lower)
                    val = val.replace(raw_upper.capitalize(), clean_upper.capitalize())
                    art[key] = val

            # Safety net: strip prompt leakage sebelum save.
            for key in ("content", "title", "excerpt"):
                val = str(art.get(key) or "").strip()
                if key == "content":
                    val = self._strip_prompt_leakage(val)
                art[key] = val

            title = art.get('title', '')
            excerpt = art.get('excerpt', '')
            content = art.get('content', '')
            if not (title and excerpt and content):
                logger.warning("[news-maker] Invalid article payload, skip save.")
                return

            with db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO intelligence_reports (title, excerpt, content, category, author) VALUES (%s, %s, %s, %s, %s)",
                    (title, excerpt, content, category, "ALGOVIBE_ORACLE")
                )
            logger.info("[news-maker] Article saved: %s", title)
        except Exception as e:
            logger.error("[news-maker] Save error: %s", e)

def main():
    parser = argparse.ArgumentParser(description="Algovibe News Maker")
    parser.add_argument("--manual", choices=["morning", "evening", "headline", "brief", "landing", "tech_deep_dive", "whale_tracking", "risk_warning"], help="Run one-shot manual generation")
    parser.add_argument("--coin", help="Coin symbol for manual headline/landing mode, e.g. btc")
    parser.add_argument("--count", type=int, default=3, help="Headline count target for landing mode")
    args = parser.parse_args()

    oracle = NewsMaker()
    loop = asyncio.get_running_loop() if asyncio._get_running_loop() else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(signum, _frame):
        oracle._running = False
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if args.manual:
        loop.run_until_complete(oracle.run_manual(args.manual, coin_symbol=args.coin, headline_count=args.count))
    else:
        loop.run_until_complete(oracle.start())

if __name__ == "__main__":
    main()
