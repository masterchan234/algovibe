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
            
        # Private WS — menerima order update real-time
        self.private_ws = PrivateWS(
            api_key    = os.getenv('INDODAX_API_KEY', ''),
            api_secret = os.getenv('INDODAX_API_SECRET', ''),
            on_order_update = self.exit_manager.handle_order_update
        )
        
        log_message("Oracle Engine initialized. Consciousness level: FULL.")
        init_msg = (
            f"🚀 *ALGOVIBE SCALPER v3.0* 🚀\n"
            f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}\n"
            f"Daily Trades: {self.daily_trades}\n"
            f"Loss Streak: {self.consecutive_losses}\n"
            f"Oracle: ONLINE"
        )
        log_message(init_msg.replace('*', '').replace('\n', ' | '))
        send_telegram_message(init_msg)

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

    def execute_trade(self, pair, price, trend="N/A", tq_prob=0.0, whale_boost=1.0, sector_boost=1.0, imbalance=1.0, volatility=0.0):
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
            buy_price     = price * (1 - BUY_MAKER_OFFSET)
            amount_crypto = amount_idr / buy_price
            
            # Estimate Targets
            tp_target = buy_price * (1 + TP_PCT)
            sl_target = buy_price * (1 + SL_PCT)

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
                "exit_order_id": None
            }

            db.upsert_position(pair, data)
            return True

        except Exception as e:
            log_message(f"Trade Error: {e}")
            return False

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

        log_message("WebSocket clients running. Starting main loop…")

        while True:
            # ── Oracle Health & Regime Check ───────────────────────────────
            if not self.oracle.is_healthy():
                log_message(f"ORACLE: {self.oracle.get_rationale('', 'SKIP_LATENCY')}")
                time.sleep(10)
                continue
                
            regime = self.oracle.get_market_regime()
            adaptive_cfg = self.oracle.get_adaptive_config()
            
            # Update Sector Sentiment every 10 minutes
            if int(time.time()) % 600 < 10:
                self.oracle.update_sector_sentiment()
                
            # [Anti-Hallucination] Reality Check every 30 minutes
            if int(time.time()) % 1800 < 10:
                self.exit_manager.reconcile_with_exchange()
                
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

            # [Project Predator] Real-time Fake Pump Detection for Top 5
            for r in ranked[:5]:
                self.oracle.detect_fake_pump(r['pair'], r['change'])
            
            # Manage Shadow Jaring (Liquidity Grab)
            self.exit_manager.manage_shadow_jaring(ranked, tickers)

            log_message(
                f"Oracle Loop | Regime: {regime:8} | "
                f"AI_Thresh: {adaptive_cfg['ai_threshold']*100:.0f}% | "
                f"MinVol: {adaptive_cfg['min_vol']/1_000_000:.0f}M"
            )
            
            # Take Top N
            high_speed = [p for p in ranked if p['volatility'] >= MIN_TRACKING_VOLATILITY][:MAX_TRACKED_PAIRS]
            open_positions = self.exit_manager.get_open_positions()

            for p in high_speed:
                pair = p['pair']
                if pair in open_positions or pair in self.blacklist:
                    continue
                
                trend, breakout, history = self.update_history_and_trend(pair, p['last'])
                imbalance_ratio = p.get('imbalance', 1.0)
                # Get Prediction Probability
                tq_prob = get_prediction(history) if trend == "UP" else 0.0
                
                # [Project Predator] Sync prediction to DB for Dashboard
                db.save_ticker_history(pair, history, prediction_prob=tq_prob, trend=trend)
                
                # Predator Boosts (Whale & Sector)
                whale_boost = self.oracle.get_whale_boost(pair)
                sector_boost = self.oracle.get_sector_boost(pair)
                
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

                # Signal Trigger: Trend UP + Breakout + AI Probability >= Threshold
                has_strong_imb = imbalance_ratio >= STRONG_IMBALANCE_RATIO
                ai_ready = tq_prob >= adaptive_cfg['ai_threshold']
                
                # RE-CHECK PERSISTENT STATE BEFORE TRADE
                self.consecutive_losses = int(db.get_state("consecutive_losses", 0))
                if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    log_message(f"PAUSE: Hit max losses ({MAX_CONSECUTIVE_LOSSES}).")
                    time.sleep(60)
                    break

                if breakout and ai_ready and (trend == "UP" and has_strong_imb):
                    if self.daily_trades < MAX_TRADES_PER_DAY:
                        if self.execute_trade(pair, p['last'], trend=trend, tq_prob=tq_prob, 
                                             whale_boost=whale_boost, sector_boost=sector_boost,
                                             imbalance=imbalance_ratio, volatility=p['volatility']):
                            self.daily_trades += 1
                            db.save_state("daily_trades", self.daily_trades)

            time.sleep(5)

def calculate_score(args):
    """Fungsi pembantu untuk multiprocessing ranking."""
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
        imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 0

        max_spread = config_override['max_spread'] * 100
        min_vol = config_override['min_vol']
        
        if spread < max_spread and 0.2 <= volatility <= 8.0 and vol > min_vol:
            imb_bonus = (imbalance * 50_000_000) if imbalance > 1.2 else 0
            score = (vol * 0.5) + (volatility * 0.3) - (spread * 0.2) + imb_bonus
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