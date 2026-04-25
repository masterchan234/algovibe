import time
import json
import logging
from datetime import datetime, timedelta
from engines.database import db
from engines.llm_worker import LLMWorker

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

        # ── Consciousness & Self-Learning ──────────────────────────────────
        # lessons: rolling list of lessons dari POST_MORTEM, max 20 entri
        self._lessons: list = []
        self.last_consciousness_update = datetime.now() - timedelta(minutes=31)  # force first update

        # veto cache: {pair: {verdict, confidence, reason, ts}}
        # Diisi saat LLM structured result masuk; dibaca oleh check_llm_veto()
        self._veto_cache: dict = {}
        
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

        from engines.technicals import get_technical_summary, derive_trade_signal
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
                # Skip jika baru di-queue (individual pair cooldown 5 menit)
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

                last_price = prices[-1] if prices else 0
                prompt = (
                    f"Context: Elite Quantitative Scalper. AUTO-SCAN. Pair: {pair.upper()}. Price: Rp {last_price:,.2f}. "
                    f"Data: RSI={tech['rsi']}, Vol={tech['volatility']}%, Signal={sig['verdict']} ({sig['confidence']}% confidence). "
                    f"Reason: {sig['reason']}. "
                    "Task: Confirm or reject this BUY signal with tactical precision. "
                    "Output: BUY/HOLD/AVOID verdict + 1 sentence reasoning + Profit Target + Stop Loss (Rp) + ETA (minutes)."
                )
                task_id = f"ANALYSIS:{pair}"
                # [Veto] Gunakan query_structured agar hasil bisa di-parse sebagai keputusan
                self.llm_worker.query_structured(task_id, prompt)
                self.pump_cooldowns[pair] = now
                logger.info(f"[AutoQueue] LLM structured queued for {pair} (BUY {sig['confidence']}%)")
        finally:
            conn.close()

    def process_llm_insights(self):
        """Called periodically to fetch async LLM insights."""
        # 1. Check for manual analysis requests from DB (triggered by Dashboard)
        on_demand_pair = db.get_state("trigger_analysis_pair")
        if on_demand_pair:
            db.save_state("trigger_analysis_pair", None)
            try:
                import sqlite3
                from config import DB_PATH
                from engines.technicals import get_technical_summary
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (on_demand_pair,)).fetchone()
                    prices = json.loads(row['history_json']) if (row and row['history_json']) else []
                    tech = get_technical_summary(prices)

                    depth = conn.execute("SELECT vol, is_bid FROM market_depth WHERE pair = ?", (on_demand_pair,)).fetchall()
                    bids  = sum(r['vol'] for r in depth if r['is_bid'] == 1)
                    asks  = sum(r['vol'] for r in depth if r['is_bid'] == 0)
                    total = bids + asks
                    tech['buy_pressure'] = round((bids / total * 100), 1) if total > 0 else 50.0
                finally:
                    conn.close()

                last_price = prices[-1] if prices else 0
                prompt = (
                    f"Context: Elite Quantitative Scalper. Pair: {on_demand_pair.upper()}. Price: Rp {last_price:,.2f}. "
                    f"Data: RSI={tech['rsi']}, Vol={tech['volatility']}%, Signal={tech['signal']}, BuyPressure={tech['buy_pressure']}%. "
                    "Task: Provide a sharp tactical analysis. "
                    "Output: Verdict (BUY/HOLD/AVOID). 1 Sentence Reasoning. "
                    "Targets: 1 Profit Target, 1 Stop Loss (Rp), and 1 Estimated Time Horizon (e.g. 5-15m)."
                )
                self.llm_worker.query_structured(f"ANALYSIS:{on_demand_pair}", prompt)
            except Exception:
                pass

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
                structured = LLMWorker.parse_structured_result(t_res)
                is_structured = structured.get("confidence", 0) > 0 or structured.get("verdict") in ("BUY","AVOID","HOLD")

                log_message(
                    f"🤖 [LiteRT Insight] {label} {pair_name}: "
                    f"{structured['verdict']} ({structured['confidence']}%) — {structured['reason']}"
                    if is_structured else f"🤖 [LiteRT Insight] {label} {pair_name}: {t_res}"
                )
                db.save_state("latest_llm_insight", f"{label}:{pair_name}|{t_res}")
                db.save_state(f"llm_reasoning_{pair_name}", t_res)

                # ── Veto Cache: simpan untuk check_llm_veto() ─────────────
                if is_structured and label in ("ANALYSIS", "PUMP_ALERT"):
                    self._veto_cache[pair_name] = {
                        **structured,
                        "ts": datetime.now()
                    }
                    db.save_state(f"llm_veto_{pair_name}", t_res)  # persist lintas restart

                # ── Sentiment (hanya pair spesifik, Fix BUG #4) ──────────
                self._update_llm_sentiment(t_res, pair_name)
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
        - Analisis masih fresh (< 10 menit)

        Jika tidak ada analisis LLM → TIDAK veto (bot tetap jalan berdasarkan teknikal).
        """
        pair_lower = pair.lower()
        LLM_VETO_THRESHOLD    = 65   # Minimum confidence untuk veto
        LLM_VETO_TTL_MINUTES  = 10   # Analisis lebih dari 10 menit dianggap stale

        # 1. Cek in-memory cache dulu (lebih cepat)
        cached = self._veto_cache.get(pair_lower)
        if cached:
            age_min = (datetime.now() - cached["ts"]).total_seconds() / 60
            if age_min <= LLM_VETO_TTL_MINUTES:
                if cached["verdict"] == "AVOID" and cached["confidence"] >= LLM_VETO_THRESHOLD:
                    return True, f"LLM VETO ({cached['confidence']}%): {cached['reason']}"
                return False, cached["reason"]

        # 2. Fallback: cek DB (persist lintas restart/insight_thread delay)
        try:
            raw = db.get_state(f"llm_veto_{pair_lower}")
            if raw:
                structured = LLMWorker.parse_structured_result(raw)
                # Tidak ada timestamp di DB — kita tidak tahu usianya, skip veto tapi log
                if structured["verdict"] == "AVOID" and structured["confidence"] >= LLM_VETO_THRESHOLD:
                    logger.info(f"[Oracle] LLM DB-Veto for {pair}: {structured['reason']}")
                    return True, f"LLM VETO (DB) ({structured['confidence']}%): {structured['reason']}"
        except Exception:
            pass

        return False, "No LLM verdict"

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
            
            prompt = f"The crypto market regime shifted from {self.last_regime} to {regime}. The leading coin is {leader_str}. The average 24h change of top 50 pairs is {avg_change:.2f}%. As an expert trader, give a 2-sentence strategic advice for trading in this environment."
            self.llm_worker.query_async("REGIME_ANALYSIS", prompt)
            
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
        from config import AI_CONFIDENCE_THRESHOLD, MIN_VOLUME_FILTER, MAX_ALLOWED_SPREAD_PCT
        
        overrides = {
            "ai_threshold": AI_CONFIDENCE_THRESHOLD,
            "min_vol": MIN_VOLUME_FILTER,
            "max_spread": MAX_ALLOWED_SPREAD_PCT
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
        
        return overrides

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

    def get_sizing_multiplier(self) -> float:
        """
        Adjust capital allocation based on market risk.
        """
        regime = self.get_market_regime()
        if regime == MarketRegime.CRASH: return 0.0 # Stop trading
        if regime == MarketRegime.BEARISH: return 0.5 # Half size
        if regime == MarketRegime.CHOPPY: return 0.8 # Conservative
        return 1.0 # Full speed

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
        Called periodically (e.g., every 5-10 minutes).
        """
        try:
            from config import SECTOR_MAP
            tickers = self.market_ws.get_tickers()
            if not tickers: return
            
            # {SECTOR: [change1, change2, ...]}
            sector_data = {}
            for pair, data in tickers.items():
                s = SECTOR_MAP.get(pair.lower())
                if s:
                    if s not in sector_data: sector_data[s] = []
                    sector_data[s].append(data.get('change_24h', 0))
            
            # Calculate and save average
            for sector, changes in sector_data.items():
                avg_change = sum(changes) / len(changes)
                db.save_sector_sentiment(sector, avg_change)
                
        except Exception as e:
            logger.error(f"[Oracle] Sector Update Error: {e}")

    # ── Self-Learning Engine (Project Evolution) ───────────────────────

    def learn_from_trade(self, pair, pnl_idr):
        """
        Analisa DNA transaksi terakhir dan sesuaikan bobot di database.
        """
        try:
            dna = db.get_latest_trade_dna(pair)
            if not dna: return
            
            # [Anti-Hallucination] Outlier Filter
            # Jika rugi > 15%, kemungkinan besar ada anomali pasar (flash crash/delist)
            # Bot tidak boleh belajar berlebihan dari kejadian 'Bencana Alam'
            if pnl_idr < -0.15: 
                logger.warning(f"[Oracle] Learning SKIP: Trade {pair} was an extreme outlier (Bencana Alam).")
                return
            
            # Learning Rate (Seberapa cepat bot berubah pikiran)
            LR = 0.05 
            
            is_profit = pnl_idr > 0
            
            # Update Bobot berdasarkan fitur yang ada saat beli
            # Jika fitur tersebut hadir saat PROFIT, naikkan bobotnya.
            # Jika fitur tersebut hadir saat LOSS, turunkan bobotnya.
            
            new_weights = {}
            for feature in ["ai_prob", "whale_boost", "sector_sentiment", "imbalance"]:
                current_w = self.weights.get(feature, 1.0)
                
                # Check presence/strength of feature in DNA (Zero-Centering)
                strength = dna.get(feature, 0)
                if feature == "ai_prob": 
                    strength -= 0.5 # 0 is neutral, >0 is confident
                elif feature == "sector_sentiment": 
                    strength /= 5.0 # Normalize sentiment
                elif feature == "whale_boost":
                    strength -= 1.0 # 0 if no whale, 0.5 if whale present
                elif feature == "imbalance":
                    strength -= 1.0 # 0 if balanced, >0 if highly imbalanced
                    
                
                if is_profit:
                    new_w = current_w + (LR * abs(strength))
                else:
                    new_w = current_w - (LR * abs(strength))
                
                # Clamp weights between 0.5 and 2.0 (Jangan biarkan terlalu ekstrim)
                new_w = max(0.5, min(2.0, new_w))
                new_weights[feature] = new_w
                db.update_dynamic_weight(feature, new_w)
            
            self.weights = new_weights
            logger.info(f"[Oracle] Evolution: Adjusted weights after {pair} trade. New Weights: {new_weights}")
            
            # [LiteRT-LM + Self-Learning] Request Post-Mortem Analysis
            status = "PROFIT" if is_profit else "LOSS"
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
