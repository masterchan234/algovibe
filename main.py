import os
import time
import numpy as np

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
    AI_CONFIDENCE_THRESHOLD, CAPITAL_PER_TRADE_PCT, AI_MIN_HISTORY_POINTS,
    LOG_FILE
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
        self.pool = ThreadPool(processes=4)
        
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
        
        markets = []
        retry_count = 0
        while not markets and retry_count < 5:
            try:
                markets = self.exchange.fetch_markets()
            except ccxt.RateLimitExceeded:
                retry_count += 1
                log_message(f"⚠️ [Rate Limit] Terkena blokir Indodax (429). Menunggu 60 detik... (Percobaan {retry_count}/5)")
                time.sleep(60)
            except Exception as e:
                log_message(f"❌ Error fetching markets: {e}")
                break

        if not markets:
            log_message("❌ Gagal mengambil data market setelah beberapa percobaan. Melanjutkan dengan data lokal.")
            return

        try:
            full_idr_list = [m['symbol'] for m in markets if m['quote'] == 'IDR' and m['active']]
            
            # [Smart Sync] Hanya sinkronisasi koin yang datanya kurang di memori (< 50 points)
            idr_pairs = []
            skipped = 0
            for symbol in full_idr_list:
                pair_id = symbol.replace('/', '').lower()
                if len(self.price_history[pair_id]) < 50:
                    idr_pairs.append(symbol)
                else:
                    skipped += 1
            
            total_pairs = len(idr_pairs)
            log_message(f"📦 Smart Backfill: {total_pairs} pairs to sync | {skipped} pairs skipped (data warm in DB)")
            
            if not idr_pairs:
                log_message("✅ All market data is already warm. Skipping full backfill.")
                db.save_state("backfill_status", "COMPLETED")
                return

            def fetch_one(symbol):
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=60)
                    if not ohlcv: return None
                    prices = [c[4] for c in ohlcv]
                    pair_id = symbol.replace('/', '').lower()
                    return (pair_id, prices)
                except: return None

            batch_size = 10
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
                
                time.sleep(2.5)
                
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

        # Logic Mean Reversion / Pullback:
        # Hitung EMA-20 dan EMA-50 (simplified with SMA untuk speed di tick-data)
        avg_20 = sum(history[-20:]) / 20 if len(history) >= 20 else history[0]
        avg_50 = sum(history[-50:]) / 50 if len(history) >= 50 else avg_20
        
        # Trend Uptrend: avg_20 > avg_50
        # Pullback: Harga turun menyentuh/mendekati avg_20 (misal dalam 0.2%)
        is_uptrend = avg_20 > avg_50 * 1.001
        is_pullback = len(history) >= 50 and is_uptrend and price <= avg_20 * 1.002
        
        return "UP" if is_uptrend else "SIDEWAYS", is_pullback, history

    def execute_trade(self, pair, price, trend="N/A", tq_prob=0.0, whale_boost=1.0, sector_boost=1.0, imbalance=1.0, volatility=0.0, dynamic_tp=None, dynamic_sl=None, taker=False):
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

            from config import TP_PCT, SL_PCT
            final_tp = dynamic_tp if dynamic_tp is not None else TP_PCT
            final_sl = dynamic_sl if dynamic_sl is not None else SL_PCT
            
            # [Senior Fix] Definisikan history dari buffer real-time
            history = list(self.price_history[pair])
            prices = np.array(history, dtype=float)

            if taker:
                # Mode Agresif: Langsung hajar kanan di harga market
                buy_price = price
                log_message(f"🚀 [Taker Execute] Mengejar momentum di harga Market: Rp {buy_price:,.2f}")
            else:
                # Mode Hemat Fee: Antri di EMA-9 (Retest Entry)
                alpha = 2 / (9 + 1)
                ema_9 = prices[0]
                for p_val in prices:
                    ema_9 = (p_val * alpha) + (ema_9 * (1 - alpha))
                
                buy_price = ema_9
                log_message(f"🎣 [Maker Entry] Memasang jaring beli di EMA-9: Rp {buy_price:,.2f}")
                
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
                # Gunakan Limit Buy di EMA-9
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
                
                # [Senior Maintenance] Auto-Rotate Logs (> 24 jam)
                # Menghapus file log teks agar tidak membengkak, database tetap aman.
                if os.path.exists(LOG_FILE):
                    try:
                        # Truncate file log (start fresh for the new day)
                        with open(LOG_FILE, 'w') as f:
                            f.write(f"--- Log rotated at {datetime.now()} ---\n")
                        log_message("📜 LOGS: File log telah dirotasi otomatis (Daily Reset).")
                    except Exception as e:
                        log_message(f"⚠️ LOGS: Gagal merotasi log: {e}")

                # [Senior Maintenance] Prune Database Logs
                db.prune_old_data(hours=24)

                self.daily_trades = 0
                self.consecutive_losses = 0
                self.start_day = current_day
                db.save_state("daily_trades", 0)
                db.save_state("consecutive_losses", 0)
                db.save_state("start_day", self.start_day)
                log_message("New day detected. Counters reset & Logs rotated.")

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
            
            # ── [Priority Tracking] Open Positions > Watch Pair > Volatility ──
            open_positions = self.exit_manager.get_open_positions()
            watch_pair = db.get_state('current_watch_pair', 'btcidr')
            
            # Kumpulkan semua kandidat
            priority_pairs = list(open_positions.keys())
            if watch_pair not in priority_pairs:
                priority_pairs.append(watch_pair)
                
            # Tambahkan top volatility pairs hingga mencapai MAX_TRACKED_PAIRS
            high_speed_tickers = []
            seen_pairs = set()
            
            # 1. Masukkan koin prioritas (Open + Watch)
            for pair in priority_pairs:
                ticker = next((p for p in ranked if p['pair'] == pair), None)
                if ticker:
                    high_speed_tickers.append(ticker)
                    seen_pairs.add(pair)
            
            # 2. Masukkan koin berdasarkan Momentum Relatif (Score / Volatility)
            # [Smart Engineering] Kita gunakan Volatility-Adjusted Scoring.
            # Koin besar (BTC/ETH) akan masuk radar HANYA jika bergerak kuat relatif terhadap volatilitasnya.
            for p in ranked:
                if len(high_speed_tickers) >= MAX_TRACKED_PAIRS:
                    break
                
                # Hanya masukkan jika ada momentum nyata relatif terhadap volatilitas (Relative Score > Threshold)
                # Rumus: Skor Momentum / Volatilitas. Jika > 1.0 berarti bergerak di atas normal.
                relative_momentum = p['score'] / max(0.1, p['volatility'])
                
                if p['pair'] not in seen_pairs:
                    # Threshold 0.5 berarti koin tersebut punya 'kehidupan' momentum
                    if relative_momentum > 0.5 or p['volatility'] >= MIN_TRACKING_VOLATILITY:
                        high_speed_tickers.append(p)
                        seen_pairs.add(p['pair'])
            
            high_speed = high_speed_tickers
            
            # --- Market Regime Filter (BTC Protection) ---
            # Ambil harga BTC/IDR dari WebSocket (btcidr) untuk menghindari REST Rate Limit
            btc_data = tickers.get('btcidr')
            if btc_data:
                btc_last = float(btc_data['last'])
            else:
                # Fallback: Jika WS belum ada data BTC, ambil dari history terakhir di DB
                btc_history = db.get_ticker_history("BTC/IDR", limit=1)
                btc_last = btc_history[0]['last'] if btc_history else 0
                if btc_last == 0:
                    log_message("⚠️ Data BTC tidak tersedia di WS/DB. Melewati filter regime.")
                    btc_last = 999_999_999 # Anggap bullish agar tidak nyangkut
            
            # Simulasi EMA 20 BTC sederhana (menggunakan data history yang sudah ada di DB)
            btc_history = db.get_ticker_history("BTC/IDR", limit=20)
            if btc_history and len(btc_history) >= 20:
                btc_prices = [h['last'] for h in btc_history]
                btc_ema = sum(btc_prices) / 20
                if btc_last < btc_ema:
                    log_message(f"⚠️ [BTC Filter] BTC sedang bearish ({btc_last} < {btc_ema:.0f}). Bot stand-by.")
                    time.sleep(30)
                    continue
            


            for p in high_speed:
                pair = p['pair']
                
                # [Fix] Update history ALWAYS (Penting untuk Dashboard Sync)
                trend, breakout, history = self.update_history_and_trend(pair, p['last'])
                imbalance_ratio = p.get('imbalance', 1.0)
                
                # Get specific AI boost for this pair (valid for 5 mins after analysis)
                ai_boost = self.oracle.get_sentiment_boost(pair)
                
                # Get Prediction Probability (using quantitative math + LLM qualitative opinion)
                tq_prob = get_prediction(history, tickers.get(pair), ai_boost) if trend == "UP" else 0.0
                
                # Calculate Multi-Timeframe Changes (Short-term)
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
                boost_adj = ((whale_boost - 1.0) * 0.1) + ((sector_boost - 1.0) * 0.1)
                final_prob = max(0.0, min(0.99, tq_prob + boost_adj))

                # [Senior Engineer] Derive Real-time Signal for Dashboard
                tech = get_technical_summary(history)
                tech_sig = derive_trade_signal(tech)
                signal_verdict = tech_sig['verdict']

                # [Project Predator] Sync prediction & signal to DB for Dashboard
                db.save_ticker_history(pair, history, prediction_prob=final_prob, trend=trend,
                                     signal=signal_verdict,
                                     ch_5m=ch_5m, ch_15m=ch_15m, ch_1h=ch_1h,
                                     volatility=p['volatility'], score=p['score'])

                # SKIP TRADE TRIGGER JIKA SUDAH PUNYA POSISI ATAU BLACKLIST
                if pair in open_positions or pair in self.blacklist:
                    continue
                
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
                    f"PB: {str(breakout):5} | TQ_Prob: {prob_display}{boost_str}"
                )

                # ── [Fix Bug#2] Inject Order Book pressure ke sinyal teknikal ──
                # Sebelumnya: derive_trade_signal dipanggil tanpa buy_pressure → bp=50 selalu.
                # Sekarang: ambil data depth dari DB dan inject agar sinyal lebih akurat.
                tech = get_technical_summary(history)
                depth_data = db.get_market_depth(pair)
                if depth_data['bids'] and depth_data['asks']:
                    total_bid = sum(b['vol'] for b in depth_data['bids'])
                    total_ask = sum(a['vol'] for a in depth_data['asks'])
                    total_depth = total_bid + total_ask
                    if total_depth > 0:
                        tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)
                tech_sig = derive_trade_signal(tech)

                # ── [Fix Bug#2] Soft-warn SELL: hanya blok RSI overbought berat (>72) ──
                # Sebelumnya: semua SELL di-skip → memblok zona mean reversion (RSI 55-70).
                # Sekarang: hanya blok jika RSI benar-benar overbought, log tapi jangan exit.
                if tech_sig['verdict'] == 'SELL' and tech.get('rsi', 50) > 72:
                    log_message(
                        f"⛔ OVERBOUGHT BLOCK [{pair}]: RSI={tech.get('rsi'):.0f} — {tech_sig['reason']}"
                    )
                    continue
                elif tech_sig['verdict'] == 'SELL':
                    # Soft warning: catat tapi jangan skip
                    log_message(
                        f"⚠️  SELL-WARN [{pair}]: {tech_sig['reason']} — tetap evaluasi K-NN."
                    )

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
                # [Deep Context Tuning] Ambil indikator teknikal untuk memperkuat prediksi
                from engines.technicals import calculate_rsi
                rsi_val = calculate_rsi(history)
                
                # Hitung EMA 20 dan jarak harga (EMA Distance)
                if len(history) >= 20:
                    prices = np.array(history, dtype=float)
                    alpha = 2 / (20 + 1)
                    ema_20 = prices[0]
                    for price in prices[1:]:
                        ema_20 = (price * alpha) + (ema_20 * (1 - alpha))
                    # [Fix] Sinkronisasi rumus dengan backtester: positif = pullback di bawah EMA
                    ema_dist = ((ema_20 - p['last']) / ema_20) * 100
                else:
                    ema_dist = 0.0

                current_dna = {
                    "ai_prob": tq_prob,
                    "whale_boost": whale_boost,
                    "sector_sentiment": sector_boost,
                    "imbalance": imbalance_ratio,
                    "volatility": p['volatility'],
                    "rsi": rsi_val,
                    "ema_dist": ema_dist
                }
                                # Biarkan AI/K-NN menentukan apakah ini pola kemenangan
                knn_decision = self.oracle.find_winning_pattern(pair, current_dna)
                
                # [Tuning] Dynamic Confidence Threshold berbasis Volatilitas & EMA Distance
                # [Backtest Insight] ema_dist > 0.1% terbukti 8.6x lebih sering WIN
                # [Scalper's Edge] Forward-Looking Momentum
                # Untuk scalping, Order Book Imbalance (BP) adalah indikator masa depan terkuat.
                # Jika bid jauh lebih tebal dari ask (Buy Pressure), kita perkuat prediksinya.
                scalper_boost = 0.0
                if imbalance_ratio > 1.8: scalper_boost = 15.0 # Strong Bid Dominance
                elif imbalance_ratio > 1.3: scalper_boost = 8.0 # Moderate Bid Dominance
                
                current_vol = p['volatility']
                dynamic_threshold = 55.0
                if current_vol > 2.0: dynamic_threshold = 68.0
                elif current_vol < 0.6: dynamic_threshold = 50.0
                
                # Turunkan threshold jika ada pullback nyata ke EMA (confirmed setup)
                if ema_dist > 0.10: dynamic_threshold = max(45.0, dynamic_threshold - 10.0)


                if total_confidence >= dynamic_threshold:
                    log_message(f"🚀 [Scalper Boost] {pair}: Confidence {knn_decision['confidence']:.1f}% + Boost {scalper_boost:.1f}% = {total_confidence:.1f}% (Thresh: {dynamic_threshold})")
                    
                    if self.daily_trades < MAX_TRADES_PER_DAY:
                        # [Backtest Insight] Dynamic ATR-based TP/SL 
                        # Menghitung volatilitas aktual sebelum entry untuk target presisi
                        from engines.technicals import calculate_atr
                        try:
                            # Ambil data OHLCV 1m terbaru (limit 20 candle)
                            ohlcv_atr = self.exchange.fetch_ohlcv(pair, timeframe='1m', limit=20)
                            if ohlcv_atr:
                                highs = [c[2] for c in ohlcv_atr]
                                lows = [c[3] for c in ohlcv_atr]
                                closes = [c[4] for c in ohlcv_atr]
                                atr_val = calculate_atr(highs, lows, closes)
                                
                                # [Fee-Aware & Rebound Logic]
                                from config import FEE_BUY_TAKER, FEE_SELL_TAKER
                                round_trip_fee = FEE_BUY_TAKER + FEE_SELL_TAKER
                                # [Anti-Panic Sell] Gunakan ATR 2.5x untuk ruang napas lebih luas
                                base_sl_pct = (atr_val * 2.5) / p['last']
                                
                                # Jika RSI sangat rendah (< 35), berikan ruang SL lebih lebar (+25%)
                                rebound_multiplier = 1.25 if rsi_val < 35 else 1.0
                                final_sl_val = base_sl_pct * rebound_multiplier
                                
                                # [Senior Fix] Pastikan SL minimal 3.5% agar tidak terkena Panic Sell akibat noise
                                final_sl_val = max(0.035, final_sl_val)
                                
                                final_dyn_tp = min(0.12, max(0.02, (atr_val * 4.0) / p['last']))
                                final_dyn_sl = -min(0.06, final_sl_val) # Cap max SL 6%
                                
                                log_message(f"🎯 [ATR Target] {pair}: TP {final_dyn_tp*100:.1f}%, SL {final_dyn_sl*100:.1f}% (Rebound: {rebound_multiplier}x)")
                            else:
                                final_dyn_tp, final_dyn_sl = knn_decision['target_tp'], knn_decision['target_sl']
                        except Exception as e:
                            log_message(f"⚠️ ATR Calculation failed: {e}")
                            final_dyn_tp, final_dyn_sl = knn_decision['target_tp'], knn_decision['target_sl']

                        # [Senior Tuning] Execution Intelligence
                        # Taker (Instant) jika momentum meledak, Maker (Limit) jika sinyal standar untuk hemat fee.
                        use_taker = (total_confidence > 75.0) or (imbalance_ratio > 2.5)
                        mode_str = "🚀 TAKER (INSTANT)" if use_taker else "🎣 MAKER (LIMIT EMA-9)"
                        log_message(f"⚡ [Execution Mode] {pair}: {mode_str}")

                        if self.execute_trade(pair, p['last'], trend=trend, tq_prob=tq_prob, 
                                             whale_boost=whale_boost, sector_boost=sector_boost,
                                             imbalance=imbalance_ratio, volatility=p['volatility'],
                                             dynamic_tp=final_dyn_tp, dynamic_sl=final_dyn_sl,
                                             taker=use_taker):
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
        
        # [Senior Engineer Fix] Lepaskan batas atas volatilitas untuk Dashboard Ranking.
        # Koin yang sedang 'pumping' (>8%) justru yang paling trending.
        if spread < max_spread and volatility >= 0.1 and vol > min_vol:
            # ── [Enhanced Quant Scoring: Relative Momentum] ──
            # [Smart Thinking] Gunakan Volatility-Adjusted Change.
            # Rumus: (Abs_Change * Log_Volume) / max(0.5, Volatility)
            # Ini akan menonjolkan koin besar saat mereka 'breakout' dari range normalnya.
            change_24h = d.get('change_24h', 0)
            rel_momentum = (abs(change_24h) * 5) / max(0.5, volatility)
            
            # 1. Volume Power (Logarithmic scaling)
            log_vol = math.log10(vol) if vol > 0 else 0
            score_vol = max(0, (log_vol - 7) * 8)
            
            # 2. Spread Penalty (Makin besar spread, makin tidak likuid)
            penalty_spread = spread * 12
            
            # Final Score (Normalized roughly to 0-100+)
            # Koin stagnan (Change kecil vs Volatility besar) akan otomatis tereliminasi.
            score = score_vol + (rel_momentum * 10) - penalty_spread
            
            return {
                    "pair": pair, "last": last, "spread": spread, 
                    "volatility": volatility, "score": score, "imbalance": imbalance,
                    "change": change_24h,
                    "vol_idr": vol
            }
    except: pass
    return None

if __name__ == "__main__":
    bot = ScalperBot()
    bot.run()