import time
import json
import logging
from datetime import datetime, timedelta
import config
from config import INITIAL_CAPITAL, DAILY_LOSS_LIMIT_PCT
from app.infrastructure.database import db
from app.infrastructure.llm_worker import LLMWorker
from utils.logger import log_message, send_telegram_message

logger = logging.getLogger(__name__)

class MarketRegime:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    CHOPPY  = "CHOPPY"
    CRASH   = "CRASH"
    RANGING = "RANGING"  # [Fix BUG #1] Sebelumnya undefined, menyebabkan AttributeError

class Oracle:
    """
    The 'Consciousness' engine of the bot.
    Responsible for market regime detection, self-health monitoring, and performance learning.
    """
    def __init__(self, market_ws):
        self.market_ws = market_ws
        self.last_regime = None  # Force evaluation on first run
        self.last_regime_update = datetime.now() - timedelta(minutes=5)
        self.weights = db.get_all_weights() or {
            "ai_prob": 1.0, "whale_boost": 1.0,
            "sector_sentiment": 1.0, "imbalance": 1.0
        }
        self.health_status = True
        self.latency_history = []
        self.llm_worker = LLMWorker.get_instance()
        self.pair_boosts = {}  # {pair: {"boost": float, "expiry": datetime}}
        self.last_top_mover_analysis = datetime.now() - timedelta(minutes=10)
        self.pump_cooldowns = {}  # {pair: last_analysis_time}
        self.last_buy_signal_queue = datetime.now() - timedelta(minutes=2)
        self.last_trade_verdict_request = {}  # {pair: datetime} throttling explicit trade-verdict requests

        # ── Consciousness & Self-Learning ──────────────────────────────────
        # lessons: rolling list of lessons dari POST_MORTEM, max 20 entri
        self._lessons: list = []
        self.last_consciousness_update = datetime.now() - timedelta(minutes=31)  # force first update

        # veto cache: {pair: {verdict, confidence, reason, ts}}
        # Diisi saat LLM structured result masuk; dibaca oleh check_llm_veto()
        self._veto_cache: dict = {}
        
        # [Senior Fix] Non-blocking tracking
        self.active_analysis: dict = {}   # {pair: task_id}
        self.analysis_results: dict = {}  # {pair: {verdict, confidence, reason, time}}

    # ── Rich Market Context Builder ────────────────────────────────────────────

    def _build_rich_market_context(self, pair: str, prices: list, tech: dict, conn) -> dict:
        """
        Membangun konteks pasar lengkap (9 tier) untuk disuguhkan ke LLM.
        Mengembalikan dict ctx berisi semua data terformat siap pakai di prompt.
        """
        pair = pair.lower()
        ctx = {}

        try:
            last_price = float(prices[-1]) if prices else 0.0
            ws_trades_raw = self.market_ws.get_trades(pair) or []
            ws_ticker = self.market_ws.get_tickers().get(pair, {})

            # ── Tier 1a: VWAP ──────────────────────────────────────────────
            vwap = 0.0
            if len(ws_trades_raw) >= 3:
                try:
                    total_pv = sum(float(t[4]) * abs(float(t[6])) for t in ws_trades_raw if len(t) > 6)
                    total_v  = sum(abs(float(t[6])) for t in ws_trades_raw if len(t) > 6)
                    vwap = total_pv / total_v if total_v > 0 else 0.0
                except Exception:
                    vwap = 0.0
            vwap_signal = "N/A"
            if vwap > 0 and last_price > 0:
                diff_pct = (last_price - vwap) / vwap * 100
                if diff_pct > 0.5:
                    vwap_signal = f"ABOVE VWAP +{diff_pct:.2f}% (bullish intraday)"
                elif diff_pct < -0.5:
                    vwap_signal = f"BELOW VWAP {diff_pct:.2f}% (bearish intraday)"
                else:
                    vwap_signal = f"AT VWAP {diff_pct:+.2f}% (neutral)"
            ctx['vwap_signal'] = vwap_signal

            # ── Tier 1b: Trade Flow Imbalance (50 trades) + Whale Prints ───
            buy_count = sell_count = 0
            large_trades = []
            for t in ws_trades_raw[:50]:
                try:
                    side    = str(t[3]).lower() if len(t) > 3 else ''
                    vol_idr = float(t[5]) if len(t) > 5 else 0
                    if side == 'buy':
                        buy_count += 1
                    elif side == 'sell':
                        sell_count += 1
                    if vol_idr > 50_000_000:
                        large_trades.append(f"{side.upper()} Rp{vol_idr/1e6:.1f}jt")
                except Exception:
                    continue
            total_flow = buy_count + sell_count
            flow_pct = round(buy_count / total_flow * 100, 1) if total_flow > 0 else 50.0
            flow_label = "STRONG BUY FLOW" if flow_pct >= 65 else ("STRONG SELL FLOW" if flow_pct <= 35 else "BALANCED FLOW")
            ctx['trade_flow'] = f"{flow_pct}% buy ({buy_count}/{total_flow} trades) — {flow_label}"
            ctx['whale_prints'] = ", ".join(large_trades[-3:]) if large_trades else "None detected"

            # ── Tier 1c: Price Position in 24h Range ───────────────────────
            high_24h = float(ws_ticker.get('high', 0) or 0)
            low_24h  = float(ws_ticker.get('low',  0) or 0)
            if high_24h > low_24h and last_price > 0:
                pos_pct = round((last_price - low_24h) / (high_24h - low_24h) * 100, 1)
                if pos_pct >= 80:
                    pos_label = "NEAR 24H HIGH — overbought zone"
                elif pos_pct <= 20:
                    pos_label = "NEAR 24H LOW — value zone"
                else:
                    pos_label = "MID RANGE"
                ctx['range_position'] = f"{pos_pct}% of 24h range ({pos_label})"
            else:
                ctx['range_position'] = "N/A"

            # ── Tier 2a: Per-Pair Win/Loss History ─────────────────────────
            try:
                recent_pair_trades = conn.execute(
                    "SELECT pnl_idr FROM trade_history WHERE pair = ? "
                    "AND reason != 'BACKTEST' ORDER BY id DESC LIMIT 5",
                    (pair,)
                ).fetchall()
                if recent_pair_trades:
                    wins   = sum(1 for r in recent_pair_trades if float(r['pnl_idr'] or 0) > 0)
                    losses = len(recent_pair_trades) - wins
                    streak = " ".join("W" if float(r['pnl_idr'] or 0) > 0 else "L"
                                      for r in recent_pair_trades)
                    ctx['pair_history'] = f"Last {len(recent_pair_trades)} trades: [{streak}] ({wins}W/{losses}L)"
                else:
                    ctx['pair_history'] = "No prior trades on this pair"
            except Exception:
                ctx['pair_history'] = "N/A"

            # ── Tier 2b: Volume Anomaly Score ──────────────────────────────
            trade_count = len(ws_trades_raw)
            activity_ratio = trade_count / 20.0  # 20 trades/min = baseline normal
            if activity_ratio > 3:
                vol_anomaly = f"VERY HIGH activity ({trade_count} recent trades, {activity_ratio:.1f}x normal)"
            elif activity_ratio > 1.5:
                vol_anomaly = f"ABOVE NORMAL activity ({trade_count} recent trades, {activity_ratio:.1f}x)"
            elif activity_ratio < 0.5:
                vol_anomaly = f"LOW activity ({trade_count} recent trades, thin market)"
            else:
                vol_anomaly = f"Normal activity ({trade_count} recent trades)"
            ctx['volume_anomaly'] = vol_anomaly

            # ── Tier 2c: Price Momentum (5m, 15m, 1h) ──────────────────────
            try:
                ticker_row = conn.execute(
                    "SELECT ch_5m, ch_15m, ch_1h FROM ticker_history WHERE pair = ?",
                    (pair,)
                ).fetchone()
                if ticker_row:
                    ctx['momentum'] = (
                        f"5m={float(ticker_row['ch_5m'] or 0):+.2f}% | "
                        f"15m={float(ticker_row['ch_15m'] or 0):+.2f}% | "
                        f"1h={float(ticker_row['ch_1h'] or 0):+.2f}%"
                    )
                else:
                    ctx['momentum'] = "N/A"
            except Exception:
                ctx['momentum'] = "N/A"

            # ── Tier 2d: Sector + Market Regime ────────────────────────────
            try:
                from config import SECTOR_MAP
                sector = SECTOR_MAP.get(pair, "Unknown")
                s_val  = db.get_latest_sector_sentiment(sector) if sector != "Unknown" else 0
                s_lbl  = "Positive" if s_val > 0.5 else ("Negative" if s_val < -0.5 else "Neutral")
                ctx['sector'] = f"{sector} ({s_lbl}, avg {s_val:+.2f}%)"
            except Exception:
                ctx['sector'] = "Unknown"
            regime = db.get_state("market_regime") or "UNKNOWN"
            fear_greed = db.get_state("market_sentiment_index") or "50"
            ctx['market_regime'] = f"{regime} | Fear/Greed: {fear_greed}/100"

            # ── Tier 3a: Time of Day Session ───────────────────────────────
            hour = datetime.now().hour
            if 1 <= hour < 8:
                session = "Asia Session (Indodax peak liquidity)"
            elif 8 <= hour < 16:
                session = "Europe Session (mixed liquidity)"
            elif 16 <= hour < 23:
                session = "US Session (global volatility peak)"
            else:
                session = "Off-Hours (thin liquidity, wider spreads)"
            ctx['session'] = f"{hour:02d}:xx — {session}"

            # ── Tier 3b: LLM Lessons Memory (max 3 terbaru) ────────────────
            if self._lessons:
                ctx['lessons'] = " | ".join(self._lessons[-3:])
            else:
                ctx['lessons'] = "No lessons accumulated yet"

        except Exception as e:
            logger.warning(f"[Oracle] _build_rich_market_context error for {pair}: {e}")

        return ctx

    def _format_rich_prompt(self, pair: str, last_price: float, tech: dict,
                             ob_ctx: dict, ctx: dict, task_type: str) -> str:
        """
        Merakit prompt akhir dari semua konteks yang telah dikumpulkan dengan format sederhana.
        """
        return (
            f"=== QUANTITATIVE SCALPING ANALYSIS: {pair.upper()} ===\n"
            f"Current Price: {last_price:.2f}\n"
            f"VWAP Signal: {ctx.get('vwap_signal', 'N/A')}\n"
            f"Momentum: {ctx.get('momentum', 'N/A')}\n"
            f"24h Range Position: {ctx.get('range_position', 'N/A')}\n"
            f"\n[TECHNICAL INDICATORS]\n"
            f"RSI: {tech.get('rsi','N/A')}\n"
            f"Volatility: {tech.get('volatility','N/A')} percent\n"
            f"Technical Signal: {tech.get('signal', tech.get('verdict','N/A'))}\n"
            f"Orderbook Buy Pressure: {ob_ctx.get('buy_pressure', 50):.1f} percent\n"
            f"\n[ORDER BOOK MICROSTRUCTURE]\n"
            f"Bid-Ask Imbalance: {ob_ctx.get('imbalance', 1.0):.2f} (ratio)\n"
            f"Spread: {ob_ctx.get('spread_pct', 0):.3f} percent\n"
            f"Bid Depth (IDR): {ob_ctx.get('bid_depth', 0):.0f}\n"
            f"Ask Depth (IDR): {ob_ctx.get('ask_depth', 0):.0f}\n"
            f"Best Bid: {ob_ctx.get('best_bid', 0):.2f}\n"
            f"Best Ask: {ob_ctx.get('best_ask', 0):.2f}\n"
            f"\n[MARKET ACTIVITY AND FLOW]\n"
            f"Trade Flow Snapshot: {ctx.get('trade_flow', 'N/A')}\n"
            f"Whale Prints: {ctx.get('whale_prints', 'None')}\n"
            f"Volume Anomaly: {ctx.get('volume_anomaly', 'N/A')}\n"
            f"Recent Trades History: {ob_ctx.get('trade_summary', 'N/A')}\n"
            f"\n[MARKET CONTEXT]\n"
            f"Market Regime: {ctx.get('market_regime', 'N/A')}\n"
            f"Sector Information: {ctx.get('sector', 'N/A')}\n"
            f"Trading Session: {ctx.get('session', 'N/A')}\n"
            f"\n[BOT MEMORY]\n"
            f"Previous Analysis for this pair: {ctx.get('pair_history', 'N/A')}\n"
            f"Recent Lessons: {ctx.get('lessons', 'None')}\n"
            f"\n[TASK]\n"
            f"{'Confirm or reject this BUY signal.' if task_type == 'auto' else 'Provide a sharp tactical analysis.'} "
            f"Using ALL the data provided above. Output your verdict (BUY/HOLD/AVOID) followed by a short explanation, "
            f"Profit Target, Stop Loss, and estimated time (ETA).\n"
        )

    def _prepare_rich_prompt(self, pair: str, task_type: str = 'auto') -> str:
        """
        Helper untuk mengumpulkan data (History, OB, Tech, Context) dan
        merakitnya menjadi prompt kaya untuk LLM.
        """
        from app.domain.technicals import get_technical_summary, derive_trade_signal
        import sqlite3
        import json

        pair = pair.lower()
        conn = None
        try:
            # Gunakan path DB aktif dari router (live/dryrun), bukan DB_PATH legacy
            conn = sqlite3.connect(db.db_path, timeout=30)
            conn.row_factory = sqlite3.Row

            # 1. Ticker History
            row = conn.execute(
                "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
            ).fetchone()
            prices = json.loads(row['history_json']) if (row and row['history_json']) else []
            
            # 2. Technicals
            tech = get_technical_summary(prices)
            sig = derive_trade_signal(tech)
            tech['signal'] = sig['verdict']
            tech['reason'] = sig.get('reason', '')
            tech['verdict'] = sig['verdict'] # Fallback
            tech['tech_confidence'] = sig.get('confidence', 0)

            # 3. Order Book Microstructure
            depth_rows = conn.execute(
                "SELECT price, vol, is_bid FROM market_depth WHERE pair = ?", (pair,)
            ).fetchall()
            bids = [(r['price'], r['vol']) for r in depth_rows if r['is_bid'] == 1]
            asks = [(r['price'], r['vol']) for r in depth_rows if r['is_bid'] == 0]
            
            total_bid = sum(v for _, v in bids)
            total_ask = sum(v for _, v in asks)
            total_ob  = total_bid + total_ask
            best_bid  = max((p for p, _ in bids), default=0)
            best_ask  = min((p for p, _ in asks), default=0)
            spread_pct = round(((best_ask - best_bid) / best_bid * 100), 3) if best_bid > 0 else 0
            
            # 4. Recent Trades (Snapshot from WS)
            ws_trades_snap = self.market_ws.get_trades(pair) or []
            t_lines = []
            for t in ws_trades_snap[:5]:
                try:
                    side  = str(t[3]).upper() if len(t) > 3 else '?'
                    price = float(t[4]) if len(t) > 4 else 0
                    vol   = float(t[6]) if len(t) > 6 else 0
                    t_lines.append(f"{side}@Rp{price:,.0f}({vol:.4f})")
                except Exception: continue
            trade_summary = " | ".join(t_lines) if t_lines else "N/A"

            ob_ctx = {
                'buy_pressure': round(total_bid / total_ob * 100, 1) if total_ob > 0 else 50.0,
                'imbalance':    round(total_bid / total_ask, 2) if total_ask > 0 else 1.0,
                'bid_depth':    total_bid,
                'ask_depth':    total_ask,
                'best_bid':     best_bid,
                'best_ask':     best_ask,
                'spread_pct':   spread_pct,
                'trade_summary': trade_summary,
            }

            # 5. Build context
            ctx = self._build_rich_market_context(pair, prices, tech, conn)
            last_price = float(prices[-1]) if prices else 0
            
            return self._format_rich_prompt(pair, last_price, tech, ob_ctx, ctx, task_type=task_type)
        except sqlite3.DatabaseError as e:
            # Fail-safe: jika SQLite korup/malformed, jangan matikan bot.
            err = str(e).lower()
            if "malformed" in err or "database disk image is malformed" in err:
                log_message(
                    f"⚠️ [Oracle] SQLite malformed on {db.db_path}. Rich prompt fallback aktif untuk {pair}."
                )
            else:
                log_message(f"⚠️ [Oracle] SQLite error in rich prompt for {pair}: {e}")

            ws_ticker = self.market_ws.get_tickers().get(pair, {}) if self.market_ws else {}
            ws_last = float(ws_ticker.get('last', 0) or 0)
            tech = {
                'rsi': 'N/A',
                'volatility': 'N/A',
                'signal': 'HOLD',
                'reason': 'fallback_no_db',
                'verdict': 'HOLD',
                'tech_confidence': 0,
            }
            ob_ctx = {
                'buy_pressure': 50.0,
                'imbalance': 1.0,
                'bid_depth': 0,
                'ask_depth': 0,
                'best_bid': 0,
                'best_ask': 0,
                'spread_pct': 0,
                'trade_summary': 'N/A (db unavailable)',
            }
            ctx = self._build_rich_market_context(pair, [], tech, None)
            return self._format_rich_prompt(pair, ws_last, tech, ob_ctx, ctx, task_type=task_type)
        finally:
            if conn is not None:
                conn.close()

    def auto_queue_buy_candidates(self, ranked_signals):
        """
        Setiap 60 detik, ambil top-5 koin dengan sinyal BUY terkuat dan
        antri ke LiteRT-LM untuk deep reasoning otomatis.
        ranked_signals: list of dict {pair, tech_verdict, tech_confidence, ...}
        """
        now = datetime.now()
        if (now - self.last_buy_signal_queue).total_seconds() < 60:
            return  # cooldown 60 detik
        self.last_buy_signal_queue = now

        from app.domain.technicals import get_technical_summary, derive_trade_signal
        import sqlite3

        # Ambil top 5 koin dengan verdict BUY dan confidence tertinggi
        buy_candidates = sorted(
            [r for r in ranked_signals if r.get('tech_verdict') == 'BUY'],
            key=lambda x: x.get('tech_confidence', 0),
            reverse=True
        )[:5]

        if not buy_candidates:
            return

        try:
            for candidate in buy_candidates:
                pair = candidate['pair']
                if pair in self.pump_cooldowns:
                    age = (now - self.pump_cooldowns[pair]).total_seconds()
                    if age < 300:
                        continue

                # Gunakan helper baru untuk membangun prompt kaya
                prompt = self._prepare_rich_prompt(pair, task_type='auto')
                self.llm_worker.query_structured(f"ANALYSIS:{pair}", prompt)
                self.pump_cooldowns[pair] = now
                logger.info(f"[AutoQueue] LLM rich-context queued for {pair}")
        finally:
            pass

    def process_llm_insights(self):
        """Called periodically to fetch async LLM insights."""
        # 1. Check for manual analysis requests from DB (triggered by Dashboard)
        on_demand_pair = db.get_state("trigger_analysis_pair")
        # Guard: hanya proses jika pair valid (bukan None, string kosong, atau literal "None")
        if on_demand_pair and isinstance(on_demand_pair, str) and on_demand_pair.strip().lower() not in ('', 'none'):
            db.save_state("trigger_analysis_pair", None)
            try:
                import sqlite3
                from app.domain.technicals import get_technical_summary
                # Gunakan path DB aktif dari router (live/dryrun), bukan DB_PATH legacy
                conn = sqlite3.connect(db.db_path, timeout=30)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT history_json FROM ticker_history WHERE pair = ?",
                        (on_demand_pair,)
                    ).fetchone()
                    prices = json.loads(row['history_json']) if (row and row['history_json']) else []
                    tech = get_technical_summary(prices)

                    depth = conn.execute(
                        "SELECT price, vol, is_bid FROM market_depth WHERE pair = ?",
                        (on_demand_pair,)
                    ).fetchall()
                    bids = [(r['price'], r['vol']) for r in depth if r['is_bid'] == 1]
                    asks = [(r['price'], r['vol']) for r in depth if r['is_bid'] == 0]
                    total_bid_vol = sum(v for _, v in bids)
                    total_ask_vol = sum(v for _, v in asks)
                    total_vol     = total_bid_vol + total_ask_vol
                    best_bid      = max((p for p, _ in bids), default=0)
                    best_ask      = min((p for p, _ in asks), default=0)
                    spread_pct    = round(((best_ask - best_bid) / best_bid * 100), 3) if best_bid > 0 else 0

                    ws_trades_snap = self.market_ws.get_trades(on_demand_pair) or []
                    t_lines = []
                    for t in ws_trades_snap[:5]:
                        try:
                            side  = str(t[3]).upper() if len(t) > 3 else '?'
                            price = float(t[4]) if len(t) > 4 else 0
                            vol   = float(t[6]) if len(t) > 6 else 0
                            t_lines.append(f"{side}@Rp{price:,.0f}({vol:.4f})")
                        except Exception:
                            continue
                    trade_summary = " | ".join(t_lines) if t_lines else "N/A"

                    ob_ctx = {
                        'buy_pressure': round(total_bid_vol / total_vol * 100, 1) if total_vol > 0 else 50.0,
                        'imbalance':    round(total_bid_vol / total_ask_vol, 2) if total_ask_vol > 0 else 1.0,
                        'bid_depth':    total_bid_vol,
                        'ask_depth':    total_ask_vol,
                        'best_bid':     best_bid,
                        'best_ask':     best_ask,
                        'spread_pct':   spread_pct,
                        'trade_summary': trade_summary,
                    }

                    # Bangun rich context (9 tier) — conn masih terbuka untuk query DB
                    ctx = self._build_rich_market_context(on_demand_pair, prices, tech, conn)
                finally:
                    conn.close()

                last_price = float(prices[-1]) if prices else 0
                prompt = self._format_rich_prompt(
                    on_demand_pair, last_price, tech, ob_ctx, ctx, task_type='ondemand'
                )
                self.llm_worker.query_structured(f"ANALYSIS:{on_demand_pair}", prompt)
                logger.info(f"[Oracle] Rich-context on-demand analysis queued for {on_demand_pair}")
            except Exception as e:
                logger.warning(f"[Oracle] process_llm_insights on-demand error: {e}")

        # 2. Poll ALL results from the worker and filter by prefix
        self.llm_worker.check_results()
        for task_id in list(self.llm_worker.results.keys()):
            task = self.llm_worker.results[task_id]
            t_type = task["type"]
            t_res = task["result"]
            
            if t_type == "FAKE_PUMP_ANALYSIS":
                log_message(f"🤖 [LiteRT Insight] Fake Pump: {t_res}")
                db.save_state("latest_llm_insight", f"FAKE_PUMP|{t_res}")
                hero_msg = f"**Market Alert**: {t_res}"
                db.save_state("hero_ai_insight", hero_msg)
                db.log_system_event("GLOBAL", "AI_INSIGHT", hero_msg, "WARNING")
                # [Fix BUG #4] TIDAK apply sentiment ke pair manapun — konteks global, bukan pair spesifik
                self.llm_worker.results.pop(task_id)
            elif t_type == "REGIME_ANALYSIS":
                log_message(f"🤖 [LiteRT Insight] Regime Shift: {t_res}")
                db.save_state("latest_llm_insight", f"REGIME|{t_res}")
                hero_msg = f"**Regime Intelligence**: {t_res}"
                db.save_state("hero_ai_insight", hero_msg)
                db.log_system_event("GLOBAL", "AI_INSIGHT", hero_msg, "INFO")
                # [Fix BUG #4] TIDAK apply sentiment — ini analisis market global, bukan pair spesifik
                self.llm_worker.results.pop(task_id)
            elif t_type.startswith("TOP_MOVER:") or t_type.startswith("ANALYSIS:") or t_type.startswith("PUMP_ALERT:"):
                if t_type.startswith("PUMP_ALERT:"):
                    label = "PUMP_ALERT"
                elif t_type.startswith("ANALYSIS:"):
                    label = "ANALYSIS"
                else:
                    label = "TOP_MOVER"

                pair_name = t_type.split(":")[1].lower()

                # ── Parse structured vs text result ──────────────────────
                is_json = isinstance(t_res, str) and t_res.strip().startswith('{')
                structured = LLMWorker.parse_structured_result(t_res) if is_json else None
                is_structured = structured is not None and (structured.get("confidence", 0) > 0 or structured.get("verdict") in ("BUY","AVOID","HOLD"))
                
                if is_structured:
                    log_message(
                        f"🤖 [LiteRT Insight] {label} {pair_name}: "
                        f"{structured['verdict']} ({structured['confidence']}%) — {structured['reason']}"
                    )
                    # Narratif untuk Hero Section
                    hero_msg = f"**{pair_name.upper()} Insight**: {structured['reason']}. **{structured['verdict']}**."
                else:
                    log_message(f"🤖 [LiteRT Insight] {label} {pair_name}: {t_res}")
                    hero_msg = f"**{pair_name.upper()} Analysis**: {t_res}"
                
                db.save_state("latest_llm_insight", f"{label}:{pair_name}|{t_res}")
                db.save_state("hero_ai_insight", hero_msg)
                db.log_system_event(pair_name, "AI_INSIGHT", hero_msg, "INFO")
                db.save_state(f"llm_reasoning_{pair_name}", t_res)

                # ── [Senior Sync] Update Trade DNA Table ──────────────────
                if is_structured:
                    db.update_llm_reasoning(
                        pair_name, 
                        structured.get('verdict', 'HOLD'),
                        float(structured.get('confidence', 0)),
                        structured.get('reason', t_res)
                    )
                    # Log to System Events for Dashboard Visibility
                    db.log_system_event(
                        pair_name, 
                        "AI_ANALYSIS", 
                        f"Audit synced: {structured['verdict']} ({structured['confidence']}%)", 
                        'SUCCESS'
                    )

                # ── Veto Cache: simpan untuk check_llm_veto() ─────────────
                if is_structured and label in ("ANALYSIS", "PUMP_ALERT"):
                    self._veto_cache[pair_name] = {
                        **structured,
                        "ts": datetime.now()
                    }
                    db.save_state(f"llm_veto_{pair_name}", t_res)  # persist lintas restart
                    # [Fix Bug#5] Simpan timestamp agar DB veto bisa expire
                    db.save_state(f"llm_veto_ts_{pair_name}", datetime.now().isoformat())

                # ── Sentiment (hanya pair spesifik, Fix BUG #4) ──────────
                self._update_llm_sentiment(t_res, pair_name)
                
                # ── Update ETA with Math Calibration ───────────────────
                if is_structured:
                    # Ambil harga terakhir dari DB untuk kalibrasi math ETA
                    try:
                        # [Fix] Gunakan limit lebih banyak (50) agar math ETA punya data untuk velocity
                        # Dan perbaiki KeyError: 'history_json' karena get_ticker_history() mengembalikan list of dict harga.
                        rows = db.get_ticker_history(pair_name, limit=50)
                        if rows:
                            prices = [r['last'] for r in rows]
                            current_price = prices[-1]
                            target_tp = current_price * (1 + (structured.get('take_profit_pct', 2.0)/100))
                            math_eta = self._calculate_math_eta(pair_name, target_tp, current_price, prices)
                            
                            # Calibrate: Rata-rata AI vs Math (Bobot 60% Math, 40% AI)
                            ai_eta = structured.get('eta_minutes', 15)
                            calibrated_eta = int((math_eta * 0.6) + (ai_eta * 0.4))
                            
                            # Update structured result dengan hasil kalibrasi
                            structured['eta_minutes'] = calibrated_eta
                            
                            # Update veto cache & DB
                            if pair_name in self._veto_cache:
                                self._veto_cache[pair_name]['eta_minutes'] = calibrated_eta
                            
                            # Log calibration
                            logger.info(f"[Oracle] ETA Calibrated for {pair_name}: AI={ai_eta}m, Math={math_eta}m -> Final={calibrated_eta}m")
                    except Exception as e:
                        logger.warning(f"[Oracle] ETA Calibration error for {pair_name}: {e}")

                self.llm_worker.results.pop(task_id)
            elif t_type == "POST_MORTEM":
                log_message(f"🤖 [LiteRT Insight] Post-Mortem: {t_res}")
                db.save_state("latest_llm_insight", f"POST_MORTEM|{t_res}")
                # ── Self-Learning: masukkan lesson ke consciousness ──
                self._accumulate_lesson(t_res)
                # Force consciousness refresh agar lesson langsung terpakai
                self.last_consciousness_update = datetime.now() - timedelta(minutes=31)
                self.llm_worker.results.pop(task_id)
            
    def _update_llm_sentiment(self, text: str, pair: str = "GLOBAL"):
        """
        Extract sentiment dan apply ke pair SPESIFIK selama 5 menit.
        [Fix BUG #4] Hanya boleh dipanggil untuk analisis pair tertentu,
        bukan untuk global commentary (REGIME, FAKE_PUMP, POST_MORTEM).
        """
        if pair == "GLOBAL":
            # Safety guard: jangan apply global sentiment ke semua pair
            logger.debug("[Oracle] _update_llm_sentiment called with GLOBAL — skipped.")
            return
        
        text_lower = text.lower()
        boost = 0.0
        
        # Hitung weighted sentiment: kata positif dan negatif ditimbang
        positive_hits = sum(1 for w in ['bullish', 'opportunity', 'buy', 'breakout', 'strong', 'uptrend'] if w in text_lower)
        negative_hits = sum(1 for w in ['bearish', 'risk', 'avoid', 'dump', 'weak', 'fake', 'trap', 'sell', 'downtrend'] if w in text_lower)
        
        # Hanya apply boost jika ada konsensus (lebih banyak sinyal satu arah)
        if positive_hits > negative_hits and positive_hits >= 2:
            boost = min(0.15, positive_hits * 0.04)   # Max +0.15
        elif negative_hits > positive_hits and negative_hits >= 2:
            boost = max(-0.20, -(negative_hits * 0.05))  # Max -0.20
        
        expiry = datetime.now() + timedelta(minutes=5)
        self.pair_boosts[pair.lower()] = {"boost": boost, "expiry": expiry}
        logger.debug(f"[Oracle] Sentiment boost for {pair}: {boost:+.2f} (pos={positive_hits}, neg={negative_hits})")

    # ── LLM Veto Power ────────────────────────────────────────────────────────

    def check_llm_veto(self, pair: str) -> tuple:
        """
        Cek apakah LLM memveto entry untuk pair ini.
        Returns: (vetoed: bool, reason: str)

        Veto aktif jika:
        - LLM verdict == AVOID dengan confidence >= 75%
        - Analisis masih fresh (< 10 menit in-memory, < 2 jam di DB)

        Jika tidak ada analisis LLM → TIDAK veto (bot tetap jalan berdasarkan teknikal).
        """
        pair_lower = pair.lower()
        LLM_VETO_THRESHOLD   = 75   # Minimum confidence untuk veto
        LLM_VETO_TTL_MINUTES = 10   # In-memory cache TTL
        LLM_DB_VETO_TTL_MIN  = 120  # [Fix Bug#5] DB veto TTL: 2 jam (bukan selamanya!)

        # 1. Cek in-memory cache dulu (lebih cepat)
        cached = self._veto_cache.get(pair_lower)
        if cached:
            age_min = (datetime.now() - cached["ts"]).total_seconds() / 60
            if age_min <= LLM_VETO_TTL_MINUTES:
                if cached["verdict"] == "AVOID" and cached["confidence"] >= LLM_VETO_THRESHOLD:
                    return True, f"LLM VETO ({cached['confidence']}%): {cached['reason']}"
                return False, cached["reason"]

        # 2. Fallback: cek DB (persist lintas restart/insight_thread delay)
        # [Fix Bug#5] Simpan timestamp di key terpisah agar bisa enforce TTL
        try:
            raw = db.get_state(f"llm_veto_{pair_lower}")
            veto_ts_str = db.get_state(f"llm_veto_ts_{pair_lower}")
            if raw:
                # Cek usia veto dari DB
                if veto_ts_str:
                    try:
                        veto_ts = datetime.fromisoformat(veto_ts_str)
                        veto_age_min = (datetime.now() - veto_ts).total_seconds() / 60
                        if veto_age_min > LLM_DB_VETO_TTL_MIN:
                            # Veto sudah expired, hapus dari DB
                            db.save_state(f"llm_veto_{pair_lower}", None)
                            db.save_state(f"llm_veto_ts_{pair_lower}", None)
                            logger.info(f"[Oracle] DB Veto expired for {pair} (age={veto_age_min:.0f}m).")
                            return False, "Veto expired"
                    except (ValueError, TypeError):
                        pass  # Timestamp rusak, abaikan

                structured = LLMWorker.parse_structured_result(raw)
                if structured["verdict"] == "AVOID" and structured["confidence"] >= LLM_VETO_THRESHOLD:
                    logger.info(f"[Oracle] LLM DB-Veto for {pair}: {structured['reason']}")
                    return True, f"LLM VETO (DB) ({structured['confidence']}%): {structured['reason']}"
        except Exception:
            pass

        return False, "No LLM verdict"

    def get_pair_trade_verdict(self, pair: str, max_age_minutes: int = 15) -> dict | None:
        """
        Ambil verdict trading terstruktur terbaru untuk pair.
        Mengutamakan cache in-memory, fallback ke DB, dengan TTL agar tidak pakai opini usang.
        """
        pair_lower = pair.lower()

        # 1) Fresh in-memory verdict dari insight thread
        cached = self._veto_cache.get(pair_lower)
        if cached:
            age_min = (datetime.now() - cached.get("ts", datetime.now())).total_seconds() / 60
            if age_min <= max_age_minutes:
                return {
                    "verdict": cached.get("verdict", "HOLD"),
                    "confidence": int(cached.get("confidence", 0)),
                    "reason": cached.get("reason", ""),
                    "source": "cache",
                    "age_min": round(age_min, 2),
                }

        # 2) Fallback DB verdict (persist lintas restart)
        try:
            raw = db.get_state(f"llm_veto_{pair_lower}")
            ts_raw = db.get_state(f"llm_veto_ts_{pair_lower}")
            if raw and ts_raw:
                ts = datetime.fromisoformat(ts_raw)
                age_min = (datetime.now() - ts).total_seconds() / 60
                if age_min <= max_age_minutes:
                    parsed = LLMWorker.parse_structured_result(raw)
                    return {
                        "verdict": parsed.get("verdict", "HOLD"),
                        "confidence": int(parsed.get("confidence", 0)),
                        "reason": parsed.get("reason", ""),
                        "source": "db",
                        "age_min": round(age_min, 2),
                    }
        except Exception:
            pass

        return None

    def request_trade_verdict(self, pair, ctx):
        """
        [Non-Blocking] Meminta analisis mendalam dari LLM.
        Jika hasil belum ada, bot akan mengantrekan tugas dan mengembalikan 'ANALYZING'.
        """
        if not self.llm_worker or not self.llm_worker.is_active:
            return "HOLD", 0, "LLM Offline"

        pair_lower = pair.lower()

        # 0. [Fix Bug#1] Cek _veto_cache TERLEBIH DAHULU agar tidak bypass veto
        #    _veto_cache diisi oleh auto_queue & insight_thread (jalur lain dari analysis_results)
        cached_veto = self._veto_cache.get(pair_lower)
        if cached_veto:
            age_min = (datetime.now() - cached_veto["ts"]).total_seconds() / 60
            if age_min <= 10:  # TTL sama dengan check_llm_veto
                return cached_veto["verdict"], cached_veto["confidence"], cached_veto["reason"]

        # 1. Cek hasil yang sudah matang di Oracle memory (analysis_results)
        if pair_lower in self.analysis_results:
            res = self.analysis_results[pair_lower]
            # Expire cache setelah 5 menit (analisis lama dianggap basi)
            if (datetime.now() - res['time']).total_seconds() < 300:
                return res['verdict'], res['confidence'], res['reason']
        
        # 2. Cek apakah ada task yang sedang berjalan (active_analysis)
        if pair_lower in self.active_analysis:
            task_id = self.active_analysis[pair_lower]
            res = self.llm_worker.get_result(task_id)
            if res:
                # Hasil baru saja selesai di LLMWorker!
                del self.active_analysis[pair_lower]
                parsed = self.llm_worker.parse_structured_result(res['result'])
                self.analysis_results[pair_lower] = {
                    "verdict": parsed['verdict'],
                    "confidence": parsed['confidence'],
                    "reason": parsed['reason'],
                    "time": datetime.now()
                }
                # [Fix Bug#1] Sinkronkan ke _veto_cache agar jalur check_llm_veto konsisten
                self._veto_cache[pair_lower] = {
                    **parsed,
                    "ts": datetime.now()
                }
                return parsed['verdict'], parsed['confidence'], parsed['reason']
            else:
                # Masih antre/proses di background LLM process
                return "ANALYZING", 0, "Deep reasoning in progress..."

        # 3. Jika belum ada di antrean maupun di hasil, buat task baru
        # Gunakan throttling agar tidak spamming LLM (max 1 request per 2 menit)
        last_req = self.last_trade_verdict_request.get(pair_lower)
        if last_req and (datetime.now() - last_req).total_seconds() < 120:
             return "HOLD", 0, "Wait for cooldown..."

        # Bangun konteks untuk LLM
        prompt = self._build_rich_market_context(pair, ctx.get('history', []), ctx, None)
        task_id = self.llm_worker.query_structured(f"ANALYSIS {pair}", prompt)
        
        if task_id:
            self.active_analysis[pair_lower] = task_id
            self.last_trade_verdict_request[pair_lower] = datetime.now()
            return "ANALYZING", 0, "Analysis queued..."
            
        return "HOLD", 0, "Queue Failed"

    # ── Daily Profit Target Engine ────────────────────────────────────────────

    def calculate_daily_profit_target(self, balance: float = 0.0) -> float:
        """
        Kalkulasi target profit harian berbasis AI:
        - Saldo tersedia (balance)
        - Market regime real-time
        - Win-rate dan P&L 24 jam terakhir
        - Waktu sesi (Asia/Europe/US)

        Formula:
          base_target = balance * BASE_RATE (1%)
          x regime_multiplier  (BULLISH=1.5, CHOPPY=0.7, BEARISH=0.5, RANGING=0.8)
          x wr_multiplier      (WR>=60%=1.2, WR<40%=0.7, else=1.0)
          x session_multiplier (Asia peak=1.2, US peak=1.1, off-hours=0.8)

        Minimum: Rp 50.000 (agar selalu ada target bermakna)
        Maximum: 5% dari balance

        Returns:
            float — target profit harian dalam IDR
        """
        try:
            regime = self.get_market_regime()
            stats = db.get_daily_stats(dry_run=1 if config.DRY_RUN else 0)

            total_trades = stats.get('total_trades', 0)
            win_count = stats.get('win_count', 0)
            win_rate = (win_count / total_trades) if total_trades > 0 else 0.5

            # Gunakan balance Rp 100 juta sebagai fallback aman jika tidak ada data
            if balance <= 0:
                try:
                    balance_str = db.get_state("cached_idr_balance")
                    balance = float(balance_str) if balance_str else 0.0
                except Exception:
                    balance = 0.0

            # Gunakan INITIAL_CAPITAL sebagai fallback aman jika saldo belum tersedia di DB
            effective_balance = balance if balance > 0 else INITIAL_CAPITAL

            # 1. Base rate: 1% dari saldo
            base_target = effective_balance * 0.01

            # 2. Regime multiplier
            regime_mult = {
                MarketRegime.BULLISH: 1.5,
                MarketRegime.CHOPPY:  0.7,
                MarketRegime.BEARISH: 0.5,
                MarketRegime.CRASH:   0.2,
                MarketRegime.RANGING: 0.8,
            }.get(regime, 1.0)

            # 3. Win-rate multiplier
            if win_rate >= 0.60:
                wr_mult = 1.2
            elif win_rate < 0.40:
                wr_mult = 0.7
            else:
                wr_mult = 1.0

            # 4. Session multiplier (Indodax peak liquidity windows)
            hour = datetime.now().hour
            if 0 <= hour < 8:       # Asia Session (Indodax peak, termasuk jam 0)
                session_mult = 1.2
            elif 16 <= hour < 24:   # US Session (high global volatility, termasuk jam 23)
                session_mult = 1.1
            else:                   # Europe Session (jam 8-15)
                session_mult = 1.0

            target = base_target * regime_mult * wr_mult * session_mult

            # Clamp: minimum Rp 50.000, maksimum 5% dari saldo
            target = max(50_000.0, min(effective_balance * 0.05, target))

            logger.info(
                f"[Oracle] Daily Target Calc: balance=Rp{effective_balance:,.0f}, "
                f"regime={regime}(x{regime_mult}), WR={win_rate*100:.0f}%(x{wr_mult}), "
                f"session=h{hour}(x{session_mult}) → Target=Rp{target:,.0f}"
            )
            return target

        except Exception as e:
            logger.error(f"[Oracle] calculate_daily_profit_target error: {e}")
            return 100_000.0  # safe fallback

    def refresh_daily_profit_target(self, balance: float = 0.0) -> float:
        """
        Hitung ulang dan simpan target profit harian ke DB.
        Dipanggil setiap ~30 menit oleh market_cycle.

        Returns:
            float — target yang baru tersimpan
        """
        try:
            target = self.calculate_daily_profit_target(balance=balance)
            db.save_state("daily_profit_target", str(target))
            db.save_state("daily_profit_target_ts", datetime.now().isoformat())
            log_message(f"🎯 [TARGET] Profit harian diperbarui: Rp {target:,.0f}")
            return target
        except Exception as e:
            logger.error(f"[Oracle] refresh_daily_profit_target error: {e}")
            return 0.0

    def get_daily_target_status(self) -> dict:
        """
        Kembalikan status progress terhadap target profit harian.

        Returns dict:
            target_idr      — target yang dihitung AI
            current_pnl     — Net P&L hari ini (Realized + Unrealized)
            realized_pnl    — Realized P&L saja (dari trade_history)
            unrealized_pnl  — Unrealized P&L dari open positions
            peak_pnl_today  — Net PnL tertinggi yang dicapai hari ini
            progress_pct    — 0-100+% seberapa dekat ke target
            reached         — True jika target sudah tercapai
            near            — True jika sudah >= 80% dari target
            loss_limit_hit  — True jika daily loss limit sudah terlampaui
        """
        try:
            target_raw = db.get_state("daily_profit_target")
            target_idr = float(target_raw) if target_raw else 0.0

            stats = db.get_daily_stats(dry_run=1 if config.DRY_RUN else 0)
            realized_pnl = float(stats.get('total_pnl_idr', 0))

            # ── Unrealized PnL from open positions ───────────────────────────
            # Compute unrealized PnL using current market prices so HARVEST_ONLY
            # is triggered only when Net PnL (realized + unrealized) >= target.
            from config import FEE_BUY_TAKER, FEE_SELL_TAKER
            unrealized_pnl = 0.0
            try:
                tickers = self.market_ws.get_tickers() or {}
                open_positions = db.get_open_positions()
                for pair, pos_data in open_positions.items():
                    ticker_data = tickers.get(pair, {})
                    raw_price = ticker_data.get('last') or ticker_data.get('buy')
                    if not raw_price:
                        continue
                    current_price = float(raw_price)
                    entry_price = float(pos_data.get('entry_price', 0))
                    amount = float(pos_data.get('amount_crypto', 0))
                    amount_idr = float(pos_data.get('amount_idr', 0))
                    if entry_price <= 0 or amount <= 0:
                        continue
                    gross_pnl = (current_price - entry_price) * amount
                    fee_buy = amount_idr * FEE_BUY_TAKER
                    fee_sell = current_price * amount * FEE_SELL_TAKER
                    unrealized_pnl += gross_pnl - (fee_buy + fee_sell)
            except Exception as _ue:
                logger.warning(f"[Oracle] Unrealized PnL calc error: {_ue}")

            # Net PnL = Realized + Unrealized
            current_pnl = realized_pnl + unrealized_pnl

            # ── Peak PnL Tracking ────────────────────────────────────────────
            # Track peak using Net PnL so drawdown logic also uses the true net value.
            peak_raw = db.get_state("peak_pnl_today")
            peak_pnl_today = float(peak_raw) if peak_raw else 0.0
            if current_pnl > peak_pnl_today:
                peak_pnl_today = current_pnl
                db.save_state("peak_pnl_today", str(peak_pnl_today))

            progress_pct = (current_pnl / target_idr * 100) if target_idr > 0 else 0.0

            # Hitung loss limit dalam IDR berdasarkan saldo efektif
            try:
                balance_str = db.get_state("cached_idr_balance")
                effective_balance = float(balance_str) if balance_str else INITIAL_CAPITAL
            except Exception:
                effective_balance = INITIAL_CAPITAL
            daily_loss_limit_idr = -(effective_balance * DAILY_LOSS_LIMIT_PCT)

            # Compute time remaining in the current trading day (0–100%).
            # 0 = midnight (start of day, 100% remains); 86400 = end of day (0% remains).
            now = datetime.now()
            seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second
            time_remaining_pct = max(0.0, round((1 - seconds_since_midnight / 86400) * 100, 1))

            # Read persisted session mode (written by evaluate_and_update_session_mode)
            session_mode = db.get_state("session_mode") or "NORMAL"
            # Explicit safe check: treat None/unset as True (buy enabled by default)
            session_buy_enabled = (db.get_state("session_buy_enabled") or "true") == "true"

            return {
                "target_idr": target_idr,
                "current_pnl": current_pnl,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "peak_pnl_today": peak_pnl_today,
                "progress_pct": round(progress_pct, 1),
                "reached": current_pnl >= target_idr and target_idr > 0,
                "near": progress_pct >= 80.0 and target_idr > 0,
                "loss_limit_hit": current_pnl <= daily_loss_limit_idr,
                "time_remaining_pct": time_remaining_pct,
                "session_mode": session_mode,
                "session_buy_enabled": session_buy_enabled,
            }
        except Exception as e:
            logger.error(f"[Oracle] get_daily_target_status error: {e}")
            return {
                "target_idr": 0.0, "current_pnl": 0.0,
                "realized_pnl": 0.0, "unrealized_pnl": 0.0,
                "peak_pnl_today": 0.0,
                "progress_pct": 0.0, "reached": False, "near": False,
                "loss_limit_hit": False, "time_remaining_pct": 50.0,
                "session_mode": "NORMAL", "session_buy_enabled": True,
            }

    def evaluate_and_update_session_mode(self, target_status: dict) -> dict:
        """
        Smart Trailing & STOP BUY Protocol.

        Evaluates whether the daily NET target has been reached and decides the
        next session mode:

          NORMAL       — target not yet reached; normal operation.
          TRAILING     — target reached but conditions allow extending the goal.
          HARVEST_ONLY — buy blocked; only existing positions are monitored.

        Net PnL = Realized PnL + Unrealized PnL (from get_daily_target_status).
        Trigger requires Net_PnL >= Target_PnL (not realized alone).

        Smart Trailing conditions (all must hold):
          • time_remaining_pct > 40  (enough of the day left)
          • market regime == BULLISH
          • market sentiment index >= 80  (strong bullish momentum)

        Once HARVEST_ONLY is persisted to DB it stays until either:
          • current_pnl drops back below target_idr (de-trigger: BUY re-enabled), or
          • a new UTC trading day resets it (see _utc_reset_monitor_loop).

        Returns dict:
            buy_enabled  — bool: whether new Entry Ready orders are allowed
            session_mode — str: NORMAL | TRAILING | HARVEST_ONLY
            message      — str: human-readable status for logging
        """
        _TRAILING_EXTRA_PCT   = 0.15   # Extend target by +15% of current PnL
        _TIME_THRESHOLD_PCT   = 40.0   # Stop trailing when < 40% of day remains
        _SENTIMENT_THRESHOLD  = 80     # sentiment >= 80 = "strong bullish" (~at 100)

        try:
            target_idr      = target_status.get("target_idr", 0)
            current_pnl     = target_status.get("current_pnl", 0)   # Net PnL
            realized_pnl    = target_status.get("realized_pnl", current_pnl)
            unrealized_pnl  = target_status.get("unrealized_pnl", 0.0)
            peak_pnl_today  = target_status.get("peak_pnl_today", 0)
            time_remaining  = target_status.get("time_remaining_pct", 50.0)
            session_mode    = target_status.get("session_mode", "NORMAL")

            # Already HARVEST_ONLY: re-evaluate the locking condition.
            if session_mode == "HARVEST_ONLY":
                # De-trigger: if Net PnL has dropped back below target (e.g. via manual exit/loss),
                # the original HARVEST_ONLY condition is no longer met → revert to NORMAL.
                if target_idr > 0 and current_pnl < target_idr:
                    db.save_state("session_mode", "NORMAL")
                    db.save_state("session_buy_enabled", "true")
                    msg = (
                        f"🔓 [SESSION RESET] HARVEST_ONLY released. "
                        f"Net PnL (Rp {current_pnl:,.0f}) dropped below Target (Rp {target_idr:,.0f}). "
                        f"Peak was Rp {peak_pnl_today:,.0f}. BUY re-enabled."
                    )
                    log_message(msg)
                    return {
                        "buy_enabled": True,
                        "session_mode": "NORMAL",
                        "message": msg,
                    }
                # Net PnL still at/above target — stay locked
                msg = (
                    f"Net PnL: Rp {current_pnl:,.0f} "
                    f"(Realized: Rp {realized_pnl:,.0f}, Unrealized: Rp {unrealized_pnl:,.0f}) | "
                    f"Target: Rp {target_idr:,.0f} | Status: HARVEST_ONLY"
                )
                return {
                    "buy_enabled": False,
                    "session_mode": "HARVEST_ONLY",
                    "message": msg,
                }

            # Target not yet reached — normal or previous trailing mode continues
            if target_idr <= 0 or current_pnl < target_idr:
                return {
                    "buy_enabled": True,
                    "session_mode": session_mode,
                    "message": "",
                }

            # ── Target reached: decide TRAILING or HARVEST_ONLY ──────────────
            if time_remaining > _TIME_THRESHOLD_PCT:
                regime = self.get_market_regime()
                try:
                    sentiment = int(float(db.get_state("market_sentiment_index") or "50"))
                except (ValueError, TypeError):
                    sentiment = 50

                if regime == MarketRegime.BULLISH and sentiment >= _SENTIMENT_THRESHOLD:
                    # Set new target to 115% of current PnL (current_pnl × 1.15).
                    # Equivalent to: new_target = current_pnl + 15% of current_pnl.
                    new_target = current_pnl * (1 + _TRAILING_EXTRA_PCT)
                    db.save_state("daily_profit_target", str(new_target))
                    db.save_state("daily_profit_target_ts", datetime.now().isoformat())
                    db.save_state("session_mode", "TRAILING")
                    db.save_state("session_buy_enabled", "true")
                    msg = (
                        f"📈 [SMART TRAILING] Target extended: "
                        f"Rp {target_idr:,.0f} → Rp {new_target:,.0f} "
                        f"(+{_TRAILING_EXTRA_PCT*100:.0f}% of current PnL). "
                        f"Regime={regime}, Sentiment={sentiment}/100, "
                        f"Time Left={time_remaining:.0f}%. SESSION_BUY_ENABLED=True."
                    )
                    log_message(msg)
                    return {
                        "buy_enabled": True,
                        "session_mode": "TRAILING",
                        "message": msg,
                    }
                else:
                    # Conditions degraded while target was exceeded — stop buying
                    log_message(
                        f"⛔ [STOP BUY] Regime={regime}, Sentiment={sentiment}/100 — "
                        f"no longer bullish. Triggering HARVEST_ONLY."
                    )

            # ── Trigger STOP BUY / HARVEST_ONLY ─────────────────────────────
            db.save_state("session_mode", "HARVEST_ONLY")
            db.save_state("session_buy_enabled", "false")
            msg = (
                f"Net PnL: Rp {current_pnl:,.0f} "
                f"(Realized: Rp {realized_pnl:,.0f}, Unrealized: Rp {unrealized_pnl:,.0f}) | "
                f"Target: Rp {target_idr:,.0f} | Status: HARVEST_ONLY"
            )
            log_message(f"🛑 [HARVEST ONLY] {msg}")
            send_telegram_message(
                f"🛑 *SESSION MODE: HARVEST ONLY*\n"
                f"Target Rp {target_idr:,.0f} tercapai.\n"
                f"Net PnL: Rp {current_pnl:,.0f} "
                f"(Realized: Rp {realized_pnl:,.0f} | Unrealized: Rp {unrealized_pnl:,.0f})\n"
                f"Sisa Hari: {time_remaining:.0f}%\n"
                f"🔒 Tidak ada pembelian baru. "
                f"Memantau & memperketat TP posisi terbuka."
            )

            # ── Strategic Break: compile session analytics and notify dashboard ──
            self._emit_strategic_break(
                target_idr=target_idr,
                current_pnl=current_pnl,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                peak_pnl_today=peak_pnl_today,
            )

            return {
                "buy_enabled": False,
                "session_mode": "HARVEST_ONLY",
                "message": msg,
            }

        except Exception as e:
            logger.error(f"[Oracle] evaluate_and_update_session_mode error: {e}")
            return {"buy_enabled": True, "session_mode": "NORMAL", "message": ""}

    def _emit_strategic_break(self, target_idr, current_pnl, realized_pnl, unrealized_pnl, peak_pnl_today):
        """
        Compile session analytics and persist a strategic_break_payload to DB.
        The dashboard push_data_loop picks this up and forwards it as a
        'strategic_break' Socket.IO event to the frontend.
        """
        try:
            from config import ROUND_TRIP_FEE_PCT
            stats = db.get_daily_stats(dry_run=1 if config.DRY_RUN else 0)
            total_trades    = stats.get('total_trades', 0)
            win_count       = stats.get('win_count', 0)
            gross_profit    = float(stats.get('gross_profit', 0))
            gross_loss      = float(stats.get('gross_loss', 0))      # negative value
            total_volume    = float(stats.get('total_volume_idr', 0))

            # Win Rate
            win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

            # Profit Factor = Gross Profit / |Gross Loss|; ∞ when no losses recorded
            if gross_loss < 0:
                profit_factor = gross_profit / abs(gross_loss)
            elif gross_profit > 0:
                profit_factor = float('inf')
            else:
                profit_factor = 0.0

            # Max Session Drawdown = Peak Equity - Current Net PnL
            max_drawdown = max(0.0, peak_pnl_today - current_pnl)

            # Estimated total fees
            estimated_fees = total_volume * ROUND_TRIP_FEE_PCT

            payload = {
                "target_idr": target_idr,
                "net_pnl": current_pnl,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "peak_pnl_today": peak_pnl_today,
                "win_rate": round(win_rate, 2),
                "win_count": win_count,
                "total_trades": total_trades,
                "profit_factor": profit_factor if profit_factor != float('inf') else 9999.0,  # cap ∞ for JSON serialisation
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "max_drawdown": max_drawdown,
                "estimated_fees": round(estimated_fees, 2),
                "total_volume_idr": total_volume,
                "triggered_at": datetime.now().isoformat(),
                "status": "PENDING",  # PENDING → CONTINUE or STOP after user action
            }
            db.save_state("strategic_break_payload", json.dumps(payload))
            logger.info(
                f"[Oracle] Strategic Break emitted — Net PnL: Rp {current_pnl:,.0f}, "
                f"Win Rate: {win_rate:.1f}%, Profit Factor: {profit_factor:.4f}"
            )
        except Exception as e:
            logger.error(f"[Oracle] _emit_strategic_break error: {e}")

    # ── Consciousness & Self-Learning ─────────────────────────────────────────

    def update_consciousness(self, regime: str = None, recent_stats: dict = None):
        """
        Bangun dan kirim market context terbaru ke LLM sebagai 'kesadaran'.
        Dipanggil periodik (setiap ~30 menit) atau saat regime shift.
        """
        if (datetime.now() - self.last_consciousness_update).total_seconds() < 1800:
            return  # Cooldown 30 menit
        self.last_consciousness_update = datetime.now()

        try:
            regime = regime or db.get_state("market_regime") or "UNKNOWN"
            sentiment = db.get_state("market_sentiment_index") or "N/A"
            regime_leader = db.get_state("regime_leader") or "UNKNOWN"

            if not recent_stats:
                recent_stats = db.get_daily_stats(dry_run=1 if config.DRY_RUN else 0)

            win_rate = 0.0
            if recent_stats.get("total_trades", 0) > 0:
                win_rate = recent_stats["win_count"] / recent_stats["total_trades"] * 100

            # Gabungkan lessons terakhir
            lessons_text = ""
            if self._lessons:
                lessons_text = "\nPelajaran dari trade sebelumnya:\n"
                for i, lesson in enumerate(self._lessons[-5:], 1):  # Max 5 lesson terbaru
                    lessons_text += f"  {i}. {lesson}\n"

            weights_text = ", ".join(f"{k}={v:.2f}" for k, v in self.weights.items())

            context = (
                f"=== MARKET CONSCIOUSNESS UPDATE [{datetime.now().strftime('%H:%M')}] ===\n"
                f"Regime: {regime} | Sentiment Index: {sentiment}/100 | Leader: {regime_leader}\n"
                f"Performance 24h: {recent_stats.get('total_trades',0)} trades, "
                f"Win Rate: {win_rate:.1f}%, "
                f"Total PnL: Rp {recent_stats.get('total_pnl_idr',0):,.0f}\n"
                f"Active Weights: {weights_text}"
                f"{lessons_text}"
                f"=== END UPDATE ==="
            )

            self.llm_worker.update_consciousness(context)
            logger.info(f"[Oracle] Consciousness updated: regime={regime}, wr={win_rate:.1f}%")
        except Exception as e:
            logger.error(f"[Oracle] Consciousness update failed: {e}")

    def _accumulate_lesson(self, lesson: str):
        """
        Tambahkan lesson baru ke rolling list.
        Lessons ini di-inject ke LLM consciousness setiap update.
        """
        if lesson and len(lesson) > 10:
            self._lessons.append(f"[{datetime.now().strftime('%H:%M')}] {lesson}")
            if len(self._lessons) > 20:  # Jaga max 20 lesson
                self._lessons.pop(0)
            db.save_state("llm_lessons", json.dumps(self._lessons))

    def _load_lessons_from_db(self):
        """Restore lessons dari DB saat startup (survive restart)."""
        try:
            raw = db.get_state("llm_lessons")
            if raw:
                self._lessons = json.loads(raw)
        except Exception:
            pass

    def get_sentiment_boost(self, pair: str) -> float:
        """Get the active AI boost for a pair, if not expired."""
        data = self.pair_boosts.get(pair.lower())
        if data and datetime.now() < data["expiry"]:
            return data["boost"]
        return 0.0
        
    def is_healthy(self) -> bool:
        """
        Check if the bot is in a state where it can trade safely.
        Checks WebSocket latency and connection status.
        """
        # 1. Check WebSocket Connection
        if not self.market_ws.is_connected():
            logger.warning("[Oracle] Market WS disconnected. Bot is BLIND.")
            return False
            
        # 2. Check Latency
        latency = self.market_ws.get_latency()
        if latency > 2.0: # Lebih dari 2 detik delay
            logger.warning(f"[Oracle] High Latency Detected: {latency:.2f}s. Entering Safe Mode.")
            return False
            
        return True

    def get_market_regime(self) -> str:
        """
        Analyze global market sentiment across all tracked pairs.
        """
        # Update regime every 1 minute
        if datetime.now() - self.last_regime_update < timedelta(minutes=1):
            return self.last_regime
            
        tickers = self.market_ws.get_tickers()
        if not tickers:
            return MarketRegime.CHOPPY
            
        # Analyze top 50 pairs by volume
        all_pairs = list(tickers.values())
        all_pairs.sort(key=lambda x: x.get('vol_idr', 0), reverse=True)
        top_50 = all_pairs[:50]
        
        # Calculate global price change
        up_count = 0
        down_count = 0
        total_change = 0
        panic_count = 0 # Short-term memory (velocity proxy)
        
        for t in top_50:
            change = t.get('change_24h', 0)
            total_change += change
            if change > 1.0: up_count += 1
            if change < -1.0: down_count += 1
            
            # Deteksi jika harga saat ini sedang nyungsep (berada di dasar kisaran 24 jam)
            last = float(t.get('last', 0))
            high = float(t.get('high', 1))
            low = float(t.get('low', 0))
            if high > low:
                pos_in_range = (last - low) / (high - low)
        
        # Calculate global price change
        avg_change = sum(p.get('change_24h', 0) for p in top_50) / len(top_50)
        
        # Calculate Sentiment Index (0 - 100)
        # -2.5% or less = 0 (Extreme Fear)
        # +2.5% or more = 100 (Extreme Greed)
        sentiment_idx = 50 + (avg_change * 20)
        sentiment_idx = max(0, min(100, sentiment_idx))
        db.save_state("market_sentiment_index", int(sentiment_idx))

        up_moves = [p for p in top_50 if p.get('change_24h', 0) > 0.5]
        down_moves = [p for p in top_50 if p.get('change_24h', 0) < -0.5]
        
        if len(up_moves) > len(top_50) * 0.6:
            regime = MarketRegime.BULLISH
        elif len(down_moves) > len(top_50) * 0.6:
            regime = MarketRegime.BEARISH
        elif abs(avg_change) < 0.2:
            regime = MarketRegime.RANGING
        else:
            regime = MarketRegime.CHOPPY
            
        if regime != self.last_regime:
            # Find the "Leader" of the movement (highest change in top 50)
            leader = max(top_50, key=lambda x: x.get('change_24h', 0)) if top_50 else None
            leader_str = f"{leader['pair'].upper()} (+{leader['change_24h']:.1f}%)" if leader else "UNKNOWN"
            
            logger.info(f"[Oracle] Market Regime Shift: {self.last_regime} -> {regime} | Leader: {leader_str}")
            db.save_state("market_regime", regime)
            db.save_state("regime_leader", leader_str)
            
            # Apply dynamic weight adjustments based on regime
            self._apply_regime_weights(regime)
            
            # [Senior Debug] Temporarily disable global regime analysis to prevent queue stall
            # self.llm_worker.query_async("REGIME_ANALYSIS", prompt)
            
        self.last_regime = regime
        self.last_regime_update = datetime.now()
        return regime

    def _apply_regime_weights(self, regime):
        """Sesuaikan bobot global bot berdasarkan fase pasar saat ini."""
        try:
            if regime == MarketRegime.BULLISH:
                db.update_weight('ai_prob', 1.2)
                db.update_weight('sector_sentiment', 1.3)
                db.update_weight('imbalance', 1.0)
                db.update_weight('whale_boost', 1.1)
            elif regime == MarketRegime.BEARISH:
                db.update_weight('ai_prob', 0.8)
                db.update_weight('sector_sentiment', 0.7)
                db.update_weight('imbalance', 1.2) # Lebih condong ke orderbook di pasar merah
                db.update_weight('whale_boost', 0.9)
            elif regime == MarketRegime.RANGING:
                db.update_weight('ai_prob', 1.0)
                db.update_weight('sector_sentiment', 1.0)
                db.update_weight('imbalance', 1.1)
                db.update_weight('whale_boost', 1.0)
            else: # CHOPPY
                db.update_weight('ai_prob', 0.9)
                db.update_weight('sector_sentiment', 0.9)
                db.update_weight('imbalance', 1.0)
                db.update_weight('whale_boost', 1.0)
            logger.info(f"[Oracle] Adaptive weights applied for {regime} regime.")
        except Exception as e:
            logger.error(f"[Oracle] Failed to apply weights: {e}")

    def get_adaptive_config(self) -> dict:
        """
        Return configuration overrides based on the current market regime.
        """
        regime = self.get_market_regime()
        from config import TECH_CONFIDENCE_THRESHOLD, MIN_VOLUME_FILTER, MAX_ALLOWED_SPREAD_PCT, MIN_PRICE_FILTER
        
        overrides = {
            "tech_threshold": TECH_CONFIDENCE_THRESHOLD,
            "min_vol": MIN_VOLUME_FILTER,
            "max_spread": MAX_ALLOWED_SPREAD_PCT,
            "min_price": MIN_PRICE_FILTER
        }
        
        if regime == MarketRegime.BULLISH:
            overrides["tech_threshold"] -= 0.05 # Loloskan lebih banyak di pasar Bullish
        elif regime == MarketRegime.BEARISH:
            overrides["tech_threshold"] += 0.10 # Lebih ketat di pasar Bearish
            overrides["min_vol"] *= 2.0       # Hanya ambil koin volume besar
        elif regime == MarketRegime.CRASH:
            overrides["tech_threshold"] = 0.95  # Hampir tidak beli apapun
            overrides["max_spread"] /= 2.0    # Spread harus sangat tipis
            
        # Self-Tuning based on recent performance
        performance_penalty = self.learn_from_history()
        overrides["tech_threshold"] += performance_penalty

        # --- [Advanced] Equity-Based Adaptation (Senior Strategy) ---
        try:
            stats = db.get_daily_stats(dry_run=1 if config.DRY_RUN else 0) # Ambil statistik 24 jam terakhir
            daily_pnl = stats.get('total_pnl_idr', 0)
            
            # 1. Profit-Pause (Proteksi Cuan)
            # Jika sudah profit > 5% dari modal harian (misal > 5jt), naikkan threshold sangat tinggi
            if daily_pnl > 5_000_000:
                logger.info(f"[Oracle] Equity-Shield: Profit Harian Tinggi (Rp {daily_pnl:,.0f}). Mengetatkan entry.")
                overrides["tech_threshold"] += 0.20 # Sangat selektif
            
            # 2. Recovery-Mode (Balikkan Keadaan)
            # Jika sedang merugi (red), turunkan threshold sedikit agar lebih agresif mencari peluang rebound
            elif daily_pnl < -2_000_000:
                logger.info(f"[Oracle] Recovery-Mode: Drawdown terdeteksi (Rp {daily_pnl:,.0f}). Melonggarkan entry.")
                overrides["tech_threshold"] -= 0.05 # Lebih agresif
        except Exception as e:
            logger.error(f"[Oracle] Equity adaptation error: {e}")
        
        return overrides

    def get_entry_policy(self, regime: str, base_dynamic_threshold: float) -> dict:
        """
        Policy adaptif untuk gate entry berbasis regime + performa terbaru.
        Mengembalikan threshold yang dipakai di main loop agar keputusan lebih presisi.
        """
        dyn = float(base_dynamic_threshold)

        # Baseline gate untuk keputusan LLM
        llm_buy_min_conf = 60.0
        llm_hold_block_conf = 70.0
        llm_avoid_block_conf = 60.0

        # Regime-aware adjustments
        if regime == MarketRegime.BULLISH:
            dyn -= 2.0
            llm_buy_min_conf = 58.0
            llm_hold_block_conf = 74.0
        elif regime == MarketRegime.BEARISH:
            dyn += 6.0
            llm_buy_min_conf = 68.0
            llm_hold_block_conf = 62.0
            llm_avoid_block_conf = 55.0
        elif regime == MarketRegime.CHOPPY:
            dyn += 4.0
            llm_buy_min_conf = 65.0
            llm_hold_block_conf = 75.0  # [Loosened] was 66.0
            llm_avoid_block_conf = 65.0 # [Loosened] was 58.0
        elif regime == MarketRegime.RANGING:
            dyn += 1.0
            llm_buy_min_conf = 62.0

        # Self-tuning dari 20 trade terakhir
        try:
            recent = db.get_recent_trades(limit=20)
            if recent:
                wins = sum(1 for t in recent if float(t.get('pnl_idr') or 0) > 0)
                wr = wins / len(recent)
                if wr < 0.50:
                    dyn += 3.0
                    llm_buy_min_conf += 3.0
                elif wr >= 0.65:
                    dyn -= 1.5
                    llm_buy_min_conf -= 1.0
        except Exception:
            pass

        return {
            "dynamic_threshold": max(50.0, min(85.0, dyn)),
            "llm_buy_min_conf": max(50.0, min(85.0, llm_buy_min_conf)),
            "llm_hold_block_conf": max(55.0, min(90.0, llm_hold_block_conf)),
            "llm_avoid_block_conf": max(50.0, min(90.0, llm_avoid_block_conf)),
        }

    def learn_from_history(self) -> float:
        """
        Analyze recent trades to see if the current strategy is losing money.
        Returns a penalty to be added to AI_THRESHOLD.
        """
        try:
            recent_trades = db.get_recent_trades(limit=10)
            if not recent_trades: return 0.0
            
            wins = sum(1 for t in recent_trades if t['pnl_idr'] > 0)
            win_rate = wins / len(recent_trades)
            
            if win_rate < 0.4: # Win rate buruk
                logger.info(f"[Oracle] Self-Tuning: Recent Win Rate is low ({win_rate*100:.0f}%). Tightening filters.")
                return 0.10
            elif win_rate < 0.6:
                return 0.05
                
            return 0.0
        except Exception as e:
            logger.error(f"[Oracle] Learning Error: {e}")
            return 0.0

    def get_sizing_multiplier(self, pair: str, velocity_score: float = 0.5, confidence: float = 0.5) -> float:
        """
        [PROFIT VELOCITY SIZING]
        Menentukan besarnya modal berdasarkan 'kecepatan' koin mencapai target.
        Multiplier = Base (Regime) * Performance (Velocity + Confidence)
        """
        # 1. Base Multiplier dari Market Regime
        regime = self.get_market_regime()
        if regime == MarketRegime.CRASH: return 0.0
        
        base_m = 1.0
        if regime == MarketRegime.BEARISH: base_m = 0.6
        elif regime == MarketRegime.BULLISH: base_m = 1.3
        elif regime == MarketRegime.CHOPPY: base_m = 0.9
        
        # 2. Velocity & Confidence Factor (0.0 - 1.0)
        # velocity_score tinggi = tren sangat curam / momentum kuat
        # confidence tinggi = probabilitas AI tinggi
        perf_score = (velocity_score * 0.65) + (confidence * 0.35)
        
        # 3. Final Multiplier Calculation
        # Kita ingin multiplier berada di rentang 0.2x hingga 2.5x dari base porsi modal
        # Jika perf_score rendah (0.1), multiplier akan kecil (~0.2x) -> Modal receh untuk koin lemot
        # Jika perf_score tinggi (0.9), multiplier bisa mencapai 2.0x -> Modal gajah untuk koin fast-track
        
        final_multiplier = base_m * (perf_score * 2.2)
        
        # Batasi agar tetap rasional (Safety Guard)
        final_multiplier = max(0.15, min(2.5, final_multiplier))
        
        # logging.info(f"🧠 Sizing [{pair}]: Regime={regime}, Vel={velocity_score:.2f}, Conf={confidence:.2f} -> Mult={final_multiplier:.2f}x")
        logging.info(f"🧠 Sizing [{pair}]: Regime={regime}, Vel={velocity_score:.2f}, Conf={confidence:.2f} -> Mult={final_multiplier:.2f}x")
        return final_multiplier

    def get_rationale(self, pair: str, decision: str) -> str:
        """
        Generate semantic explanation for a decision.
        """
        regime = self.get_market_regime()
        if decision == "SKIP_LATENCY":
            return "Saya memutuskan berhenti sejenak karena mata saya 'rabun' (latency tinggi). Terlalu berisiko."
        if decision == "SKIP_REGIME":
            return f"Saya tidak mau beli koin apapun sekarang karena pasar global sedang {regime}. Saya menjaga modal kita."
        if decision == "ADAPTIVE_SL":
            return f"Pasar sedang {regime}, saya akan sedikit lebih 'sabar' menahan guncangan harga."
        if decision == "WHALE_BOOST":
            return "Ada paus besar sedang menahan harga di bawah kita. Kesempatan emas!"
        if decision == "SECTOR_BOOST":
            return "Sektor koin ini sedang 'hot'. Momentum sedang berpihak pada kita."
        
        return "Keputusan berdasarkan analisa teknikal dan probabilitas AI yang kuat."

    def get_whale_boost(self, pair: str) -> float:
        """
        Scan Order Book for massive buy walls.
        Returns a score multiplier boost (e.g., 1.5) if a whale is found.
        """
        try:
            from config import WHALE_THRESHOLD_IDR
            # Ambil OB data (MarketWS cache)
            ob = self.market_ws.get_order_book(pair)
            if not ob or not ob.get('bids'): return 1.0
            
            # Cari tembok beli raksasa di 10 level pertama
            # format bid: [price, amount]
            for bid in ob['bids'][:10]:
                val_idr = float(bid[0]) * float(bid[1])
                if val_idr >= WHALE_THRESHOLD_IDR:
                    db.save_state("latest_whale", f"{pair}|{int(val_idr)}")
                    return 1.5 # Boost skor 50%
                    
            return 1.0
        except Exception:
            return 1.0

    def detect_fake_pump(self, pair: str, price_change_pct: float) -> bool:
        """
        Detect if a price surge is artificial (low liquidity support).
        """
        try:
            if price_change_pct < 3.0: return False
            
            ob = self.market_ws.get_order_book(pair)
            if not ob or not ob.get('bids'): return True # No bids = definitely fake
            
            # Check Buy Wall support
            buy_wall_idr = sum(float(b[0]) * float(b[1]) for b in ob['bids'][:5])
            from config import WHALE_THRESHOLD_IDR
            
            if buy_wall_idr < (WHALE_THRESHOLD_IDR / 5): # Very thin buy support
                db.save_state("latest_fake_pump", f"{pair}|{price_change_pct:.1f}")
                
                # Gunakan helper baru untuk konteks likuiditas yang lebih dalam
                rich_context = self._prepare_rich_prompt(pair, task_type='fakepump')
                prompt = (
                    f"CRYPTO LIQUIDITY WARNING: The coin {pair.upper()} just jumped {price_change_pct:.1f}% in price, "
                    f"but the 'buy wall' (bids) is extremely thin ({buy_wall_idr:,.0f} IDR). "
                    "Is this a 'fake pump' designed to trap buyers? Analyze the data below and give a 2-sentence verdict.\n\n"
                    f"{rich_context}"
                )
                self.llm_worker.query_async("FAKE_PUMP_ANALYSIS", prompt)
                
                return True
                
            return False
        except Exception:
            return False

    def request_periodic_analysis(self, ranked_pairs):
        """
        Analyze the top mover every few minutes to keep the dashboard active.
        """
        # Cooldown 5 menit agar tidak membebani CPU
        if (datetime.now() - self.last_top_mover_analysis).total_seconds() < 300:
            return

        target_pair = None
        change = 0
        vol = 0

        if ranked_pairs:
            top_coin = ranked_pairs[0]
            target_pair = top_coin.get('pair', 'UNKNOWN')
            change = top_coin.get('change', 0)
            vol = top_coin.get('volatility', 0)
        else:
            # Fallback jika ranked_pairs kosong: cari koin dengan % change tertinggi dari raw tickers
            tickers = self.market_ws.get_tickers()
            if not tickers: return
            
            # Cari koin dengan kenaikan tertinggi (exclude stablecoins jika perlu)
            potential_pairs = [t for t in tickers.values() if 'change_24h' in t]
            if not potential_pairs: return
            
            top_ticker = max(potential_pairs, key=lambda x: abs(x.get('change_24h', 0)))
            target_pair = top_ticker.get('pair', 'UNKNOWN')
            change = top_ticker.get('change_24h', 0)
            vol = 0 # Volatility info not in raw ticker
            
        # Gunakan helper baru untuk membangun prompt kaya
        rich_context = self._prepare_rich_prompt(target_pair, task_type='topmover')
        prompt = (
            f"As a Senior Quantitative Crypto Analyst, analyze the price action of {target_pair.upper()} "
            f"which has moved {change:.2f}% in the last 24 hours. "
            "Give a sharp, professional market insight (risk & opportunity) in max 2 short sentences using the data below.\n\n"
            f"{rich_context}"
        )
        self.llm_worker.query_async(f"TOP_MOVER:{target_pair}", prompt)
        self.last_top_mover_analysis = datetime.now()

    def request_pump_analysis(self, pair, change):
        """
        Triggered when a coin is 'exploding' or crashing rapidly.
        """
        last_time = self.pump_cooldowns.get(pair)
        if last_time and (datetime.now() - last_time).total_seconds() < 900: # 15 min cooldown
            return
            
        status = "PUMPING" if change > 0 else "CRASHING"
        
        # Gunakan helper baru untuk memberikan data Order Book asli ke AI
        rich_context = self._prepare_rich_prompt(pair, task_type='pumpalert')
        prompt = (
            f"URGENT: The coin {pair.upper()} is {status} with a {change:.2f}% move! "
            "Analyze the orderbook depth and price action below. Is this a trap or a real breakout? "
            "Give a 1-sentence warning or recommendation for scalpers.\n\n"
            f"{rich_context}"
        )
        self.llm_worker.query_async(f"PUMP_ALERT:{pair}", prompt)
        self.pump_cooldowns[pair] = datetime.now()

    def get_sector_boost(self, pair: str) -> float:
        """
        Check if the pair's sector is pumping.
        """
        try:
            sentiment = self.get_sector_sentiment_val(pair)
            if sentiment > 2.0: # Sektor naik > 2% rata-rata
                return 1.3 # Boost skor 30%
                
            return 1.0
        except Exception:
            return 1.0

    def get_sector_sentiment_val(self, pair: str) -> float:
        """
        Returns the raw sentiment score for a pair's sector.
        """
        try:
            from config import SECTOR_MAP
            sector = SECTOR_MAP.get(pair.lower())
            if not sector: return 0.0
            return db.get_latest_sector_sentiment(sector)
        except Exception:
            return 0.0

    def update_sector_sentiment(self):
        """
        Calculate and persist global sector sentiment.
        Includes 24h average, 1h momentum, and sector leader identification.
        """
        try:
            from config import SECTOR_MAP
            
            # Ambil data performa terbaru dari database (Mempunyai ch_1h)
            with db.get_connection() as conn:
                rows = conn.execute("SELECT pair, ch_1h, score FROM ticker_history").fetchall()
            
            # Ambil tickers live untuk change_24h
            ws_tickers = self.market_ws.get_tickers()
            if not ws_tickers: return
            
            # {SECTOR: {"changes_24h": [], "changes_1h": [], "leader": None, "leader_ch": -999}}
            sector_data = {}
            for row in rows:
                pair = row['pair'].lower()
                s = SECTOR_MAP.get(pair)
                if s:
                    if s not in sector_data:
                        sector_data[s] = {"24h": [], "1h": [], "leader": "N/A", "leader_ch": -999}
                    
                    ch_24h = float(ws_tickers.get(pair, {}).get('change_24h', 0))
                    ch_1h  = float(row['ch_1h'] or 0)
                    
                    sector_data[s]["24h"].append(ch_24h)
                    sector_data[s]["1h"].append(ch_1h)
                    
                    # Update leader based on 24h performance (motor penggerak narasi)
                    if ch_24h > sector_data[s]["leader_ch"]:
                        sector_data[s]["leader_ch"] = ch_24h
                        sector_data[s]["leader"] = pair.upper().replace('IDR', '')

            # Save to history
            for sector, d in sector_data.items():
                if not d["24h"]: continue
                avg_24h = sum(d["24h"]) / len(d["24h"])
                avg_1h  = sum(d["1h"]) / len(d["1h"])
                db.save_sector_sentiment(
                    sector, 
                    avg_24h, 
                    momentum=avg_1h, 
                    leader=d["leader"], 
                    leader_ch=d["leader_ch"]
                )
                
        except Exception as e:
            logger.error(f"[Oracle] Sector Update Error: {e}")

    # ── Self-Learning Engine (Project Evolution) ───────────────────────

    def learn_from_trade(self, pair, pnl_idr):
        """
        [Revisi] Experience Replay: Tidak ada lagi if-else trauma/weights.
        Semua transaksi hanya di-log ke otak LLM untuk Post-Mortem. Otak matematis
        (k-NN) langsung mengambil data dari database di putaran berikutnya.
        """
        try:
            dna = db.get_latest_trade_dna(pair)
            if not dna: return
            
            is_profit = pnl_idr > 0
            status = "PROFIT" if is_profit else "LOSS"
            logger.info(f"[Oracle] Experience Logged: {pair} closed with {status} ({pnl_idr}).")

            # [LiteRT-LM + Self-Learning] Request Post-Mortem Analysis
            prompt = (
                f"Trade Post-Mortem: {pair} ditutup dengan {status} Rp {pnl_idr:,.0f}. "
                f"DNA trade: ai_prob={dna.get('ai_prob',0):.2f}, whale={dna.get('whale_boost',1):.1f}, "
                f"sector={dna.get('sector_sentiment',0):.2f}, imbalance={dna.get('imbalance',1):.2f}, "
                f"vol={dna.get('volatility',0):.2f}%. "
                f"Sebagai quant analyst, apa satu pelajaran paling penting dari trade ini? "
                f"Jawab dalam 1 kalimat ringkas bahasa Indonesia."
            )
            self.llm_worker.query_async("POST_MORTEM", prompt)
            
        except Exception as e:
            logger.error(f"[Oracle] Learning Error: {e}")

    def _calculate_math_eta(self, pair, target_price, current_price, prices):
        """
        Estimasi waktu (menit) untuk mencapai target harga berdasarkan volatilitas ATR 1m.
        Formula: Jarak Harga / (Rata-rata pergerakan harga per menit).
        """
        try:
            if not prices or len(prices) < 5:
                return 15 # Default
            
            # Hitung 'Price Velocity' (V) - rata-rata absolut perubahan harga per menit
            # Gunakan window 30 menit terakhir jika ada
            window = prices[-30:] if len(prices) >= 30 else prices
            deltas = [abs(window[i] - window[i-1]) for i in range(1, len(window))]
            
            if not deltas: return 15
            
            velocity = sum(deltas) / len(deltas)
            
            if velocity <= 0:
                # Jika harga stagnan, gunakan volatilitas historis (0.05% per menit sebagai proxy)
                velocity = current_price * 0.0005
                
            distance = abs(target_price - current_price)
            eta = distance / velocity
            
            # Cap ETA antara 2 menit hingga 6 jam (360 menit)
            return max(2, min(360, int(eta)))
        except Exception as e:
            logger.warning(f"[Oracle] Math ETA Error for {pair}: {e}")
            return 15

    def find_winning_pattern(self, pair: str, current_dna: dict) -> dict:
        """
        K-Nearest Neighbors Pattern Matcher & LLM Strategy Generator.
        Mencari 5 transaksi masa lalu terdekat (Euclidean), lalu LLM menentukan target.
        Returns: { 'verdict': 'BUY'/'AVOID', 'target_tp': 0.02, 'target_sl': -0.012, 'confidence': float }
        """
        import math
        history = db.get_all_closed_dnas(limit=1000)
        # [Fix Bug#3] Naikkan default confidence ke 60 agar melewati dynamic_threshold (55)
        # Sebelumnya 50% selalu gagal saat DB kosong (50 < threshold 55 = SEMUA koin di-void)
        default_resp = {"verdict": "BUY", "target_tp": 0.04, "target_sl": -0.02, "confidence": 60.0}
        
        if not history or len(history) < 3:
            return default_resp
            
        distances = []
        for h in history:
            try:
                # Normalisasi jarak k-NN (Backtest-Tuned: 5-Factor Feature Space)
                # [Backtest Insight] ema_dist adalah PREDICTOR TERKUAT:
                # Win avg_ema_dist=0.1148 vs Lose avg_ema_dist=0.0133 (8.6x lebih besar)
                d_vol = (current_dna.get('volatility', 0) - h.get('volatility', 0)) / 2.0
                d_imb = (current_dna.get('imbalance', 1) - h.get('imbalance', 1)) / 1.5
                d_ai  = (current_dna.get('ai_prob', 0) - h.get('ai_prob', 0)) / 20.0
                d_rsi = (current_dna.get('rsi', 50) - h.get('rsi', 50)) / 15.0
                d_ema = (current_dna.get('ema_dist', 0) - h.get('ema_dist', 0)) / 0.1  # Scale dinaikkan
                
                # Weighted Manhattan: ema_dist bobot 2.5x (vs sebelumnya 0.8x)
                # RSI diturunkan ke 0.6x (backtest: RSI hampir sama antara WIN dan LOSE)
                dist = abs(d_vol)*1.2 + abs(d_imb)*1.0 + abs(d_ai)*1.0 + abs(d_rsi)*0.6 + abs(d_ema)*2.5
                
                # [Senior Optimization] Recency Bias (Forward-Looking)
                # Pola yang terjadi baru-baru ini lebih relevan untuk scalping.
                # Berikan diskon jarak 25% jika data terjadi kurang dari 10 jam lalu.
                try:
                    from datetime import datetime, timedelta
                    created_at = h.get('created_at') # DNA usually has this from DB
                    if created_at:
                        if isinstance(created_at, str):
                            dt_dna = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        else:
                            dt_dna = created_at
                        
                        if (datetime.now(dt_dna.tzinfo) - dt_dna).total_seconds() < 36000: # 10 Hours
                            dist *= 0.75 # Make it look 'closer' (more relevant)
                except:
                    pass

                distances.append((dist, h))
            except:
                continue
                
        distances.sort(key=lambda x: x[0])
        top_matches = [item[1] for item in distances[:5]]
        # Susun laporan historis mendalam untuk LiteRT-LM reasoning
        win_count = sum(1 for t in top_matches if t.get('pnl_idr', 0) > 0)
        win_rate = win_count / len(top_matches) if top_matches else 0
        win_rate_knn = win_rate
        
        history_details = []
        for t in top_matches:
            outcome = "WIN" if t.get('pnl_idr', 0) > 0 else "LOSS"
            history_details.append(
                f"- {outcome} ({t.get('pnl_pct', 0):.2f}%): RSI={t.get('rsi', 50)}, "
                f"EMA_Dist={t.get('ema_dist', 0):.4f}, Vol={t.get('volatility', 0):.2f}%"
            )
        
        history_text = "\n".join(history_details)
        
        prompt = (
            f"URGENT TRADE ANALYSIS FOR {pair.upper()} (MEAN REVERSION / BTD MODE).\n"
            f"Current DNA: RSI={current_dna.get('rsi', 50)}, EMA_Dist={current_dna.get('ema_dist', 0):.4f}, "
            f"Volatility={current_dna.get('volatility', 0):.2f}%, Imbalance={current_dna.get('imbalance', 1):.2f}.\n"
            f"\nCompare with 5 similar patterns (STRATEGY: BUY THE DIP AT EMA-20, TARGET: 4% TP):\n{history_text}\n"
            f"\nYour Task: Identify High-Probability Bounces. We target 4.0% Profit with 2.0% Stop Loss (1:2 RR). "
            "Only suggest BUY if historical patterns confirm a strong bounce from this oversold level (RSI < 45). "
            "If history shows continued dumping, return AVOID. Fees (0.51%) are already factored in. "
            "Return JSON: verdict (BUY/AVOID), confidence (0-100), TP (float, default 0.04), SL (float, default -0.02), eta_minutes (int, default 15)."
        )
        task_id = self.llm_worker.query_structured(f"KNN_STRATEGY:{pair}", prompt)
        
        # --- [Fix] Handle Async / Failed LLM with Statistical Fallback ---
        # [Senior Refactor] Removed Hard Veto (AVOID) per user request.
        # K-NN now acts as a 'Confidence Weight' rather than a blocker.
        # If history is bad, it just gives low confidence instead of Vetoing.
        
        current_rsi = current_dna.get('rsi', 50)
        current_ema_dist = current_dna.get('ema_dist', 0)
        
        # "Strong setup" hanya untuk oversold dalam (RSI < 35), bukan sekadar dip biasa.
        # RSI < 35 lebih jarang terjadi sehingga lebih selektif dan lebih andal sebagai sinyal.
        is_strong_setup = current_rsi < 35 or current_ema_dist > 0.10
        
        verdict = "BUY" # Hard Veto removed: Always allow technical evaluation
        
        # Base confidence from k-NN historical win rate
        base_confidence = win_rate_knn * 100
        
        # Gunakan K-NN win rate mentah tanpa floor artifisial.
        # Floor artifisial (52%/65%) sebelumnya menyebabkan bot over-confident
        # pada setup buruk dan masuk ke trade yang berakhir SL.
        # Break-even bot memerlukan win rate >= 65%; confidence floor harus
        # mencerminkan data historis nyata, bukan angka yang dimanipulasi.
        n_valid = sum(1 for t in top_matches if t.get('rsi') is not None or t.get('ema_dist') is not None)
        has_quality_features = n_valid >= 3
        
        if not has_quality_features:
            # Feature-blind K-NN — tidak bisa dipercaya.
            # Gunakan nilai netral (50%) agar gate downstream memutuskan berdasarkan indikator lain.
            confidence = min(base_confidence, 50.0) if base_confidence > 0 else 50.0
            reason = f"K-NN Feature-Blind Fallback (netral 50%, raw={base_confidence:.0f}%)"
        elif is_strong_setup:
            # Deep oversold (RSI < 35): bisa sedikit boost karena mean-reversion lebih kuat,
            # tapi tetap tidak melebihi 70% agar gate downstream masih efektif.
            confidence = min(max(base_confidence, 55.0), 70.0)
            reason = f"Deep-Oversold Mode (RSI {current_rsi:.0f}, k-NN WinRate: {win_rate_knn*100:.0f}%)"
        else:
            confidence = base_confidence
            reason = f"Historical Weighted (k-NN WinRate: {win_rate_knn*100:.0f}%)"

        heuristic_resp = {
            "verdict": verdict,
            "target_tp": 0.04,
            "target_sl": -0.02,
            "confidence": confidence,
            "reason": reason
        }

        # Log to Dashboard
        msg = f"🧠 AI Rank: {heuristic_resp['confidence']:.1f}% | {heuristic_resp['reason']}"
        db.log_system_event(pair, 'AI_ORACLE', msg, 'INFO')

        return heuristic_resp

    def apply_learning_to_score(self, base_score, features):
        """
        Gunakan bobot hasil belajar untuk memodifikasi skor akhir.
        """
        multiplier = 1.0
        for feat, val in features.items():
            weight = self.weights.get(feat, 1.0)
            # [Fix] Gunakan != 0 agar fitur negatif (seperti Sektor Merah) juga diproses
            if val != 0: 
                multiplier *= weight
                
        return base_score * multiplier

    def apply_weight_decay(self):
        """
        [Anti-Hallucination] 
        Secara perlahan mengembalikan bobot ke 1.0 agar bot tidak terjebak di masa lalu.
        """
        try:
            DECAY_RATE = 0.01 # 1% setiap jam
            new_weights = {}
            for feat, weight in self.weights.items():
                if weight > 1.0:
                    new_w = max(1.0, weight - DECAY_RATE)
                elif weight < 1.0:
                    new_w = min(1.0, weight + DECAY_RATE)
                else:
                    new_w = 1.0
                
                new_weights[feat] = new_w
                db.update_dynamic_weight(feat, new_w)
            
            self.weights = new_weights
            logger.info("[Oracle] Weight Decay: Dynamic weights normalized toward 1.0.")
        except Exception as e:
            logger.error(f"[Oracle] Decay Error: {e}")
