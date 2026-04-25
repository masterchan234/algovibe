import os
import time

import collections
from datetime import datetime
from collections import defaultdict, deque
from multiprocessing.pool import ThreadPool

import ccxt
from dotenv import load_dotenv

from engines.database import db
from engines.ws_market import MarketDataWS
from engines.ws_private import PrivateWS
from engines.exit_manager import ExitManager
from engines.predictor import get_prediction
from engines.oracle import Oracle
from engines.technicals import get_technical_summary, derive_trade_signal
from config import (
    DRY_RUN, INITIAL_CAPITAL, MIN_BALANCE_THRESHOLD, MIN_TRADE_IDR,
    MAX_TRADES_PER_DAY, MAX_CONSECUTIVE_LOSSES, MAX_TRACKED_PAIRS,
    MIN_VOLUME_FILTER, MIN_TRACKING_VOLATILITY, STRONG_IMBALANCE_RATIO,
    AI_CONFIDENCE_THRESHOLD, CAPITAL_PER_TRADE_PCT, AI_MIN_HISTORY_POINTS
)
from utils.logger import log_message, send_telegram_message, run_performance_report

class ScalperBot:
    def __init__(self):
        load_dotenv()
        
        # ── Bursa Initialization ───────────────────────────────────────────
        self.exchange = ccxt.indodax({
            'apiKey': os.getenv('INDODAX_API_KEY'),
            'secret': os.getenv('INDODAX_API_SECRET'),
            'enableRateLimit': True,
        })
        
        # ── Oracle & Market Awareness ──────────────────────────────────────
        self.market_ws = MarketDataWS()
        self.oracle = Oracle(self.market_ws)
        self.exit_manager = ExitManager(self.exchange, dry_run=DRY_RUN, oracle=self.oracle)
        
        # ── State & Persistence ────────────────────────────────────────────
        self.start_day = int(db.get_state("start_day", datetime.now().day))
        self.daily_trades = int(db.get_state("daily_trades", 0))
        self.consecutive_losses = int(db.get_state("consecutive_losses", 0))
        
        # Memory-based history tracking with DB Recovery
        self.price_history = defaultdict(lambda: deque(maxlen=100))
        self.blacklist = set(db.get_blacklist())
        self.pool = ThreadPool(processes=8)
        
        # Load history from DB (Warm-up prevention)
        log_message("Loading price history from database...")
        db_history = db.get_all_ticker_history()
        for pair, history in db_history.items():
            self.price_history[pair].extend(history)
            
        # [LiteRT-LM] High Frequency Insight Thread
        import threading
        self.insight_thread = threading.Thread(target=self._insight_loop, daemon=True)
            
        # Private WS — menerima order update real-time
        self.private_ws = PrivateWS(
            api_key    = os.getenv('INDODAX_API_KEY', ''),
            api_secret = os.getenv('INDODAX_API_SECRET', ''),
            on_order_update = self.exit_manager.handle_order_update
        )
        
        log_message("Oracle Engine initialized. Consciousness level: FULL.")

        # ── Restore LLM Lessons dari DB (survive restart) ──
        self.oracle._load_lessons_from_db()
        
        # ── Backfill Historical Data (Warm-up) ───────────────────────────
        self._backfill_historical_data()
        
        init_msg = (
            f"🚀 *ALGOVIBE SCALPER v3.0* 🚀\n"
            f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}\n"
            f"Oracle: ONLINE | Backfill: SYNCING"
        )
        log_message(init_msg.replace('*', '').replace('\n', ' | '))
        send_telegram_message(init_msg)

    def _backfill_historical_data(self):
        """Ambil data candle 1 jam terakhir untuk SEMUA koin (Batch Process)."""
        log_message("⏳ Starting FULL Historical Backfill (1h window)...")
        db.save_state("backfill_status", "SYNCING")
        try:
            markets = self.exchange.fetch_markets()
            idr_pairs = [m['symbol'] for m in markets if m['quote'] == 'IDR' and m['active']]
            total_pairs = len(idr_pairs)
            log_message(f"📦 Total pairs to backfill: {total_pairs}")
            
            def fetch_one(symbol):
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=60)
                    if not ohlcv: return None
                    prices = [c[4] for c in ohlcv]
                    pair_id = symbol.replace('/', '').lower()
                    return (pair_id, prices)
                except: return None

            batch_size = 20
            count = 0
            for i in range(0, total_pairs, batch_size):
                batch = idr_pairs[i:i + batch_size]
                log_message(f"🔄 Syncing batch {i//batch_size + 1}... ({min(i+batch_size, total_pairs)}/{total_pairs})")
                
                results = self.pool.map(fetch_one, batch)
                
                for res in results:
                    if res:
                        pair_id, prices = res
                        self.price_history[pair_id].extend(prices)
                        if len(prices) >= 5:
                            last = prices[-1]
                            ch_5m = ((last - prices[-5]) / prices[-5] * 100) if len(prices) >= 5 else 0
                            ch_15m = ((last - prices[-15]) / prices[-15] * 100) if len(prices) >= 15 else 0
                            ch_1h = ((last - prices[0]) / prices[0] * 100) if len(prices) >= 60 else 0
                            db.save_ticker_history(pair_id, list(self.price_history[pair_id]), 0, "BACKFILLED", 
                                                 ch_5m, ch_15m, ch_1h, 0, 0)
                        count += 1
                
                import time
                time.sleep(1.0)
                
            log_message(f"✅ FULL Backfill completed for {count} pairs.")
            db.save_state("backfill_status", "COMPLETED")
        except Exception as e:
            log_message(f"⚠️ Backfill failed: {e}")
            db.save_state("backfill_status", "FAILED")

    def get_tickers(self):
        """Ambil data harga terbaru dari WebSocket buffer."""
        tickers = self.market_ws.get_tickers()
        return tickers if tickers else None

    def update_history_and_trend(self, pair, price):
        history_deque = self.price_history[pair]
        history_deque.append(price)
        history = list(history_deque)

        # Logic UP: Harga di atas rata-rata 10 candle terakhir + momentum positif
        avg_10 = sum(history[-10:]) / 10 if len(history) >= 10 else history[0]
        is_up = len(history) >= 10 and price > avg_10 and price >= history[-3]
        
        # Logic Breakout: Current > Max of prev 5, tapi harus di atas rata-rata 20 tick untuk memfilter spike sesaat
        avg_20 = sum(history[-20:]) / 20 if len(history) >= 20 else avg_10
        is_breakout = len(history) >= 20 and price > max(history[-6:-1]) and price > avg_20 * 1.001
        
        # Logic Flat Breakout: Current price > Avg of prev 10 + 0.3% (syarat lebih ketat)
        is_flat_breakout = len(history) >= 10 and price > avg_10 * 1.003
        
        return "UP" if is_up else "SIDEWAYS", (is_breakout or is_flat_breakout), history

    def execute_trade(self, pair, price, trend="N/A", tq_prob=0.0, whale_boost=1.0, sector_boost=1.0, imbalance=1.0, volatility=0.0, dynamic_tp=None, dynamic_sl=None):
        try:
            # ── Oracle Sizing Multiplier ─────────────────────────────────────
            multiplier = self.oracle.get_sizing_multiplier()
            if multiplier <= 0:
                log_message(f"SKIP: Oracle blocked trade ({self.oracle.get_rationale(pair, 'SKIP_REGIME')})")
                return False
                
            # ── Sizing ───────────────────────────────────────────────────────
            if DRY_RUN:
                amount_idr = INITIAL_CAPITAL * CAPITAL_PER_TRADE_PCT * multiplier
            else:
                balance = self.exchange.fetch_balance()['free']['IDR']
                if balance < MIN_BALANCE_THRESHOLD:
                    log_message(f"STOP: Balance below {MIN_BALANCE_THRESHOLD}")
                    return False
                amount_idr = balance * CAPITAL_PER_TRADE_PCT * multiplier

            if amount_idr < MIN_TRADE_IDR:
                log_message(f"SKIP: Capital {amount_idr:.0f} IDR below Min Trade {MIN_TRADE_IDR}")
                return False

            from config import BUY_MAKER_OFFSET, TP_PCT, SL_PCT
            
            final_tp = dynamic_tp if dynamic_tp is not None else TP_PCT
            final_sl = dynamic_sl if dynamic_sl is not None else SL_PCT
            buy_price     = price * (1 - BUY_MAKER_OFFSET)
            amount_crypto = amount_idr / buy_price
            
            # Estimate Targets
            tp_target = buy_price * (1 + final_tp)
            sl_target = buy_price * (1 + final_sl)

            if DRY_RUN:
                order = {
                    'id': f"DRY-{int(time.time())}",
                    'status': 'closed',
                    'price': buy_price,
                    'amount': amount_crypto,
                    'timestamp': datetime.now().isoformat()
                }
                # Semantic Rationale for Predator Features
                predator_msg = ""
                if whale_boost > 1.0: predator_msg += "\n🐳 *Whale Backed*: Tembok raksasa terdeteksi menjaga harga."
                if sector_boost > 1.0: predator_msg += "\n🔥 *Sector Driven*: Koin ini mengikuti momentum sektor yang sedang 'hot'."

                msg = (
                    f"🟢 *DRY RUN BUY* 🟢\n"
                    f"Pair: `{pair}`\n"
                    f"Reason: `{trend}` (Prob: {tq_prob*100:.1f}%)\n"
                    f"Rationale: _{self.oracle.get_rationale(pair, 'BUY')}_{predator_msg}\n"
                    f"Price: Rp {buy_price:,.2f}\n"
                    f"Qty: {amount_crypto:.8f}\n"
                    f"Target TP: Rp {tp_target:,.2f}\n"
                    f"Target SL: Rp {sl_target:,.2f}"
                )
                log_message(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
            else:
                msg = (
                    f"🟢 *LIVE BUY* 🟢\n"
                    f"Pair: `{pair}`\n"
                    f"Reason: `{trend}` (Prob: {tq_prob*100:.1f}%)\n"
                    f"Price: Rp {buy_price:,.2f}\n"
                    f"Qty: {amount_crypto:.8f}\n"
                    f"Target TP: Rp {tp_target:,.2f}\n"
                    f"Target SL: Rp {sl_target:,.2f}"
                )
                log_message(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
                
                params = {'post_only': True}
                order = self.exchange.create_limit_buy_order(pair, amount_crypto, buy_price, params)

            # ── Project Evolution: Save DNA for Learning ──
            dna_data = {
                "ai_prob": tq_prob,
                "whale_boost": whale_boost,
                "sector_sentiment": self.oracle.get_sector_sentiment_val(pair),
                "imbalance": imbalance,
                "volatility": volatility
            }
            db.save_trade_dna(pair, dna_data)

            # Audit log
            db.log_order(pair, "BUY", buy_price, amount_crypto, order.get('status'), order)

            # ── Simpan Posisi ke Tracker Database ──
            data = {
                "entry_price": buy_price,
                "amount":      amount_crypto,
                "amount_idr":  amount_idr,
                "timestamp":   datetime.now().isoformat(),
                "dry_run":     DRY_RUN,
                "status":      "OPEN" if DRY_RUN else "PENDING_BUY",
                "entry_order_id": order.get('id') if not DRY_RUN else None,
                "exit_order_id": None,
                "target_tp_pct": final_tp,
                "target_sl_pct": final_sl
            }

            db.upsert_position(pair, data)
            return True

        except Exception as e:
            log_message(f"Trade Error: {e}")
            return False

    def _insight_loop(self):
        """High frequency loop to react to user analysis requests instantly."""
        while True:
            try:
                self.oracle.process_llm_insights()
            except Exception as e:
                pass
            time.sleep(2)

    # Dashboard dijalankan oleh indodax-dashboard.service (systemd terpisah)

    def run(self):
        
        # ── Mulai WebSocket Clients ─────────────────────────────────────────
        log_message("Starting Market Data WebSocket…")
        self.market_ws.start()

        # Tunggu data pertama (max 30 detik)
        if not self.market_ws.wait_ready(timeout=30):
            log_message("WARNING: Market WS not ready after 30s, will retry in loop.")

        log_message("Starting Private WebSocket…")
        self.private_ws.start()

        log_message("WebSocket clients running. Starting background threads…")
        self.insight_thread.start()
        log_message("Starting main loop…")

        while True:
            # ── Oracle Health & Regime Check ───────────────────────────────
            if not self.oracle.is_healthy():
                log_message(f"ORACLE: {self.oracle.get_rationale('', 'SKIP_LATENCY')}")
                time.sleep(10)
                continue
                
            regime = self.oracle.get_market_regime()
            adaptive_cfg = self.oracle.get_adaptive_config()
            
            # process_llm_insights is now handled by self.insight_thread for responsiveness
            
            # Update Sector Sentiment every 10 minutes
            if int(time.time()) % 600 < 10:
                self.oracle.update_sector_sentiment()
                
            # [Anti-Hallucination] Reality Check every 30 minutes
            if int(time.time()) % 1800 < 10:
                self.exit_manager.reconcile_with_exchange()

            # [LiteRT-LM] Consciousness Update every 30 minutes
            self.oracle.update_consciousness(regime=regime)
                
            # [Anti-Hallucination] Weight Decay every hour
            if int(time.time()) % 3600 < 10:
                self.oracle.apply_weight_decay()
                
            # Daily Reset
            current_day = datetime.now().day
            if current_day != self.start_day:
                run_performance_report(log_fn=log_message)
                self.daily_trades = 0
                self.consecutive_losses = 0
                self.start_day = current_day
                db.save_state("daily_trades", 0)
                db.save_state("consecutive_losses", 0)
                db.save_state("start_day", self.start_day)
                db.purge_old_data() # [Fix] Hapus data lama harian
                log_message("New day detected. Counters reset & Database cleaned.")

            # ── Ambil tickers ──────────────────────────────────────────────
            tickers = self.get_tickers()
            if not tickers:
                log_message("Market WS: no data yet, waiting…")
                time.sleep(5)
                continue

            # Parallel Ranking (using adaptive filters)
            ranked = [r for r in self.pool.map(calculate_score, [(item, adaptive_cfg) for item in tickers.items()]) if r]
            ranked.sort(key=lambda x: x['score'], reverse=True)

            # Monitor Exits
            self.exit_manager.monitor_and_execute(
                current_tickers=tickers,
                price_history=self.price_history,
                get_prediction_fn=get_prediction
            )

            # [LiteRT-LM] Top Gainer Commentary (internal 5m cooldown)
            self.oracle.request_periodic_analysis(ranked)

            # [Project Predator] Real-time Fake Pump Detection for Top 5
            for r in ranked[:5]:
                pair   = r['pair']
                change = r['change']
                self.oracle.detect_fake_pump(pair, change)
                if abs(change) > 3.0:
                    self.oracle.request_pump_analysis(pair, change)

            # [LiteRT-LM] Auto-queue top BUY candidates setiap 60 detik
            # Perlu tech_verdict di setiap ranked item — hitung untuk top 20
            ranked_with_signals = []
            for r in ranked[:20]:
                try:
                    h = list(self.price_history.get(r['pair'], []))
                    t = get_technical_summary(h)
                    s = derive_trade_signal(t)
                    r_copy = dict(r)
                    r_copy['tech_verdict']    = s['verdict']
                    r_copy['tech_confidence'] = s['confidence']
                    ranked_with_signals.append(r_copy)
                except Exception:
                    pass
            self.oracle.auto_queue_buy_candidates(ranked_with_signals)
            
            # Manage Shadow Jaring (Liquidity Grab)
            self.exit_manager.manage_shadow_jaring(ranked, tickers)

            log_message(
                f"Oracle Loop | Regime: {regime:8} | "
                f"AI_Thresh: {adaptive_cfg['ai_threshold']*100:.0f}% | "
                f"MinVol: {adaptive_cfg['min_vol']/1_000_000:.0f}M"
            )
            
            # [Heartbeat] Update status agar dashboard tahu bot menyala
            db.save_state("heartbeat", time.time())
            
            # Take Top N
            high_speed = [p for p in ranked if p['volatility'] >= MIN_TRACKING_VOLATILITY][:MAX_TRACKED_PAIRS]
            open_positions = self.exit_manager.get_open_positions()

            for p in high_speed:
                pair = p['pair']
                if pair in open_positions or pair in self.blacklist:
                    continue
                
                trend, breakout, history = self.update_history_and_trend(pair, p['last'])
                imbalance_ratio = p.get('imbalance', 1.0)
                
                # Get specific AI boost for this pair (valid for 5 mins after analysis)
                ai_boost = self.oracle.get_sentiment_boost(pair)
                
                # Get Prediction Probability (using quantitative math + LLM qualitative opinion)
                tq_prob = get_prediction(history, tickers.get(pair), ai_boost) if trend == "UP" else 0.0
                
                # Calculate Multi-Timeframe Changes (Short-term)
                # Loop is ~60s, so 5m=5 pts, 15m=15 pts, 60m=60 pts
                ch_5m = 0.0; ch_15m = 0.0; ch_1h = 0.0
                if len(history) >= 2:
                    curr = float(p['last'])
                    p_5  = float(history[-5]) if len(history) >= 5 else float(history[0])
                    p_15 = float(history[-15]) if len(history) >= 15 else float(history[0])
                    p_60 = float(history[-60]) if len(history) >= 60 else float(history[0])
                    ch_5m  = ((curr - p_5) / p_5) * 100 if p_5 > 0 else 0
                    ch_15m = ((curr - p_15) / p_15) * 100 if p_15 > 0 else 0
                    ch_1h  = ((curr - p_60) / p_60) * 100 if p_60 > 0 else 0

                # Predator Boosts (Whale & Sector)
                whale_boost = self.oracle.get_whale_boost(pair)
                sector_boost = self.oracle.get_sector_boost(pair)

                # Combine boosts into a final Confidence Score for display
                # Each boost is a multiplier (e.g. 1.2), we convert it to an additive prob adjustment
                boost_adj = ((whale_boost - 1.0) * 0.1) + ((sector_boost - 1.0) * 0.1)
                final_prob = max(0.0, min(0.99, tq_prob + boost_adj))

                # [Project Predator] Sync prediction to DB for Dashboard
                db.save_ticker_history(pair, history, prediction_prob=final_prob, trend=trend,
                                     ch_5m=ch_5m, ch_15m=ch_15m, ch_1h=ch_1h,
                                     volatility=p['volatility'], score=p['score'])
                
                # Apply Self-Learning Weights
                features = {
                    "ai_prob": tq_prob,
                    "whale_boost": whale_boost - 1.0, # 0.5 becomes 0.5
                    "sector_sentiment": self.oracle.get_sector_sentiment_val(pair),
                    "imbalance": imbalance_ratio
                }
                final_score = self.oracle.apply_learning_to_score(p['score'], features)
                
                # Diagnostic Log
                prob_display = f"{tq_prob*100:.0f}%" if tq_prob >= 0 else "WARMUP"
                boost_str = ""
                if whale_boost > 1.0: boost_str += " 🐳"
                if sector_boost > 1.0: boost_str += " 🔥"
                if final_score > p['score']: boost_str += " 📈"
                if final_score < p['score']: boost_str += " 📉"
                
                log_message(
                    f"Track: {p['pair']:10} | Regime: {regime:8} | Trend: {trend:9} | "
                    f"BO: {str(breakout):5} | TQ_Prob: {prob_display}{boost_str}"
                )

                # ── LiteRT-LM Hard Block: SELL signal mencegah entry ──────────────
                tech = get_technical_summary(history)
                tech_sig = derive_trade_signal(tech)
                if tech_sig['verdict'] == 'SELL':
                    log_message(
                        f"TECH BLOCK [{pair}]: {tech_sig['reason']} "
                        f"(confidence {tech_sig['confidence']}%) — Trade dicegah."
                    )
                    continue

                # Signal Trigger: Trend UP + Breakout + AI Probability >= Threshold
                has_strong_imb = imbalance_ratio >= STRONG_IMBALANCE_RATIO
                ai_ready = tq_prob >= adaptive_cfg['ai_threshold']
                
                # RE-CHECK PERSISTENT STATE BEFORE TRADE
                self.consecutive_losses = int(db.get_state("consecutive_losses", 0))
                if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    log_message(f"PAUSE: Hit max losses ({MAX_CONSECUTIVE_LOSSES}).")
                    time.sleep(60)
                    break

                # ── Custom AI Pattern Matcher (k-NN + LLM) ────────────────
                current_dna = {
                    "ai_prob": tq_prob,
                    "whale_boost": whale_boost,
                    "sector_sentiment": sector_boost,
                    "imbalance": imbalance_ratio,
                    "volatility": p['volatility']
                }
                                # Biarkan AI/K-NN menentukan apakah ini pola kemenangan
                knn_decision = self.oracle.find_winning_pattern(pair, current_dna)
                
                # [Tuning] Dynamic Confidence Threshold berbasis Volatilitas
                current_vol = p['volatility']
                dynamic_threshold = 60.0
                if current_vol > 2.0: dynamic_threshold = 75.0
                elif current_vol < 0.6: dynamic_threshold = 55.0

                if knn_decision['verdict'] == "BUY" and knn_decision['confidence'] >= dynamic_threshold:
                    
                    if self.daily_trades < MAX_TRADES_PER_DAY:
                        if self.execute_trade(pair, p['last'], trend=trend, tq_prob=tq_prob, 
                                             whale_boost=whale_boost, sector_boost=sector_boost,
                                             imbalance=imbalance_ratio, volatility=p['volatility'],
                                             dynamic_tp=knn_decision['target_tp'], dynamic_sl=knn_decision['target_sl']):
                            self.daily_trades += 1
                            db.save_state("daily_trades", self.daily_trades)
                else:
                    # Pola kalah dominan, jangan masuk
                    if knn_decision['verdict'] == "AVOID":
                        log_message(f"🚫 [K-NN Avoid] {pair}: Pola identik dengan Falling Knife masa lalu. Skip.")

            time.sleep(5)

def calculate_score(args):
    """Fungsi pembantu untuk multiprocessing ranking."""
    import math
    item, config_override = args
    pair, d = item
    try:
        last = float(d['last'])
        open_24h = float(d.get('open', last))
        high_24h = float(d.get('high', last))
        low_24h  = float(d.get('low', last))
        vol = float(d.get('vol_idr', 0))
        
        # Spread & Volatility
        bid = float(d.get('bid', last * 0.999))
        ask = float(d.get('ask', last * 1.001))
        spread = ((ask - bid) / last) * 100
        volatility = ((high_24h - low_24h) / last) * 100
        
        # Depth Imbalance (jika ada data OB)
        bid_depth = float(d.get('bid_depth', 0))
        ask_depth = float(d.get('ask_depth', 0))
        imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 1.0

        max_spread = config_override['max_spread'] * 100
        min_vol = config_override['min_vol']
        
        if spread < max_spread and 0.2 <= volatility <= 8.0 and vol > min_vol:
            # ── [Fix ISSUE #9] Normalized Quant Scoring ──
            # 1. Log Volume (Skala: 10M = log(7), 1T = log(12)) -> mapping ke poin ~0-50
            log_vol = math.log10(vol) if vol > 0 else 0
            score_vol = max(0, (log_vol - 7) * 10)
            
            # 2. Volatility (Skala: 0-8%) -> mapping ke poin ~0-40
            score_vola = volatility * 5
            
            # 3. Imbalance (Skala bonus max 20 poin)
            score_imb = min(20, (imbalance - 1) * 10) if imbalance > 1.2 else 0
            
            # 4. Spread Penalty (Makin besar spread, makin minus)
            penalty_spread = spread * 20
            
            score = score_vol + score_vola + score_imb - penalty_spread
            
            return {
                    "pair": pair, "last": last, "spread": spread, 
                    "volatility": volatility, "score": score, "imbalance": imbalance,
                    "change": d.get('change_24h', 0)
            }
    except: pass
    return None

if __name__ == "__main__":
    bot = ScalperBot()
    bot.run()