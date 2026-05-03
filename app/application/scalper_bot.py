import os
import json
import time
from datetime import datetime
from collections import defaultdict, deque
from multiprocessing.pool import ThreadPool

import ccxt
from dotenv import load_dotenv

from app.application.use_cases.market_cycle import run_market_cycle
from app.application.use_cases.trade_execution import execute_trade as execute_trade_use_case
from app.infrastructure.exchange_factory import create_indodax_exchange
import config
from app.infrastructure.database import db
from app.application.exit_manager import ExitManager
from app.application.oracle import Oracle
from app.infrastructure.ws_market import MarketDataWS
from app.infrastructure.ws_private import PrivateWS
from utils.logger import log_message, send_telegram_message


class ScalperBot:
    def __init__(self):
        load_dotenv()

        self.exchange = create_indodax_exchange()

        self.market_ws = MarketDataWS()
        self.oracle = Oracle(self.market_ws)
        self.exit_manager = ExitManager(self.exchange, oracle=self.oracle)

        self.start_day = int(db.get_state("start_day", datetime.now().day))
        self.daily_trades = int(db.get_state("daily_trades", 0))
        self.consecutive_losses = int(db.get_state("consecutive_losses", 0))
        self.consecutive_shadow_wins = 0 # In-memory only for recovery streak

        # [Senior Fix] Stale Halt Recovery: Jika ada loss beruntun tapi trade terakhir sudah lama (> 4 jam), 
        # anggap sesi baru dan bebaskan bot dari PAUSE.
        if self.consecutive_losses > 0:
            last_trades = db.get_recent_trades(limit=1)
            if last_trades:
                try:
                    last_ts = datetime.fromisoformat(last_trades[0]['created_at'])
                    if (datetime.now() - last_ts).total_seconds() > 14400: # 4 jam
                        log_message(f"♻️  RECOVERY: Resetting stale consecutive_losses ({self.consecutive_losses} -> 0)")
                        self.consecutive_losses = 0
                        db.save_state("consecutive_losses", 0)
                except Exception as e:
                    log_message(f"⚠️ Recovery Error: {e}")

        self.price_history = defaultdict(lambda: deque(maxlen=100))
        
        # Initialize default blacklist to database (if empty on first startup)
        from config import DEFAULT_BLACKLIST_COINS
        db.initialize_default_blacklist(DEFAULT_BLACKLIST_COINS)
        
        # Load blacklist from database (managed dynamically, not hardcoded)
        self.blacklist = set(db.get_blacklist())
        self.pool = ThreadPool(processes=4)

        log_message("Loading price history from database...")
        db_history = db.get_all_ticker_history()
        for pair, history in db_history.items():
            self.price_history[pair].extend(history)

        import threading

        self.insight_thread = threading.Thread(target=self._insight_loop, daemon=True)

        self.private_ws = PrivateWS(
            api_key=os.getenv('INDODAX_API_KEY', ''),
            api_secret=os.getenv('INDODAX_API_SECRET', ''),
            on_order_update=self.exit_manager.handle_order_update,
        )

        from app.infrastructure.deadman import DeadmanSwitch

        self.deadman = DeadmanSwitch(
            api_key=os.getenv('INDODAX_API_KEY', ''),
            api_secret=os.getenv('INDODAX_API_SECRET', ''),
        )
        self.deadman.start()

        log_message("Oracle Engine initialized. Consciousness level: FULL.")

        self.oracle._load_lessons_from_db()
        self.exit_manager.reconcile_with_exchange()

        # Initial balance fetch for LIVE mode
        self.cached_balance = 0
        if not config.DRY_RUN:
            try:
                bal = self.exchange.fetch_balance()
                self.cached_balance = bal['free'].get('IDR', 0)
                log_message(f"💰 INITIAL BALANCE: Rp {self.cached_balance:,.0f}")
            except Exception as e:
                log_message(f"⚠️ Initial Balance Fetch Failed: {e}")

        self._backfill_historical_data()

        init_msg = (
            f"🚀 *ALGOVIBE SCALPER v3.0* 🚀\n"
            f"Mode: {'DRY RUN' if config.DRY_RUN else 'LIVE'}\n"
            f"Oracle: ONLINE | Backfill: SYNCING"
        )
        log_message(init_msg.replace('*', '').replace('\n', ' | '))
        send_telegram_message(init_msg)

    def _backfill_historical_data(self):
        log_message("⏳ Starting FULL Historical Backfill (1h window)...")
        db.save_state("backfill_status", "SYNCING")

        markets = []
        retry_count = 0
        while not markets and retry_count < 5:
            try:
                markets = self.exchange.fetch_markets()
            except ccxt.RateLimitExceeded:
                retry_count += 1
                log_message(
                    f"⚠️ [Rate Limit] Terkena blokir Indodax (429). Menunggu 60 detik... (Percobaan {retry_count}/5)"
                )
                time.sleep(60)
            except Exception as e:
                log_message(f"❌ Error fetching markets: {e}")
                break

        if not markets:
            log_message("❌ Gagal mengambil data market setelah beberapa percobaan. Melanjutkan dengan data lokal.")
            return

        try:
            full_idr_list = [m['symbol'] for m in markets if m['quote'] == 'IDR' and m['active']]

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
                self.deadman.set_pairs(full_idr_list)
                return

            self.deadman.set_pairs(full_idr_list)

            def fetch_one(symbol):
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=60)
                    if not ohlcv:
                        return None
                    prices = [c[4] for c in ohlcv]
                    pair_id = symbol.replace('/', '').lower()
                    return (pair_id, prices)
                except Exception:
                    return None

            batch_size = 15
            count = 0
            all_updates = []

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
                            
                            all_updates.append((
                                pair_id,
                                list(self.price_history[pair_id]),
                                ch_5m,
                                ch_15m,
                                ch_1h
                            ))
                        count += 1
                time.sleep(0.5) # Reduced from 2.5s due to faster DB I/O

            # ── [Senior Fix] Batch DB Commit ────────────────────────────────
            if all_updates:
                db.save_ticker_history_batch(all_updates)
                
            log_message(f"✅ FULL Backfill completed for {count} pairs (Optimized Batch Commit).")
            db.save_state("backfill_status", "COMPLETED")
        except Exception as e:
            log_message(f"⚠️ Backfill failed: {e}")
            db.save_state("backfill_status", "FAILED")

    def _sync_balances_to_db(self):
        """[Senior Logic] Sync real exchange balances to DB for Dashboard visibility."""
        if config.DRY_RUN:
            return
        try:
            bal = self.exchange.fetch_balance()
            db.save_balances(bal)
            if 'IDR' in bal:
                self.cached_balance = float(bal['IDR'].get('free', 0))
            # log_message("🔄 Balances synced to DB for Dashboard.")
        except Exception as e:
            log_message(f"⚠️ Balance Sync Failed: {e}")

    def get_tickers(self):
        tickers = self.market_ws.get_tickers()
        return tickers if tickers else None

    def update_history_and_trend(self, pair, price):
        history_deque = self.price_history[pair]
        history_deque.append(price)
        history = list(history_deque)

        avg_20 = sum(history[-20:]) / 20 if len(history) >= 20 else history[0]
        avg_50 = sum(history[-50:]) / 50 if len(history) >= 50 else avg_20

        is_uptrend = avg_20 > avg_50 * 1.001
        is_pullback = len(history) >= 50 and is_uptrend and price <= avg_20 * 1.002

        return "UP" if is_uptrend else "SIDEWAYS", is_pullback, history

    def execute_trade(
        self,
        pair,
        price,
        trend="N/A",
        tq_prob=0.0,
        whale_boost=1.0,
        sector_boost=1.0,
        imbalance=1.0,
        volatility=0.0,
        dynamic_tp=None,
        dynamic_sl=None,
        taker=False,
        llm_verdict="HOLD",
        llm_confidence=0.0,
        llm_reason="",
    ):
        return execute_trade_use_case(
            self,
            pair,
            price,
            trend=trend,
            tq_prob=tq_prob,
            whale_boost=whale_boost,
            sector_boost=sector_boost,
            imbalance=imbalance,
            volatility=volatility,
            dynamic_tp=dynamic_tp,
            dynamic_sl=dynamic_sl,
            taker=taker,
            llm_verdict=llm_verdict,
            llm_confidence=llm_confidence,
            llm_reason=llm_reason,
        )

    def execute_shadow_trade(self, pair, price, gate, target):
        """
        [Shadow Recovery] Eksekusi transaksi bayangan untuk mengetes kondisi pasar.
        Disimpan di tabel shadow_orders agar ExitManager bisa memantaunya.
        """
        try:
            # Pastikan tidak ada duplikasi shadow trade untuk pair yang sama
            existing = db.get_all_shadow_orders()
            if any(s['pair'] == pair for s in existing):
                return
            
            # [Limit] Maksimal 2 shadow probe sekaligus agar tidak spamming
            shadow_count = sum(1 for s in existing if s['order_id'].startswith('SHADOW-'))
            if shadow_count >= 2:
                return

            log_message(f"🔍 [SHADOW PROBE] Entering virtual trade for {pair} at Rp {price:,.2f}...")
            
            # Kita gunakan format yang mirip dengan jaring/position agar monitor_and_execute bisa handle
            data = {
                "pair": pair,
                "order_id": f"SHADOW-{int(time.time())}",
                "price": price,
                "amount": 0, # Dummy for shadow
                "target_tp": target['tp_price'],
                "target_sl": target['sl_price'],
                "is_shadow_probe": True, # Flag khusus
                "timestamp": datetime.now().isoformat()
            }
            
            db.upsert_shadow_order(pair, data['order_id'], data['price'], data['amount'])
            # Simpan metadata tambahan ke state agar terbaca di monitor loop
            db.save_state(f"shadow_meta_{pair}", json.dumps(data))
            
        except Exception as e:
            log_message(f"⚠️ Shadow Execution Error: {e}")

    def _insight_loop(self):
        while True:
            try:
                self.oracle.process_llm_insights()
            except Exception as e:
                log_message(f"Error in _insight_loop: {e}")
            time.sleep(2)

    def run(self):
        log_message("Starting Market Data WebSocket…")
        self.market_ws.start()

        if not self.market_ws.wait_ready(timeout=30):
            log_message("WARNING: Market WS not ready after 30s, will retry in loop.")

        log_message("Starting Private WebSocket…")
        self.private_ws.start()

        log_message("WebSocket clients running. Starting background threads…")
        self.insight_thread.start()
        log_message("Starting main loop…")

        while True:
            run_market_cycle(self)
            # Sync balance ke DB setiap siklus (atau tiap 60 detik)
            if not config.DRY_RUN and (time.time() - getattr(self, '_last_bal_sync_ts', 0) > 60):
                self._sync_balances_to_db()
                self._last_bal_sync_ts = time.time()

