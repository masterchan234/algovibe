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
from textwrap import dedent
from zoneinfo import ZoneInfo

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.database import db
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
LLM_TIMEOUT_SEC = int(os.getenv("NEWS_LLM_TIMEOUT_SEC", "90"))
LLM_TIMEOUT_RETRY_SEC = int(os.getenv("NEWS_LLM_TIMEOUT_RETRY_SEC", "140"))
REMOTE_TIMEOUT_SEC = int(os.getenv("NEWS_REMOTE_TIMEOUT_SEC", "60"))
NEWS_LLM_PROVIDER = str(os.getenv("NEWS_LLM_PROVIDER", "local") or "local").strip().lower()
NEWS_HF_API_URL = str(os.getenv("NEWS_HF_API_URL", "") or "").strip()
NEWS_HF_API_TOKEN = str(os.getenv("NEWS_HF_API_TOKEN", "") or "").strip()
NEWS_CF_API_URL = str(os.getenv("NEWS_CF_API_URL", "") or "").strip()
NEWS_CF_API_TOKEN = str(os.getenv("NEWS_CF_API_TOKEN", "") or "").strip()
NEWS_WS_PRIME_SEC = int(os.getenv("NEWS_WS_PRIME_SEC", "8"))
NEWS_WS_MIN_TICKS = int(os.getenv("NEWS_WS_MIN_TICKS", "5"))
NEWS_WS_SAMPLE_SEC = float(os.getenv("NEWS_WS_SAMPLE_SEC", "5"))

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

    def _remote_provider_configured(self):
        provider = self._llm_provider
        hf_ok = bool(NEWS_HF_API_URL and NEWS_HF_API_TOKEN)
        cf_ok = bool(NEWS_CF_API_URL and NEWS_CF_API_TOKEN)

        if provider in ("huggingface", "hf"):
            return hf_ok
        if provider in ("cloudflare", "cf"):
            return cf_ok
        if provider == "auto":
            return hf_ok or cf_ok
        return False

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
            logger.info("[news-maker] ✅ News Engine Ready.")
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
        logger.info("[news-maker] ✅ Service Started.")
        await asyncio.gather(
            self._scheduler_loop(),
            self._realtime_monitor_loop(),
            self._consume_market_data(),
            self._consume_signal_data(),
        )

    def _load_manual_market_snapshot(self, coin_symbol=None):
        """Best-effort snapshot for manual generation so service remains single source."""
        try:
            import ccxt
        except Exception:
            logger.warning("[news-maker] ccxt not available for manual snapshot.")
            return

        try:
            exchange = ccxt.indodax({"enableRateLimit": True})
            wanted = str(coin_symbol or "").lower().strip()
            all_tickers = exchange.fetch_tickers()
            for symbol, t in all_tickers.items():
                if not symbol.endswith("/IDR"):
                    continue
                pair = symbol.replace("/IDR", "").replace("_IDR", "").lower()
                if wanted and pair != wanted:
                    continue

                price = float(t.get("last") or 0.0)
                pct = float(t.get("percentage") or 0.0)
                if abs(pct) < 0.0001:
                    # Some exchanges return empty/zero percentage; derive from open.
                    open_px = float(t.get("open") or 0.0)
                    if price > 0 and open_px > 0:
                        pct = ((price - open_px) / open_px) * 100.0
                quote_vol = float(t.get("quoteVolume") or 0.0)
                base_vol = float(t.get("baseVolume") or 0.0)
                vol_idr = quote_vol if quote_vol > 0 else base_vol * price

                self._tickers[pair] = {
                    "price": price,
                    "ch_24h": pct,
                    "ch_1h": float(self._tickers.get(pair, {}).get("ch_1h") or 0.0),
                    "vol": vol_idr,
                    "vol_idr": vol_idr,
                    "updated_at": time.time(),
                    "source": "ccxt",
                }
            self._prev_tickers = dict(self._tickers)
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
            pair = str(coin_symbol or "").lower().strip()
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

        logger.error("[news-maker] Unknown manual mode: %s", mode)

    async def _consume_market_data(self):
        # We only need current prices for briefing context
        from shared.algovibe_pb2 import Tick
        async for raw in self._market_sub.aiter_messages():
            try:
                tick = Tick()
                tick.ParseFromString(raw)
                pair = str(tick.symbol or "").lower()
                if pair:
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
                pair = str(sig.symbol or "").lower()
                if not pair:
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
            except Exception:
                pass

    def _apply_tick_update(self, pair, price, ch_24h, vol_idr):
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

    def _resolve_realtime_move(self, pair):
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

    def _refresh_db_context(self, force=False):
        """Refresh ticker_history and market_depth cache, then enrich in-memory tickers."""
        now_ts = time.time()
        if not force and (now_ts - self._last_db_refresh_ts) < 30:
            return

        try:
            with db.get_connection() as conn:
                signal_rows = conn.execute(
                    """
                    SELECT pair, ch_5m, ch_15m, ch_1h, volatility, score, trend, signal, updated_at
                    FROM ticker_history
                    ORDER BY updated_at DESC
                    LIMIT 500
                    """
                ).fetchall()

                depth_rows = conn.execute(
                    """
                    SELECT
                        pair,
                        MAX(CASE WHEN is_bid = 1 THEN price END) AS best_bid,
                        MIN(CASE WHEN is_bid = 0 THEN price END) AS best_ask,
                        SUM(CASE WHEN is_bid = 1 THEN vol ELSE 0 END) AS bid_vol,
                        SUM(CASE WHEN is_bid = 0 THEN vol ELSE 0 END) AS ask_vol
                    FROM market_depth
                    GROUP BY pair
                    """
                ).fetchall()

            db_signals = {}
            for row in signal_rows:
                pair = str(row.get("pair") or "").lower()
                if not pair:
                    continue
                db_signals[pair] = {
                    "ch_5m": float(row.get("ch_5m") or 0.0),
                    "ch_15m": float(row.get("ch_15m") or 0.0),
                    "ch_1h": float(row.get("ch_1h") or 0.0),
                    "volatility": float(row.get("volatility") or 0.0),
                    "score": float(row.get("score") or 0.0),
                    "trend": str(row.get("trend") or "N/A"),
                    "signal": str(row.get("signal") or "HOLD"),
                }

            depth_summary = {}
            for row in depth_rows:
                pair = str(row.get("pair") or "").lower()
                if not pair:
                    continue
                best_bid = float(row.get("best_bid") or 0.0)
                best_ask = float(row.get("best_ask") or 0.0)
                bid_vol = float(row.get("bid_vol") or 0.0)
                ask_vol = float(row.get("ask_vol") or 0.0)
                spread_pct = 0.0
                if best_bid > 0 and best_ask > 0 and best_ask >= best_bid:
                    spread_pct = ((best_ask - best_bid) / best_bid) * 100.0
                imbalance = 0.0
                total_vol = bid_vol + ask_vol
                if total_vol > 0:
                    imbalance = (bid_vol - ask_vol) / total_vol

                depth_summary[pair] = {
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "bid_vol": bid_vol,
                    "ask_vol": ask_vol,
                    "spread_pct": spread_pct,
                    "imbalance": imbalance,
                }

            self._db_signals = db_signals
            self._depth_summary = depth_summary

            # Enrich only live tickers that already have a valid price snapshot.
            for pair, sig in self._db_signals.items():
                if pair not in self._tickers:
                    continue
                if not self._is_valid_market_entry(pair, self._tickers.get(pair, {}), require_fresh=False):
                    continue
                # Prefer WS-computed change values over DB (WS = live, DB = 1 cycle stale).
                ws_hist = self._ws_price_history.get(pair)
                ch_5m_live = self._compute_ws_change(pair, 300) if ws_hist else 0.0
                ch_15m_live = self._compute_ws_change(pair, 900) if ws_hist else 0.0
                ch_1h_live = self._compute_ws_change(pair, 3600) if ws_hist else 0.0
                self._tickers[pair]["ch_5m"] = ch_5m_live if (ws_hist and abs(ch_5m_live) > 0) else sig["ch_5m"]
                self._tickers[pair]["ch_15m"] = ch_15m_live if (ws_hist and abs(ch_15m_live) > 0) else sig["ch_15m"]
                self._tickers[pair]["ch_1h"] = ch_1h_live if (ws_hist and abs(ch_1h_live) > 0) else sig["ch_1h"]
                self._tickers[pair]["volatility"] = sig["volatility"]
                self._tickers[pair]["score"] = sig["score"]
                self._tickers[pair]["trend"] = sig["trend"]
                # Signal: prefer live IPC signal dari brain-inference, fallback ke DB.
                ipc_sig = self._ipc_signals.get(pair)
                self._tickers[pair]["signal"] = ipc_sig["signal"] if ipc_sig else sig["signal"]
                if ipc_sig:
                    self._tickers[pair]["confidence"] = ipc_sig["confidence"]

            self._last_db_refresh_ts = now_ts
        except Exception as exc:
            logger.warning("[news-maker] DB context refresh failed: %s", exc)

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
                
                # Every 6 intervals (~3 min), also generate headline for top gainer
                interval_count += 1
                if interval_count >= 6 and len(self._tickers) > 0:
                    interval_count = 0
                    top_gainer = self._get_top_gainer()
                    if top_gainer:
                        await self._generate_market_headline(top_gainer[0], top_gainer[1])
                
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
            else:
                logger.warning("[news-maker] Empty/invalid article payload for %s", rtype)
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
        
        direction = "📈 Naik" if move_data['pct_change'] >= 0 else "📉 Turun"
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
                logger.warning("[news-maker] Empty headline payload for %s, skipping save.", coin_symbol)
                return
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
                logger.warning("[news-maker] Empty/invalid MARKET_INTEL_BRIEF payload")
                return
            self._save_market_brief(brief)
        except Exception as e:
            logger.error("[news-maker] Failed to generate MARKET_INTEL_BRIEF: %s", e)

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
        valid_items = self._get_valid_market_items(require_fresh=True)
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
                context += f"- {pair.upper()}: {price:,.0f} IDR ({ch_24h:+.1f}%)\n"
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
                    f"- {pair.upper()}: trend={sig.get('trend', 'N/A')} signal={sig.get('signal', 'HOLD')} "
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
                    f"- {pair.upper()}: spread={float(dep.get('spread_pct') or 0.0):.3f}% "
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

        context = f"Coin Focus: {coin_symbol.upper()}\n"
        context += f"Price: {move_data['price']:,.0f} IDR\n"
        context += f"Change 1h: {move_data['change_1h']:+.1f}% | Delta Active: {move_data['pct_change']:+.1f}% ({move_data.get('pct_source', 'auto')})\n"
        context += f"Volume 24h: {move_data['vol_idr']/1e9:.2f}B IDR\n"
        context += "\nTop Gainers (Realtime WS):\n"
        if gainers:
            for pair, _data, rt in gainers:
                context += f"- {pair.upper()}: {float(rt.get('pct_change') or 0):+.2f}% ({rt.get('pct_source', '24h')})\n"
        else:
            context += "- No valid live gainers\n"
        context += "\nTop Losers (Realtime WS):\n"
        if losers:
            for pair, _data, rt in losers:
                context += f"- {pair.upper()}: {float(rt.get('pct_change') or 0):+.2f}% ({rt.get('pct_source', '24h')})\n"
        else:
            context += "- No valid live losers\n"

        regime = db.get_state("current_market_regime") or "NEUTRAL"
        context += f"Market Regime: {regime}\n"

        pair = str(coin_symbol or "").lower()
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
        contract = self._markdown_article_output_contract(
            variant="headline",
            title_rule="Title: Breaking news impactful, 50-80 karakter. Include: coin + action + pct.",
            excerpt_rule="Excerpt: Summary 120-180 karakter. Jelaskan: apa geraknya, kenapa significant, dampaknya.",
            min_chars=MIN_NEWS_CONTENT_CHARS,
            content_hint="DETAIL LENGKAP (300+ kata) dalam FORMAT MARKDOWN yang ringkas dan enak dibaca di news feed.",
        )
        return (
            f"Anda adalah Market Analyst Algovibe. Tulis Breaking News pasar kripto dalam Bahasa Indonesia.\n"
            f"Analisis: {coin_symbol.upper()} - {direction.upper()} {abs(move_data['pct_change']):.1f}%\n"
            f"\nKonteks Data:\n{context}\n"
            f"{contract}\n"
            "Balas HANYA 1 JSON object tanpa wrapper block.\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    def _build_report_prompt(self, rtype, context):
        title = "Morning Briefing" if rtype == "MORNING_BRIEF" else "Market Review Malam"
        contract = self._markdown_article_output_contract(
            variant="report",
            title_rule="Judul: catchy, informatif, 50-80 karakter.",
            excerpt_rule="Excerpt: ringkasan 100-150 karakter yang mengecup poin utama.",
            min_chars=MIN_NEWS_CONTENT_CHARS,
            content_hint="DETAIL & LENGKAP (500+ kata) dalam FORMAT MARKDOWN PROPER yang nyaman dibaca di news feed.",
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
        """Ensure article content meets minimum character requirement."""
        if not isinstance(article, dict):
            return article

        content = self._normalize_markdown_content(article.get("content") or "")
        if len(content) >= MIN_NEWS_CONTENT_CHARS:
            article["content"] = content
            return article

        ctx = re.sub(r"\s+", " ", str(context or "")).strip()[:1200]
        extension = dedent(f"""
    ## Ringkasan Pasar

    Tulis lanjutan yang tetap ringkas, langsung ke poin, dan konsisten dengan section utama di atas.

    ## Data Utama

    Masukkan angka, level, atau sinyal penting secara singkat. Hindari penjelasan berputar.

    ## Analisis Teknis

    Jelaskan alasan utama pergerakan dengan paragraf pendek dan bullet singkat bila perlu.

    ## Level & Strategi

### Skenario Bullish
Entry bertahap hanya setelah konfirmasi volume dan candle close di atas resistance intraday. Pastikan trend alignment di timeframe 5m/15m/1h sebelum scaling in.

### Skenario Netral
Fokus range trading dengan ukuran posisi kecil. Hindari overtrading saat spread melebar atau volatilitas extreme.

### Skenario Bearish
Utamakan proteksi modal dan batasi eksposur leverage. Gunakan stop-loss ketat berbasis struktur pasar yang terdekat.

## Risiko & Skenario

### Manajemen Risiko
- Risiko per posisi: maksimal 1-2% ekuitas
- Take-profit: gunakan partial tp untuk lock profit
- Evaluasi ulang: jika volatilitas melonjak tiba-tiba

## Validasi Teknikal Pre-Entry

| Checklist | Status | Catatan |
| --- | --- | --- |
| Trend alignment 5m/15m/1h | ? | Harus searah |
| Spread health | ? | Harus tight untuk eksekusi |
| Orderbook imbalance | ? | Harus support arah posisi |
| TP/SL realistic | ? | Versus volatilitas terbaru |

## Kesimpulan

Tutupan artikel tetap singkat, konsisten, dan mudah dipindai di news feed.

## Konteks Data Pendukung

{ctx}
""").strip()
        _ext_count = 0
        while len(content) < MIN_NEWS_CONTENT_CHARS and _ext_count < 2:
            content += "\n\n" + extension
            _ext_count += 1

        article["content"] = self._normalize_markdown_content(content[: max(MIN_NEWS_CONTENT_CHARS, len(content))])
        if not article.get("excerpt"):
            article["excerpt"] = self._strip_markdown_plain(content)[:220]
        return article

    async def _ask_llm_raw(self, prompt):
        loop = asyncio.get_running_loop()
        local_first = self._llm_provider in ("local", "auto")
        remote_only = self._llm_provider in ("huggingface", "hf", "cloudflare", "cf")

        if local_first and self._engine is not None:
            text = await self._ask_llm_local_raw(prompt)
            if text:
                return text

        if remote_only or self._remote_provider_configured():
            text = await loop.run_in_executor(None, self._ask_llm_remote_raw_sync, prompt)
            if text:
                return text

        return ""

    async def _ask_llm_local_raw(self, prompt):
        loop = asyncio.get_running_loop()
        try:
            def _infer():
                with self._engine.create_conversation() as conv:
                    return conv.send_message(prompt)

            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, _infer),
                    timeout=LLM_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[news-maker] LLM timeout first attempt >%ss, retry once with %ss",
                    LLM_TIMEOUT_SEC,
                    LLM_TIMEOUT_RETRY_SEC,
                )
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, _infer),
                    timeout=LLM_TIMEOUT_RETRY_SEC,
                )

            text = self._extract_text(raw)
            logger.debug("[news-maker] Local LLM response len: %d", len(str(text) or ""))
            return text
        except asyncio.TimeoutError:
            logger.error("[news-maker] LLM timeout after retry (%ss/%ss)", LLM_TIMEOUT_SEC, LLM_TIMEOUT_RETRY_SEC)
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
            attempts = ["cloudflare"]
        elif provider in ("huggingface", "hf"):
            attempts = ["huggingface"]
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
                    resp = requests.post(NEWS_CF_API_URL, headers=headers, json=payload, timeout=REMOTE_TIMEOUT_SEC)
                    if resp.ok:
                        text = self._coerce_remote_json_text(self._extract_remote_text(resp.json()))
                        if text:
                            logger.info("[news-maker] Remote fallback used: cloudflare")
                            return text
                    else:
                        logger.warning("[news-maker] Cloudflare API error %s", resp.status_code)

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
                    resp = requests.post(NEWS_HF_API_URL, headers=headers, json=payload, timeout=REMOTE_TIMEOUT_SEC)
                    if resp.ok:
                        text = self._coerce_remote_json_text(self._extract_remote_text(resp.json()))
                        if text:
                            logger.info("[news-maker] Remote fallback used: huggingface")
                            return text
                    else:
                        logger.warning("[news-maker] HuggingFace API error %s", resp.status_code)
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
            logger.info("[news-maker] ✅ Relaxed JSON parse success")
            return relaxed

        # 1) Direct strict JSON object
        try:
            direct = json.loads(text)
            normalized = self._normalize_article(direct)
            if normalized:
                logger.info("[news-maker] ✅ Direct JSON parse success")
                return normalized
        except Exception as e:
            logger.info("[news-maker] Direct parse fail: %s", str(e)[:80])

        repaired_direct = self._repair_json_candidate(text)
        if repaired_direct and repaired_direct != text:
            try:
                direct = json.loads(repaired_direct)
                normalized = self._normalize_article(direct)
                if normalized:
                    logger.info("[news-maker] ✅ Repaired direct JSON parse success")
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
                    logger.info("[news-maker] ✅ Codeblock JSON parse success")
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
                    logger.info("[news-maker] ✅ Candidate %d normalized ok", i)
                    return normalized
                logger.info("[news-maker] Candidate %d failed normalize", i)
            elif parse_error is not None:
                logger.info("[news-maker] Candidate %d parse error: %s", i, str(parse_error)[:80])

        # 4) Loose extraction fallback for malformed but key-complete objects.
        loose = self._parse_loose_article_object(text)
        if loose:
            logger.info("[news-maker] ✅ Loose JSON-like parse success")
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

    def _save_article(self, art, category):
        try:
            art = self._sanitize_article_payload(art)
            if not art:
                logger.warning("[news-maker] Invalid/unsanitized article payload, skip save.")
                return

            art = self._ensure_min_content(art, "")
            title = str(art.get('title') or '').strip()
            excerpt = str(art.get('excerpt') or '').strip()
            content = str(art.get('content') or '').strip()
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
    parser.add_argument("--manual", choices=["morning", "evening", "headline", "brief", "landing"], help="Run one-shot manual generation")
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
