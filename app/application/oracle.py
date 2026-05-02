import time
import json
import logging
from datetime import datetime, timedelta
from app.infrastructure.database import db
from app.infrastructure.llm_worker import LLMWorker
from utils.logger import log_message

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
        Merakit prompt akhir dari semua konteks yang telah dikumpulkan.
        ob_ctx: {'imbalance': float, 'buy_pressure': float, 'bid_depth': float,
                 'ask_depth': float, 'best_bid': float, 'best_ask': float, 'spread_pct': float,
                 'trade_summary': str}
        """
        return (
            f"=== QUANTITATIVE SCALPING ANALYSIS: {pair.upper()} ===\n"
            f"Price: Rp{last_price:,.2f} | VWAP: {ctx.get('vwap_signal', 'N/A')}\n"
            f"Momentum: {ctx.get('momentum', 'N/A')}\n"
            f"24h Range Position: {ctx.get('range_position', 'N/A')}\n"
            f"\n[TECHNICAL INDICATORS]\n"
            f"RSI={tech.get('rsi','N/A')} | Volatility={tech.get('volatility','N/A')}% | "
            f"Signal={tech.get('signal', tech.get('verdict','N/A'))} | "
            f"BuyPressure(OB)={ob_ctx.get('buy_pressure', 50):.1f}%\n"
            f"\n[ORDER BOOK MICROSTRUCTURE]\n"
            f"Bid/Ask Imbalance={ob_ctx.get('imbalance', 1.0):.2f}x (>1=bullish) | "
            f"Spread={ob_ctx.get('spread_pct', 0):.3f}%\n"
            f"Bid Depth=Rp{ob_ctx.get('bid_depth', 0):,.0f} | "
            f"Ask Depth=Rp{ob_ctx.get('ask_depth', 0):,.0f}\n"
            f"Best Bid=Rp{ob_ctx.get('best_bid', 0):,.2f} | "
            f"Best Ask=Rp{ob_ctx.get('best_ask', 0):,.2f}\n"
            f"\n[MARKET ACTIVITY & FLOW]\n"
            f"Trade Flow (50 trades): {ctx.get('trade_flow', 'N/A')}\n"
            f"Whale Prints: {ctx.get('whale_prints', 'None')}\n"
            f"Volume Anomaly: {ctx.get('volume_anomaly', 'N/A')}\n"
            f"Recent Trades: {ob_ctx.get('trade_summary', 'N/A')}\n"
            f"\n[MARKET CONTEXT]\n"
            f"Market Regime: {ctx.get('market_regime', 'N/A')}\n"
            f"Sector: {ctx.get('sector', 'N/A')}\n"
            f"Session: {ctx.get('session', 'N/A')}\n"
            f"\n[BOT MEMORY & HISTORY]\n"
            f"This Pair History: {ctx.get('pair_history', 'N/A')}\n"
            f"Recent Lessons: {ctx.get('lessons', 'None')}\n"
            f"\n[TASK]\n"
            f"{'Confirm or reject this BUY signal.' if task_type == 'auto' else 'Provide a sharp tactical analysis.'} "
            f"Using ALL data above — technicals, orderbook microstructure, flow, regime, and bot memory.\n"
            f"Output: Verdict (BUY/HOLD/AVOID) + 1 concise sentence reasoning + "
            f"Profit Target (Rp) + Stop Loss (Rp) + ETA (minutes).\n"
            f"Be objective: issue AVOID if orderbook is thin, sell flow dominates, or price is at 24h high. "
            f"Only suggest BUY if multiple signals align (RSI, flow, regime, history). Do not default to BUY."
        )

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
        from config import DB_PATH

        # Ambil top 5 koin dengan verdict BUY dan confidence tertinggi
        buy_candidates = sorted(
            [r for r in ranked_signals if r.get('tech_verdict') == 'BUY'],
            key=lambda x: x.get('tech_confidence', 0),
            reverse=True
        )[:5]

        if not buy_candidates:
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            for candidate in buy_candidates:
                pair = candidate['pair']
                if pair in self.pump_cooldowns:
                    age = (now - self.pump_cooldowns[pair]).total_seconds()
                    if age < 300:
                        continue

                row = conn.execute(
                    "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
                ).fetchone()
                prices = json.loads(row['history_json']) if (row and row['history_json']) else []
                tech   = get_technical_summary(prices)
                sig    = derive_trade_signal(tech)

                # Order Book dari DB
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
                ob_ctx = {
                    'buy_pressure': round(total_bid / total_ob * 100, 1) if total_ob > 0 else 50.0,
                    'imbalance':    round(total_bid / total_ask, 2) if total_ask > 0 else 1.0,
                    'bid_depth':    total_bid,
                    'ask_depth':    total_ask,
                    'best_bid':     best_bid,
                    'best_ask':     best_ask,
                    'spread_pct':   spread_pct,
                    'trade_summary': 'see Market Activity section',
                }

                # Bangun rich context (9 tier)
                tech['signal'] = sig['verdict']
                tech['reason'] = sig.get('reason', '')
                ctx = self._build_rich_market_context(pair, prices, tech, conn)

                last_price = float(prices[-1]) if prices else 0
                prompt = self._format_rich_prompt(pair, last_price, tech, ob_ctx, ctx, task_type='auto')
                self.llm_worker.query_structured(f"ANALYSIS:{pair}", prompt)
                self.pump_cooldowns[pair] = now
                logger.info(f"[AutoQueue] LLM rich-context queued for {pair} (BUY {sig.get('confidence',0)}%)")
        finally:
            conn.close()

    def process_llm_insights(self):
        """Called periodically to fetch async LLM insights."""
        # 1. Check for manual analysis requests from DB (triggered by Dashboard)
        on_demand_pair = db.get_state("trigger_analysis_pair")
        # Guard: hanya proses jika pair valid (bukan None, string kosong, atau literal "None")
        if on_demand_pair and isinstance(on_demand_pair, str) and on_demand_pair.strip().lower() not in ('', 'none'):
            db.save_state("trigger_analysis_pair", None)
            try:
                import sqlite3
                from config import DB_PATH
                from app.domain.technicals import get_technical_summary
                conn = sqlite3.connect(DB_PATH)
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
                # [Fix BUG #4] TIDAK apply sentiment ke pair manapun — konteks global, bukan pair spesifik
                self.llm_worker.results.pop(task_id)
            elif t_type == "REGIME_ANALYSIS":
                log_message(f"🤖 [LiteRT Insight] Regime Shift: {t_res}")
                db.save_state("latest_llm_insight", f"REGIME|{t_res}")
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
                else:
                    log_message(f"🤖 [LiteRT Insight] {label} {pair_name}: {t_res}")
                db.save_state("latest_llm_insight", f"{label}:{pair_name}|{t_res}")
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
        - LLM verdict == AVOID dengan confidence >= 65%
        - Analisis masih fresh (< 10 menit in-memory, < 2 jam di DB)

        Jika tidak ada analisis LLM → TIDAK veto (bot tetap jalan berdasarkan teknikal).
        """
        pair_lower = pair.lower()
        LLM_VETO_THRESHOLD   = 65   # Minimum confidence untuk veto
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
                recent_stats = db.get_daily_stats()

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
        from config import AI_CONFIDENCE_THRESHOLD, MIN_VOLUME_FILTER, MAX_ALLOWED_SPREAD_PCT, MIN_PRICE_FILTER
        
        overrides = {
            "ai_threshold": AI_CONFIDENCE_THRESHOLD,
            "min_vol": MIN_VOLUME_FILTER,
            "max_spread": MAX_ALLOWED_SPREAD_PCT,
            "min_price": MIN_PRICE_FILTER
        }
        
        if regime == MarketRegime.BULLISH:
            overrides["ai_threshold"] -= 0.05 # Loloskan lebih banyak di pasar Bullish
        elif regime == MarketRegime.BEARISH:
            overrides["ai_threshold"] += 0.10 # Lebih ketat di pasar Bearish
            overrides["min_vol"] *= 2.0       # Hanya ambil koin volume besar
        elif regime == MarketRegime.CRASH:
            overrides["ai_threshold"] = 0.95  # Hampir tidak beli apapun
            overrides["max_spread"] /= 2.0    # Spread harus sangat tipis
            
        # Self-Tuning based on recent performance
        performance_penalty = self.learn_from_history()
        overrides["ai_threshold"] += performance_penalty

        # --- [Advanced] Equity-Based Adaptation (Senior Strategy) ---
        try:
            from config import DRY_RUN
            stats = db.get_daily_stats(dry_run=1 if DRY_RUN else 0) # Ambil statistik 24 jam terakhir
            daily_pnl = stats.get('total_pnl_idr', 0)
            
            # 1. Profit-Pause (Proteksi Cuan)
            # Jika sudah profit > 5% dari modal harian (misal > 5jt), naikkan threshold sangat tinggi
            if daily_pnl > 5_000_000:
                logger.info(f"[Oracle] Equity-Shield: Profit Harian Tinggi (Rp {daily_pnl:,.0f}). Mengetatkan entry.")
                overrides["ai_threshold"] += 0.20 # Sangat selektif
            
            # 2. Recovery-Mode (Balikkan Keadaan)
            # Jika sedang merugi (red), turunkan threshold sedikit agar lebih agresif mencari peluang rebound
            elif daily_pnl < -2_000_000:
                logger.info(f"[Oracle] Recovery-Mode: Drawdown terdeteksi (Rp {daily_pnl:,.0f}). Melonggarkan entry.")
                overrides["ai_threshold"] -= 0.05 # Lebih agresif
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
                
                prompt = f"The cryptocurrency {pair} just jumped {price_change_pct:.1f}% in price, but the 'buy wall' (bids) is extremely thin ({buy_wall_idr:,.0f} IDR). Is this a 'fake pump' designed to trap buyers? Provide a 2-sentence analysis."
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
            
        prompt = (
            f"As a Senior Quantitative Crypto Analyst, analyze the price action of {target_pair.upper()} "
            f"which has moved {change:.2f}% in the last 24 hours. "
            "Give a sharp, professional market insight (risk & opportunity) in max 2 short sentences. "
            "Do not use generic warnings; be specific."
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
        prompt = (
            f"URGENT: The coin {pair.upper()} is {status} with a {change:.2f}% move! "
            "Analyze the orderbook depth and price action. Is this a trap or a real breakout? "
            "Give a 1-sentence warning or recommendation for scalpers."
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
        
        # Is the technical setup very strong?
        is_strong_setup = current_rsi < 40 or current_ema_dist > 0.10
        
        verdict = "BUY" # Hard Veto removed: Always allow technical evaluation
        
        # Base confidence from k-NN historical win rate
        base_confidence = win_rate_knn * 100
        
        # [Fix] Jika K-NN history terlalu sedikit (< 10 records) atau 
        # top_matches semua dari pasangan yang berbeda (rsi/ema_dist semua NULL),
        # gunakan floor lebih tinggi agar tidak memblokir semua trade.
        # Threshold 50% dipakai karena win rate historis keseluruhan sekitar 67%.
        n_valid = sum(1 for t in top_matches if t.get('rsi') is not None or t.get('ema_dist') is not None)
        has_quality_features = n_valid >= 3
        
        if not has_quality_features and not is_strong_setup:
            # Feature-blind K-NN — tidak bisa dipercaya, pakai floor sedang
            confidence = max(base_confidence, 52.0)
            reason = f"K-NN Feature-Blind Fallback (floor 52%, raw={base_confidence:.0f}%)"
        elif is_strong_setup:
            # Jika setup teknikal kuat, kita boost confidence-nya agar tidak terhalang histori buruk
            # Memberikan bot 'keberanian' untuk mencoba pattern baru yang potensial
            confidence = max(base_confidence, 65.0) 
            reason = f"Momentum-First Mode (RSI {current_rsi:.0f} > k-NN History)"
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
