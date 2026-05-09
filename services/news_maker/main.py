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
from datetime import datetime
from zoneinfo import ZoneInfo

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.database import db
from config import MODEL_PATH, MODEL_CACHE_DIR
from shared.transport import IPC_MARKET, ZMQSubscriber, install_uvloop

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

class NewsMaker:
    def __init__(self):
        self._running = False
        self._engine = None
        self._engine_enabled = self._init_engine()
        self._last_report_date = {"morning": None, "evening": None}
        self._tickers = {}
        self._prev_tickers = {}
        self._headline_cache = set()  # Track recent headlines to avoid duplicates
        self._db_signals = {}
        self._depth_summary = {}
        self._last_db_refresh_ts = 0.0
        self._market_sub = ZMQSubscriber(IPC_MARKET)

    def _init_engine(self) -> bool:
        if not os.path.exists(MODEL_PATH):
            logger.warning("[news-maker] Model not found: %s", MODEL_PATH)
            return False
        try:
            from litert_lm import Engine
            os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
            self._engine = Engine(MODEL_PATH, cache_dir=MODEL_CACHE_DIR)
            logger.info("[news-maker] ✅ News Engine Ready.")
            return True
        except Exception as exc:
            logger.error("[news-maker] Failed to init Engine: %s", exc)
            return False

    async def start(self):
        self._running = True
        self._market_sub.start()
        logger.info("[news-maker] ✅ Service Started.")
        await asyncio.gather(
            self._scheduler_loop(),
            self._realtime_monitor_loop(),
            self._consume_market_data()
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
                quote_vol = float(t.get("quoteVolume") or 0.0)
                base_vol = float(t.get("baseVolume") or 0.0)
                vol_idr = quote_vol if quote_vol > 0 else base_vol * price

                self._tickers[pair] = {
                    "price": price,
                    "ch_24h": pct,
                    "ch_1h": float(self._tickers.get(pair, {}).get("ch_1h") or 0.0),
                    "vol": vol_idr,
                    "vol_idr": vol_idr,
                }
            self._prev_tickers = dict(self._tickers)
        except Exception as exc:
            logger.warning("[news-maker] Manual market snapshot failed: %s", exc)

    async def run_manual(self, mode, coin_symbol=None, headline_count=3):
        """Run one-shot generation using news_maker service logic (single source)."""
        if not self._engine_enabled:
            logger.error("[news-maker] LLM engine is not ready.")
            return

        self._refresh_db_context(force=True)
        self._load_manual_market_snapshot(coin_symbol=coin_symbol)

        mode = str(mode or "").lower().strip()
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
            sig = self._db_signals.get(pair, {})
            dep = self._depth_summary.get(pair, {})

            price = float(data.get("price") or dep.get("best_bid") or dep.get("best_ask") or 0.0)
            ch_24h = float(data.get("ch_24h") or 0.0)
            ch_1h = float(data.get("ch_1h") or sig.get("ch_1h") or 0.0)
            vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
            score = abs(float(sig.get("score") or data.get("score") or ch_24h or ch_1h))

            move_data = {
                "pct_change": ch_24h,
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
                    self._tickers[pair] = {
                        "price": float(tick.price or 0),
                        "ch_24h": float(tick.change_24h or 0),
                        "vol": float(tick.vol_idr or 0),
                        "vol_idr": float(tick.vol_idr or 0)
                    }
            except: pass

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

            # Enrich live tickers with DB signals for better scoring.
            for pair, sig in self._db_signals.items():
                if pair not in self._tickers:
                    self._tickers[pair] = {
                        "price": 0.0,
                        "ch_24h": 0.0,
                        "vol": 0.0,
                        "vol_idr": 0.0,
                    }
                self._tickers[pair]["ch_5m"] = sig["ch_5m"]
                self._tickers[pair]["ch_15m"] = sig["ch_15m"]
                self._tickers[pair]["ch_1h"] = sig["ch_1h"]
                self._tickers[pair]["volatility"] = sig["volatility"]
                self._tickers[pair]["score"] = sig["score"]
                self._tickers[pair]["trend"] = sig["trend"]
                self._tickers[pair]["signal"] = sig["signal"]

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
                self._refresh_db_context()

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
        if not self._tickers or not self._prev_tickers:
            return None
        
        significant = []
        for pair, current in self._tickers.items():
            # Check 1h change
            ch_1h = float(current.get('ch_1h') or 0)
            ch_24h = float(current.get('ch_24h') or 0)
            vol_idr = float(current.get('vol_idr') or 0)
            
            # Find newsworthy movers (lowered thresholds for more content)
            if abs(ch_1h) > 3 or abs(ch_24h) > 10:  # Reduced thresholds
                score = abs(ch_1h) * 2 + abs(ch_24h)  # Weight 1h moves higher
                if vol_idr > 50000000:  # At least 50M IDR volume
                    significant.append((pair, {
                        'pct_change': ch_24h,
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
        if not self._tickers:
            return None
        
        top = max(
            self._tickers.items(),
            key=lambda x: float(x[1].get('ch_24h') or 0)
        )
        
        pair, data = top
        ch_24h = float(data.get('ch_24h') or 0)
        
        # Only return if it has a decent move (>2%) and volume
        if ch_24h > 2 and float(data.get('vol_idr') or 0) > 30000000:
            return (pair, {
                'pct_change': ch_24h,
                'price': float(data.get('price') or 0),
                'change_1h': float(data.get('ch_1h') or 0),
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
            else:
                logger.warning("[news-maker] Empty/invalid article payload for %s", rtype)
        except Exception as e:
            logger.error("[news-maker] Failed to generate report: %s", e)

    async def _generate_market_headline(self, coin_symbol, move_data):
        """Generate headline for significant market move."""
        if not self._engine_enabled:
            return

        self._refresh_db_context()
        
        direction = "📈 Naik" if move_data['pct_change'] >= 0 else "📉 Turun"
        logger.info("[news-maker] Generating Headline: %s %s (%.1f%%)", coin_symbol.upper(), direction, move_data['pct_change'])

        # Build context about this coin and broader market
        context = self._build_market_context_for_coin(coin_symbol, move_data)
        prompt = self._build_market_headline_prompt(coin_symbol, move_data, context)
        
        try:
            article = await self._ask_llm(prompt)
            if not article:
                logger.warning("[news-maker] Empty headline payload for %s", coin_symbol)
                article = self._build_market_headline_fallback(coin_symbol, move_data)
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
        for pair, data in self._tickers.items():
            ch_24h = float(data.get("ch_24h") or 0.0)
            ch_1h = float(data.get("ch_1h") or 0.0)
            price = float(data.get("price") or 0.0)
            vol_idr = float(data.get("vol_idr") or data.get("vol") or 0.0)
            if price <= 0 or vol_idr < 30_000_000:
                continue
            if abs(ch_24h) < 1.5 and abs(ch_1h) < 0.8:
                continue

            score = abs(ch_24h) + (abs(ch_1h) * 1.5) + min(vol_idr / 1_000_000_000, 5)
            candidates.append((pair, {
                "pct_change": ch_24h,
                "price": price,
                "change_1h": ch_1h,
                "vol_idr": vol_idr,
                "score": score,
            }))

        candidates.sort(key=lambda x: float(x[1].get("score") or 0.0), reverse=True)
        return candidates[:max(1, int(limit))]

    def _build_market_context(self):
        """Build overall market context for briefing."""
        movers = sorted(self._tickers.items(), key=lambda x: abs(x[1].get('ch_24h', 0)), reverse=True)[:5]
        context = "Top Market Movers (24h):\n"
        for pair, data in movers:
            ch_24h = float(data.get('ch_24h') or 0)
            price = float(data.get('price') or 0)
            context += f"- {pair.upper()}: {price:,.0f} IDR ({ch_24h:+.1f}%)\n"
        
        regime = db.get_state("current_market_regime") or "NEUTRAL"
        sentiment = db.get_state("market_sentiment_index") or 50
        context += f"Regime: {regime} | Sentiment: {sentiment}%\n"

        ranked_signals = sorted(
            self._db_signals.items(),
            key=lambda x: abs(float(x[1].get("score") or 0.0)) + abs(float(x[1].get("ch_1h") or 0.0)),
            reverse=True,
        )[:5]
        context += "\nDB Technical Signals (ticker_history):\n"
        if ranked_signals:
            for pair, sig in ranked_signals:
                context += (
                    f"- {pair.upper()}: trend={sig.get('trend','N/A')} signal={sig.get('signal','HOLD')} "
                    f"ch_1h={float(sig.get('ch_1h') or 0.0):+.2f}% score={float(sig.get('score') or 0.0):.2f}\n"
                )
        else:
            context += "- No ticker_history signals\n"

        ranked_depth = sorted(
            self._depth_summary.items(),
            key=lambda x: abs(float(x[1].get("imbalance") or 0.0)) + float(x[1].get("bid_vol") or 0.0) + float(x[1].get("ask_vol") or 0.0),
            reverse=True,
        )[:5]
        context += "\nOrderbook Realtime (market_depth):\n"
        if ranked_depth:
            for pair, dep in ranked_depth:
                context += (
                    f"- {pair.upper()}: spread={float(dep.get('spread_pct') or 0.0):.3f}% "
                    f"imbalance={float(dep.get('imbalance') or 0.0):+.2f} "
                    f"bidVol={float(dep.get('bid_vol') or 0.0):,.0f} "
                    f"askVol={float(dep.get('ask_vol') or 0.0):,.0f}\n"
                )

        return context

    def _build_market_context_for_coin(self, coin_symbol, move_data):
        """Build context specific to one coin movement."""
        # Get top gainers and losers
        all_movers = sorted(
            self._tickers.items(),
            key=lambda x: float(x[1].get('ch_24h') or 0),
            reverse=True
        )
        gainers = all_movers[:3]
        losers = all_movers[-3:]
        
        context = f"Coin Focus: {coin_symbol.upper()}\n"
        context += f"Price: {move_data['price']:,.0f} IDR\n"
        context += f"Change 1h: {move_data['change_1h']:+.1f}% | 24h: {move_data['pct_change']:+.1f}%\n"
        context += f"Volume 24h: {move_data['vol_idr']/1e9:.2f}B IDR\n"
        context += "\nTop Gainers (24h):\n"
        for pair, data in gainers:
            context += f"- {pair.upper()}: {float(data.get('ch_24h') or 0):+.1f}%\n"
        context += "\nTop Losers (24h):\n"
        for pair, data in losers:
            context += f"- {pair.upper()}: {float(data.get('ch_24h') or 0):+.1f}%\n"
        
        regime = db.get_state("current_market_regime") or "NEUTRAL"
        context += f"Market Regime: {regime}\n"

        sig = self._db_signals.get(str(coin_symbol or "").lower(), {})
        context += "\nDB Focus Signal:\n"
        if sig:
            context += (
                f"- trend={sig.get('trend','N/A')} signal={sig.get('signal','HOLD')} "
                f"ch_5m={float(sig.get('ch_5m') or 0.0):+.2f}% "
                f"ch_15m={float(sig.get('ch_15m') or 0.0):+.2f}% "
                f"ch_1h={float(sig.get('ch_1h') or 0.0):+.2f}% "
                f"volatility={float(sig.get('volatility') or 0.0):.4f} "
                f"score={float(sig.get('score') or 0.0):.2f}\n"
            )
        else:
            context += "- No ticker_history signal for this pair\n"

        dep = self._depth_summary.get(str(coin_symbol or "").lower(), {})
        context += "Orderbook Focus:\n"
        if dep:
            context += (
                f"- best_bid={float(dep.get('best_bid') or 0.0):,.0f} "
                f"best_ask={float(dep.get('best_ask') or 0.0):,.0f} "
                f"spread={float(dep.get('spread_pct') or 0.0):.3f}% "
                f"imbalance={float(dep.get('imbalance') or 0.0):+.2f}\n"
            )
        else:
            context += "- No market_depth data for this pair\n"

        return context

    def _build_report_prompt(self, rtype, context):
        title = "Morning Briefing" if rtype == "MORNING_BRIEF" else "Market Review Malam"
        return (
            f"Anda adalah Chief Editor Algovibe Intelligence. Tulis artikel blog profesional dalam Bahasa Indonesia.\n"
            f"Tipe: {title}\n"
            f"Konteks Data:\n{context}\n"
            "Persyaratan Artikel:\n"
            "- Judul: catchy, informatif, 50-80 karakter.\n"
            "- Excerpt: ringkasan 100-150 karakter yang mengecup poin utama.\n"
            f"- Panjang konten: minimal {MIN_NEWS_CONTENT_CHARS} karakter (WAJIB).\n"
            "- Content: DETAIL & LENGKAP (500+ kata). Wajib include:\n"
            "  1. Analisis fundamental pasar hari ini.\n"
            "  2. Level teknikal penting (support, resistance, pivot, ATH/ATL).\n"
            "  3. Momentum indikator (RSI, MACD, Bollinger).\n"
            "  4. Rekomendasi trader: BUY/SELL/HOLD dengan alasan & risk management.\n"
            "  5. Kesimpulan & outlook jangka pendek.\n"
            "- Gaya: Formal Bloomberg-style, pakai tabel/bullet points untuk clarity.\n"
            "Balas HANYA 1 JSON object, tanpa markdown/code block, tanpa teks tambahan.\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

    def _build_market_headline_prompt(self, coin_symbol, move_data, context):
        """Generate prompt for market headline analysis."""
        direction = "naik/hype" if move_data['pct_change'] >= 0 else "turun/rugi"
        return (
            f"Anda adalah Market Analyst Algovibe. Tulis Breaking News pasar kripto dalam Bahasa Indonesia.\n"
            f"Analisis: {coin_symbol.upper()} - {direction.upper()} {abs(move_data['pct_change']):.1f}%\n"
            f"\nKonteks Data:\n{context}\n"
            "Persyaratan Artikel:\n"
            "- Title: Breaking news impactful, 50-80 karakter. Include: coin + action + pct.\n"
            "- Excerpt: Summary 120-180 karakter. Jelaskan: apa geraknya, kenapa significant, dampaknya.\n"
            f"- Panjang konten: minimal {MIN_NEWS_CONTENT_CHARS} karakter (WAJIB).\n"
            "- Content: DETAIL LENGKAP (300+ kata). Wajib include:\n"
            "  1. Konteks pergerakan coin (fundamental trigger, sentiment, news).\n"
            "  2. Technical reaction: price action, support/resistance breach, volume.\n"
            "  3. Comparison: vs gainers/losers lain, market regime (hype vs dump).\n"
            "  4. Risk factors: entry danger, liquidation zones, trend reversal risk.\n"
            "  5. Trading opportunity: scalp entry, swing entry, TP targets, SL placement.\n"
            "  6. Forecast: next 4 hours outlook, key price levels to watch.\n"
            "- Tone: Analytical, urgent tapi balanced. Suggest best action untuk trader.\n"
            "Balas HANYA 1 JSON object tanpa markdown/code block.\n"
            '{"title":"...","excerpt":"...","content":"..."}'
        )

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

    def _build_market_headline_fallback(self, coin_symbol, move_data):
        """Fallback headline when LLM fails."""
        direction = "📈 naik" if move_data['pct_change'] >= 0 else "📉 turun"
        pair = str(coin_symbol or "").lower()
        sig = self._db_signals.get(pair, {})
        dep = self._depth_summary.get(pair, {})

        trend = str(sig.get("trend") or "N/A")
        signal = str(sig.get("signal") or "HOLD")
        ch_5m = float(sig.get("ch_5m") or 0.0)
        ch_15m = float(sig.get("ch_15m") or 0.0)
        ch_1h = float(sig.get("ch_1h") or move_data.get("change_1h") or 0.0)
        volatility = float(sig.get("volatility") or 0.0)
        score = float(sig.get("score") or move_data.get("score") or 0.0)
        spread_pct = float(dep.get("spread_pct") or 0.0)
        imbalance = float(dep.get("imbalance") or 0.0)
        best_bid = float(dep.get("best_bid") or 0.0)
        best_ask = float(dep.get("best_ask") or 0.0)
        bid_vol = float(dep.get("bid_vol") or 0.0)
        ask_vol = float(dep.get("ask_vol") or 0.0)

        title = f"{coin_symbol.upper()} {direction} {abs(move_data['pct_change']):.1f}% - Analisis Teknis"
        excerpt = (
            f"{coin_symbol.upper()} mengalami pergerakan signifikan dengan change {move_data['pct_change']:+.1f}% dalam 24 jam. "
            f"Trend={trend}, signal={signal}, spread={spread_pct:.3f}% dan imbalance={imbalance:+.2f}."
        )
        content = (
            f"Sistem Algovibe mendeteksi pergerakan signifikan pada {coin_symbol.upper()} dengan change {move_data['pct_change']:+.1f}% dalam 24 jam terakhir. "
            f"\n\n**Data Pergerakan:**\n"
            f"- Price: {move_data['price']:,.0f} IDR\n"
            f"- Change 1h: {move_data['change_1h']:+.1f}%\n"
            f"- Change 24h: {move_data['pct_change']:+.1f}%\n"
            f"- Volume 24h: {move_data['vol_idr']/1e9:.2f}B IDR\n\n"
            f"**DB Technical Signal (ticker_history):**\n"
            f"- trend: {trend}\n"
            f"- signal: {signal}\n"
            f"- ch_5m: {ch_5m:+.2f}%\n"
            f"- ch_15m: {ch_15m:+.2f}%\n"
            f"- ch_1h: {ch_1h:+.2f}%\n"
            f"- volatility: {volatility:.4f}\n"
            f"- score: {score:.2f}\n\n"
            f"**Orderbook Realtime (market_depth):**\n"
            f"- best_bid: {best_bid:,.0f}\n"
            f"- best_ask: {best_ask:,.0f}\n"
            f"- spread: {spread_pct:.3f}%\n"
            f"- imbalance: {imbalance:+.2f}\n"
            f"- bid_vol: {bid_vol:,.0f}\n"
            f"- ask_vol: {ask_vol:,.0f}\n\n"
            f"**Analisis Teknis:**\nPergerakan ini menunjukkan volatilitas tinggi. Trader disarankan untuk: "
            f"(1) identifikasi support/resistance kunci, (2) pantau volume untuk konfirmasi trend, "
            f"(3) gunakan stop-loss ketat dengan risk management 1:2, (4) await breakout confirmation sebelum entry.\n\n"
            f"**Rekomendasi:**\n"
            f"Jika spread melebar dan imbalance negatif, hindari entry agresif. "
            f"Jika spread sehat dan imbalance positif konsisten, entry bertahap lebih aman dengan stop-loss disiplin."
        )
        return {
            "title": title,
            "excerpt": excerpt,
            "content": content
        }

    def _ensure_min_content(self, article, context):
        """Ensure article content meets minimum character requirement."""
        if not isinstance(article, dict):
            return article

        content = str(article.get("content") or "").strip()
        if len(content) >= MIN_NEWS_CONTENT_CHARS:
            return article

        ctx = re.sub(r"\s+", " ", str(context or "")).strip()[:1200]
        extension = (
            "\n\n**Rencana Eksekusi Trading (Detail):**\n"
            "1) Skenario bullish: entry bertahap hanya setelah konfirmasi volume dan candle close di atas resistance intraday.\n"
            "2) Skenario netral: fokus range trading dengan ukuran posisi kecil, hindari overtrading saat spread melebar.\n"
            "3) Skenario bearish: utamakan proteksi modal, batasi eksposur leverage, gunakan stop-loss ketat berbasis struktur pasar.\n"
            "4) Manajemen risiko: risiko per posisi maksimal 1-2% ekuitas, gunakan partial take-profit, dan evaluasi ulang jika volatilitas melonjak tiba-tiba.\n"
            "5) Validasi teknikal lanjutan: cek konfluensi trend, momentum, dan likuiditas orderbook sebelum eksekusi lanjutan.\n"
            "\n**Checklist Keputusan:**\n"
            "- Apakah trend dan signal searah timeframe 5m/15m/1h?\n"
            "- Apakah spread masih sehat untuk eksekusi?\n"
            "- Apakah imbalance orderbook mendukung arah posisi?\n"
            "- Apakah target TP/SL sudah realistis terhadap volatilitas terbaru?\n"
            "\n**Konteks Data Pendukung:**\n"
            f"{ctx}\n"
        )

        while len(content) < MIN_NEWS_CONTENT_CHARS:
            content += extension

        article["content"] = content[: max(MIN_NEWS_CONTENT_CHARS, len(content))]
        if not article.get("excerpt"):
            article["excerpt"] = re.sub(r"\s+", " ", content).strip()[:220]
        return article

    async def _ask_llm_raw(self, prompt):
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
            logger.debug("[news-maker] LLM raw response type: %s, len: %d", type(text).__name__, len(str(text) or ""))
            return text
        except asyncio.TimeoutError:
            logger.error("[news-maker] LLM timeout after retry (%ss/%ss)", LLM_TIMEOUT_SEC, LLM_TIMEOUT_RETRY_SEC)
            return ""
        except Exception as e:
            logger.error("[news-maker] LLM Error: %s", e)
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
            title = first_line[:120] or "Algovibe Market Update"
        if not excerpt:
            excerpt = re.sub(r"\s+", " ", content).strip()[:220]

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
            try:
                obj = json.loads(candidate)
                normalized = self._normalize_article(obj)
                if normalized:
                    logger.info("[news-maker] ✅ Candidate %d normalized ok", i)
                    return normalized
                logger.info("[news-maker] Candidate %d failed normalize", i)
            except Exception as e:
                logger.info("[news-maker] Candidate %d parse error: %s", i, str(e)[:80])

        return None

    def _extract_json_candidates(self, text):
        candidates = []
        depth = 0
        start_idx = None
        for i, ch in enumerate(text):
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
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if not clean:
            return None

        recovered = self._parse_json_relaxed(clean)
        if recovered:
            return recovered

        # Avoid saving raw serialized Python objects as title/content.
        if clean.startswith("{") and "'content'" in clean and "'text'" in clean:
            return None
        if self._looks_like_serialized_article(clean):
            return None

        title = clean[:90]
        excerpt = clean[:220]
        return {
            "title": title,
            "excerpt": excerpt,
            "content": clean,
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
