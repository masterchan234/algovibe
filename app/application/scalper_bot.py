import os
import json
import time
import subprocess
from datetime import datetime, timezone
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
from app.infrastructure.resilience import AutoResumeHeartbeat, emergency_notifier
from utils.logger import log_message, send_telegram_message

# WIB = UTC+7
_WIB_OFFSET_HOURS = 7


class ScalperBot:
    def __init__(self):
        load_dotenv()

        self.exchange = create_indodax_exchange()

        self.market_ws = MarketDataWS()
        self.oracle = Oracle(self.market_ws)
        self.exit_manager = ExitManager(self.exchange, oracle=self.oracle, bot=self)

        self.start_day = int(db.get_state("start_day", datetime.now().day))
        self.daily_trades = int(db.get_state("daily_trades", 0))
        self.consecutive_losses = int(db.get_state("consecutive_losses", 0))

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
        self.exit_monitor_thread = threading.Thread(target=self._exit_monitor_loop, daemon=True)
        self.utc_reset_thread = threading.Thread(target=self._utc_reset_monitor_loop, daemon=True)
        self.daily_report_thread = threading.Thread(target=self._daily_report_loop, daemon=True)
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)

        # ── Auto-Resume Heartbeat ────────────────────────────────────────────
        # Uses a lightweight balance fetch to confirm exchange reachability.
        self.auto_resume_heartbeat = AutoResumeHeartbeat(
            ping_fn=self.exchange.fetch_balance,
            notifier=emergency_notifier,
        )

        # ── UTC Reset Persistence: check on startup ──────────────────────────
        # If the bot/server restarts on a new UTC day, trigger reset before trade logic.
        today_utc = datetime.now(timezone.utc).date().isoformat()
        last_reset = db.get_state("last_reset_date")
        if not last_reset:
            # First run — initialize LAST_RESET_DATE without resetting anything
            db.save_state("last_reset_date", today_utc)
            log_message(f"📅 [SYSTEM] LAST_RESET_DATE initialized: {today_utc}")
        elif today_utc > last_reset:
            log_message(f"📅 [STARTUP] New UTC day detected ({last_reset} → {today_utc}). Triggering session reset...")
            self._perform_utc_day_reset()

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
                self.cached_balance = float(bal.get('free', {}).get('IDR', 0))
                db.save_state("cached_idr_balance", str(self.cached_balance))
            except Exception as e:
                log_message(f"⚠️ Initial Balance Fetch Failed: {e}")

            # Crash Recovery: resume management of any existing live positions
            self._resume_live_positions()
            # Reconcile order_book table with exchange's actual open orders
            self._reconcile_open_orders()

        self._backfill_historical_data()

        # Hitung target profit harian awal saat startup
        initial_target = self.oracle.refresh_daily_profit_target(balance=self.cached_balance)

        init_msg = (
            f"🚀 *ALGOVIBE SCALPER v3.0* 🚀\n"
            f"Mode: {'DRY RUN' if config.DRY_RUN else 'LIVE'}\n"
            f"Oracle: ONLINE | Backfill: SYNCING\n"
            f"Target Profit Hari Ini: Rp {initial_target:,.0f}\n"
            f"⚙️ Manual Control Mode: Auto-SL Disabled"
        )
        log_message(init_msg.replace('*', '').replace('\n', ' | '))
        send_telegram_message(init_msg)

    def _resume_live_positions(self):
        """Crash Recovery: re-subscribe market streams for all open live positions.

        Ensures the exit monitor loop keeps tracking positions that were open when
        the bot last shut down — prevents orphan positions from going unmanaged.
        """
        try:
            live_positions = db.live.get_open_positions()
            if not live_positions:
                return
            for pair in live_positions:
                self.market_ws.subscribe_market_trades(pair)
                log_message(f"♻️ [RESUME] Re-subscribed market stream for live position: {pair}")
            log_message(
                f"✅ [CRASH RECOVERY] Resumed management of {len(live_positions)} live positions."
            )
        except Exception as e:
            log_message(f"⚠️ [RESUME] Failed to resume live positions: {e}")

    def _reconcile_open_orders(self):
        """Reconcile order_book table with actual open orders on Indodax.

        Called once at startup in LIVE mode.  Fetches all open orders from the
        exchange via CCXT and upserts their current status into order_book so the
        dashboard reflects the real state even after a restart.
        """
        try:
            open_orders = self.exchange.fetch_open_orders()
            if not open_orders:
                return
            for order in open_orders:
                pair = (order.get("symbol") or "").replace("/", "").lower()
                db.live.upsert_order_book(
                    order_id=str(order.get("id") or ""),
                    pair=pair,
                    side=(order.get("side") or "").upper(),
                    price=float(order.get("price") or 0),
                    amount=float(order.get("amount") or 0),
                    status=order.get("status") or "open",
                    raw_response=order,
                )
            log_message(
                f"✅ [ORDER RECONCILE] Synced {len(open_orders)} open orders into order_book."
            )
        except Exception as e:
            log_message(f"⚠️ [ORDER RECONCILE] Failed: {e}")

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

            # [BTC Fix] Selalu sertakan BTC/IDR dalam backfill agar Oracle regime filter
            # tidak mengalami error "Data BTC tidak tersedia di WS/DB".
            btc_symbol = next((s for s in full_idr_list if s.replace('/', '').lower() == 'btcidr'), None)
            if btc_symbol and btc_symbol not in idr_pairs:
                idr_pairs.insert(0, btc_symbol)
                log_message("📌 [BTC Fix] Forcing BTC/IDR into backfill for Oracle regime filter.")

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
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=config.SIGNAL_CANDLE_TIMEFRAME, limit=60)
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
                # Cache saldo IDR ke bot_state agar Oracle bisa membacanya untuk target harian
                db.save_state("cached_idr_balance", str(self.cached_balance))
                log_message(f"💰 [BALANCE SYNC] IDR Free: {self.cached_balance:,.0f} → DB Updated")
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

    def _exit_monitor_loop(self):
        """
        Dedicated background thread — evaluates open positions every 2 seconds.

        Decouples exit latency from the main market cycle (which scores 499 pairs
        and takes 10-15 seconds per iteration).  The 2-second interval ensures Tier 3
        force-exits and HWM pullback basket-exits fire within one full candle tick of
        the price reversal rather than being delayed by the ranking/sleep pipeline.
        """
        from app.domain.predictor import get_prediction
        while True:
            try:
                tickers = self.market_ws.get_tickers()
                if tickers:
                    self.exit_manager.monitor_and_execute(
                        current_tickers=tickers,
                        price_history=self.price_history,
                        get_prediction_fn=get_prediction,
                    )
            except Exception as e:
                log_message(f"Error in _exit_monitor_loop: {e}")
            time.sleep(2)

    def _insight_loop(self):
        while True:
            try:
                if config.TECHNICAL_ONLY_MODE:
                    if not getattr(self, "_technical_only_logged", False):
                        log_message("🧩 [TECHNICAL_ONLY_MODE] LLM insight loop disabled.")
                        self._technical_only_logged = True
                    time.sleep(5)
                    continue
                self.oracle.process_llm_insights()
            except Exception as e:
                log_message(f"Error in _insight_loop: {e}")
            time.sleep(2)

    def _perform_utc_day_reset(self):
        """
        Execute NEW_DAY_RESET sequence when UTC date advances.

        Resets all session-specific state so the bot starts fresh on the new UTC day:
          - peak_pnl_today → 0
          - session_mode   → NORMAL  (releases HARVEST_ONLY lock)
          - session_buy_enabled → true
          - Daily trade counters reset
          - last_reset_date updated to today (UTC)
          - strategic_break_payload cleared
        """
        today_utc = datetime.now(timezone.utc).date().isoformat()
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.start_day = datetime.now(timezone.utc).day
        db.save_state("peak_pnl_today", "0")
        db.save_state("session_mode", "NORMAL")
        db.save_state("session_buy_enabled", "true")
        db.save_state("session_latched", "false")   # release latch on new UTC day
        db.save_state("daily_trades", "0")
        db.save_state("consecutive_losses", "0")
        db.save_state("start_day", str(self.start_day))
        db.save_state("last_reset_date", today_utc)
        # Clear any pending strategic break so a stale popup doesn't reappear
        db.save_state("strategic_break_payload", "")
        msg = "📅 [SYSTEM] New UTC Day Detected. Session Reset Successful. Returning to NORMAL mode."
        log_message(msg)
        send_telegram_message(msg)

    def _utc_reset_monitor_loop(self):
        """
        High-priority background daemon: checks for UTC midnight every 60 seconds.
        Triggers _perform_utc_day_reset() when the UTC date advances.
        """
        while True:
            try:
                today_utc = datetime.now(timezone.utc).date().isoformat()
                last_reset = db.get_state("last_reset_date")
                if last_reset and today_utc > last_reset:
                    log_message(f"📅 [UTC MONITOR] New UTC day detected ({last_reset} → {today_utc}). Triggering reset...")
                    self._perform_utc_day_reset()
            except Exception as e:
                log_message(f"⚠️ UTC Reset Monitor Error: {e}")
            time.sleep(60)

    def _daily_report_loop(self):
        """Background daemon: sends a Telegram performance summary at 00:00 WIB every day.

        WIB = UTC+7.  The loop checks the current WIB hour/minute every 60 seconds.
        A state key 'last_daily_report_date' prevents duplicate sends on the same day.
        """
        while True:
            try:
                from datetime import timedelta
                now_wib = datetime.now(timezone.utc) + timedelta(hours=_WIB_OFFSET_HOURS)
                # Fire between 00:00 and 00:01 WIB
                if now_wib.hour == 0 and now_wib.minute == 0:
                    today_wib = now_wib.date().isoformat()
                    last_sent = db.get_state("last_daily_report_date")
                    if last_sent != today_wib:
                        self._send_daily_report()
                        db.save_state("last_daily_report_date", today_wib)
            except Exception as e:
                log_message(f"⚠️ Daily Report Loop Error: {e}")
            time.sleep(60)

    def _send_daily_report(self):
        """Compose and send the daily performance summary to Telegram."""
        try:
            from datetime import timedelta
            now_wib = datetime.now(timezone.utc) + timedelta(hours=_WIB_OFFSET_HOURS)
            stats = db.get_daily_stats()
            total = stats.get('total_trades', 0)
            wins  = stats.get('win_count', 0)
            losses = stats.get('loss_count', 0)
            win_rate = (wins / total * 100) if total > 0 else 0.0
            net_pnl  = stats.get('total_pnl_idr', 0.0)
            msg = (
                f"📊 *DAILY PERFORMANCE SUMMARY* 📊\n"
                f"Tanggal: {now_wib.strftime('%d %b %Y')} WIB\n"
                f"Total Trades: {total} | Win: {wins} | Loss: {losses}\n"
                f"Win Rate: {win_rate:.1f}%\n"
                f"Net Profit: Rp {net_pnl:,.0f}"
            )
            log_message(msg.replace('*', '').replace('\n', ' | '))
            send_telegram_message(msg)
        except Exception as e:
            log_message(f"⚠️ Daily Report Send Error: {e}")

    def _cleanup_loop(self):
        """Background daemon: periodic cleanup of zombie data and Docker build cache.

        Runs once every 24 hours to prevent disk bloat.
        """
        # Initial delay — let the bot stabilise first.
        time.sleep(3600)
        while True:
            try:
                self._run_cleanup()
            except Exception as e:
                log_message(f"⚠️ Cleanup Loop Error: {e}")
            time.sleep(86400)  # 24 hours

    def _run_cleanup(self):
        """Execute disk hygiene tasks:
        1. Prune old DB rows (system events, ticker history, analytics).
        2. Remove Docker build cache to prevent the observed 33GB accumulation.
           Set DOCKER_CLEANUP_ENABLED=false in .env to disable.
        """
        log_message("🧹 [CLEANUP] Starting scheduled disk hygiene...")
        # 1. Prune stale DB records
        try:
            db.prune_old_data(hours=48)
            db.purge_old_data(days=7)
            log_message("🧹 [CLEANUP] DB prune completed.")
        except Exception as e:
            log_message(f"⚠️ [CLEANUP] DB prune error: {e}")

        # 2. Docker build cache cleanup — controlled by DOCKER_CLEANUP_ENABLED env var.
        docker_cleanup_enabled = os.getenv("DOCKER_CLEANUP_ENABLED", "true").lower() == "true"
        if not docker_cleanup_enabled:
            log_message("ℹ️ [CLEANUP] Docker cleanup disabled via DOCKER_CLEANUP_ENABLED=false.")
            return
        try:
            result = subprocess.run(
                ["docker", "builder", "prune", "--force", "--filter", "until=24h"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                freed = lines[-1] if lines else "unknown"
                log_message(f"🧹 [CLEANUP] Docker build cache cleared. {freed}")
                send_telegram_message(f"🧹 *CLEANUP* Docker build cache cleared. {freed}")
            else:
                log_message(f"⚠️ [CLEANUP] Docker prune exited {result.returncode}: {result.stderr[:200]}")
        except FileNotFoundError:
            log_message("ℹ️ [CLEANUP] Docker not available — skipping build cache prune.")
        except Exception as e:
            log_message(f"⚠️ [CLEANUP] Docker prune error: {e}")

    def run(self):
        log_message("Starting Market Data WebSocket…")
        self.market_ws.start()

        if not self.market_ws.wait_ready(timeout=30):
            log_message("WARNING: Market WS not ready after 30s, will retry in loop.")
        
        # ── Subscribe to orderbook for all open positions ──
        # Ensures exit monitor gets fresh tickers immediately (doesn't wait for summary-24h broadcast)
        self.market_ws.subscribe_open_positions()

        log_message("Starting Private WebSocket…")
        self.private_ws.start()

        log_message("WebSocket clients running. Starting background threads…")
        self.insight_thread.start()
        self.exit_monitor_thread.start()
        self.utc_reset_thread.start()
        self.daily_report_thread.start()
        self.cleanup_thread.start()
        self.auto_resume_heartbeat.start()
        log_message("Starting main loop…")

        while True:
            run_market_cycle(self)
            # Sync balance ke DB setiap 20 detik untuk dashboard accuracy
            if not config.DRY_RUN and (time.time() - getattr(self, '_last_bal_sync_ts', 0) > 20):
                self._sync_balances_to_db()
                self._last_bal_sync_ts = time.time()

