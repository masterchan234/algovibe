import json
import os
import time
import logging
import re
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from config import (
    TP_PCT, SL_PCT, PENDING_BUY_TIMEOUT_MINUTES, PENDING_BUY_MAX_SPREAD_PCT,
    FEE_BUY_MAKER, FEE_SELL_MAKER, FEE_BUY_TAKER, FEE_SELL_TAKER,
    MIN_NET_PROFIT_IDR, BASKET_HARVEST_TARGET_IDR, BASKET_HWM_PULLBACK_PCT,
    AUTO_BASKET_HARVEST_MIN, BTC_CRASH_TRIGGER_PCT,
    SELL_TAKER_OFFSET, SELL_MAKER_OFFSET, JARING_ALLOCATION_PCT, JARING_DEPTH_PCT,
    INITIAL_CAPITAL, TECH_MIN_HISTORY_POINTS,
    DCA_ENABLED, DCA_MAX_TIERS, DCA_STEP_PCT, DCA_MULTIPLIER, DCA_MIN_TECH_CONFIDENCE,
    ROUND_TRIP_FEE_PCT, SMART_BREAK_EVEN_PCT,
    MOMENTUM_TARGET_PNL_IDR, MOMENTUM_SAFETY_BUFFER_IDR, MOMENTUM_RECOVERY_THRESHOLD,
    MOMENTUM_VELOCITY_INTERVAL_SEC, MOMENTUM_TIGHT_TRAILING_PCT,
    MOMENTUM_HIGH_CORR_BUFFER_REDUCTION,
    NET_ZERO_SURVIVAL_ENABLED, NET_ZERO_MOMENTUM_THRESHOLD, NET_ZERO_HYSTERESIS_SEC,
    NET_ZERO_IMMEDIATE_VELOCITY,
    NET_ZERO_PNL_FLOOR_IDR, NET_ZERO_MIN_UNREALIZED_LOSS_PCT,
    NET_ZERO_MIN_POSITIONS, NET_ZERO_AUTO_RESET_SEC,
)
from app.infrastructure.database import db
from app.domain.technicals import (
    get_technical_summary, derive_trade_signal,
    calculate_macd, calculate_stochastic_rsi,
)
from utils.logger import log_message, send_telegram_message

class ExitManager:
    def __init__(self, exchange_client, oracle=None, bot=None):
        self.exchange = exchange_client
        self.oracle = oracle
        self.bot = bot
        self.jaring_orders = {} # {pair: {id, price, amount}}
        self.last_jaring_update = {} # {pair: timestamp}
        self.consecutive_shadow_wins = 0 # In-memory streak untuk Shadow Recovery
        self.last_balance_sync = 0 # Cooldown untuk auto-reconciliation
        # High-water-mark: tracks the peak combined net PnL seen across all open positions.
        # Updated each monitor tick when there are open positions; reset to 0 when no
        # open positions are being evaluated (i.e., the basket is empty at evaluation time).
        self._portfolio_hwm: float = 0.0
        # Rolling BTC price buffer: list of (unix_timestamp, price) for 5-min crash detection.
        self._btc_price_history: list = []
        # Throttle bypass log: timestamp of last "BASKET_EXIT Bypassed" log to avoid spam.
        self._last_basket_bypass_log_ts: float = 0.0
        # Prevents concurrent execution from the background exit-monitor thread and
        # any other caller (e.g. a manual reconcile triggered from the main cycle).
        self._monitor_lock = threading.Lock()
        # ─── Momentum Equity Protection ────────────────────────────────────────────
        # Last PnL sample for velocity calculation: (unix_timestamp, pnl_idr).
        self._last_pnl_sample: tuple = (0.0, 0.0)
        # Rolling buffer of the last 5 velocity readings (IDR / second).
        self._velocity_readings: list = []
        # Current momentum state published to the dashboard.
        self._momentum_state: str = "STALLING"
        # Latches True once the portfolio PnL has risen above the Safety Zone threshold,
        # activating the floor protection on any subsequent decline.
        self._was_above_safety_zone: bool = False
        # Tracks session peak net PnL for dashboard state recovery.
        self._peak_net_pnl_session: float = 0.0
        # Guard set: ensures "Target Reached" log + Telegram fires only once per session.
        self._target_fired_session: set = set()
        # ─── Net-Zero Survival Guard ───────────────────────────────────────────────
        # Timestamp when the Net-Zero condition (net_pnl ≤ 0 + dropping velocity) was
        # first detected.  Reset to 0 whenever the condition clears.
        self._net_zero_trigger_ts: float = 0.0
        # Session guard: True after the emergency basket exit has fired once, prevents
        # repeated triggers within the same session.
        self._net_zero_fired: bool = False
        # One-shot flag: audit log fired once to confirm pre-target guard is silent.
        self._net_zero_pretarget_logged: bool = False
        # Throttle for saving realised/unrealised PnL snapshots to bot_state (DB).
        self._last_pnl_snapshot_ts: float = 0.0
        self._sync_jaring_from_db()

    @property
    def dry_run(self):
        return db.is_dry_run()

    def get_open_positions(self):
        return db.get_open_positions()

    def save_positions(self, positions, modified_pairs):
        for pair in modified_pairs:
            # Guard against race condition: WS handler may have already deleted the
            # position (e.g. instant TAKER fill) while monitor loop was still running.
            if db.get_position(pair) is None:
                positions.pop(pair, None)
                continue
            db.upsert_position(pair, positions[pair])

    def _set_pair_cooldown(self, pair, minutes=20, reason="LOSS"):
        """Set cooldown per pair setelah trade loss agar tidak re-entry di market chop."""
        try:
            cooldown_until = time.time() + (minutes * 60)
            db.save_state(f"pair_cooldown_until_{pair}", str(cooldown_until))
            log_message(f"⏸️ [PAIR COOLDOWN] {pair}: {minutes}m ({reason})")
        except Exception as e:
            log_message(f"⚠️ Failed set cooldown {pair}: {e}")

    def _update_pnl_velocity(self, current_pnl: float, now_ts: float) -> None:
        """Sample Net PnL velocity every MOMENTUM_VELOCITY_INTERVAL_SEC seconds.

        Maintains a rolling buffer of the last 5 readings and derives the current
        momentum state: IMPROVING / STALLING / DROPPING.
        Publishes the state to DB so the dashboard can display it.
        """
        last_ts, last_pnl = self._last_pnl_sample
        elapsed = now_ts - last_ts

        if last_ts == 0.0:
            # First call — just record the baseline, no velocity yet.
            self._last_pnl_sample = (now_ts, current_pnl)
            return

        if elapsed < MOMENTUM_VELOCITY_INTERVAL_SEC:
            return  # Not enough time since last sample.

        if elapsed <= 0:
            return  # Guard against floating-point edge case.

        velocity = (current_pnl - last_pnl) / elapsed
        self._velocity_readings.append(velocity)
        if len(self._velocity_readings) > 5:
            self._velocity_readings.pop(0)
        self._last_pnl_sample = (now_ts, current_pnl)

        if not self._velocity_readings:
            return

        avg_velocity = sum(self._velocity_readings) / len(self._velocity_readings)
        if avg_velocity > MOMENTUM_RECOVERY_THRESHOLD:
            new_state = "IMPROVING"
        elif avg_velocity < -MOMENTUM_RECOVERY_THRESHOLD:
            new_state = "DROPPING"
        else:
            new_state = "STALLING"

        if new_state != self._momentum_state:
            log_message(
                f"📊 [MOMENTUM] State changed: {self._momentum_state} → {new_state} "
                f"(velocity avg: {avg_velocity:+.1f} IDR/s)"
            )
        self._momentum_state = new_state

        try:
            db.save_state("momentum_state", self._momentum_state)
            db.save_state("net_pnl_velocity", str(round(avg_velocity, 2)))
        except Exception:
            pass

    def _fire_net_zero_survival(
        self,
        open_pairs_for_basket: list,
        realized_pnl: float,
        unrealized_pnl: float,
        net_total_pnl: float,
        latest_velocity: float,
        mark_exit_requested,
        positions: dict,
        modified_pairs: set,
        current_tickers: dict = None,
    ) -> None:
        """Execute the Net-Zero Survival emergency basket exit.

        Called when Net PnL (realized + unrealized) ≤ 0 AND momentum velocity
        has been below NET_ZERO_MOMENTUM_THRESHOLD for NET_ZERO_HYSTERESIS_SEC
        consecutive seconds.

        Actions:
          1. Log to system_events (persisted, survives reboot).
          2. Persist guard state to bot_state for dashboard + reboot awareness.
          3. Set SYSTEM_PAUSE: blocks new buys by latching HARVEST_ONLY.
          4. Send high-priority Telegram notification.
          5. Execute BASKET_EXIT for every open position.
          6. Defensive sweep: cancel orphan exchange orders/positions not in DB.
        """
        n = len(open_pairs_for_basket)
        log_message(
            f"🛡️ [NET-ZERO SURVIVAL] ACTIVATED — Executing emergency BASKET_EXIT "
            f"for {n} position(s). Net PnL: Rp {net_total_pnl:+,.0f} | "
            f"Velocity: {latest_velocity:+.1f} IDR/s"
        )

        # ── Persist event ─────────────────────────────────────────────────────
        event_msg = (
            f"NET_ZERO_SURVIVAL ACTIVATED | "
            f"Realized: Rp {realized_pnl:+,.0f} | "
            f"Unrealized: Rp {unrealized_pnl:+,.0f} | "
            f"Net PnL: Rp {net_total_pnl:+,.0f} | "
            f"Velocity: {latest_velocity:+.1f} IDR/s"
        )
        try:
            db.log_system_event("PORTFOLIO", "NET_ZERO_SURVIVAL", event_msg, "CRITICAL")
        except Exception:
            pass

        try:
            db.save_state("net_zero_survival_active", "true")
            db.save_state("net_zero_survival_net_pnl", str(round(net_total_pnl, 2)))
            db.save_state("net_zero_survival_realized", str(round(realized_pnl, 2)))
            db.save_state("net_zero_survival_unrealized", str(round(unrealized_pnl, 2)))
            db.save_state("net_zero_survival_ts", datetime.now().isoformat())
        except Exception:
            pass

        # ── SYSTEM_PAUSE: latch HARVEST_ONLY to block all new buys ─────────────
        try:
            db.save_state("session_buy_enabled", "false")
            db.save_state("session_mode", "HARVEST_ONLY")
            db.save_state("session_latched", "true")
        except Exception:
            pass

        # ── Telegram emergency notification ───────────────────────────────────
        telegram_msg = (
            f"🛡️ *NET-ZERO SURVIVAL ACTIVATED*\n"
            f"⚠️ Emergency Basket Exit executed to protect principal.\n"
            f"Realized: Rp {realized_pnl:+,.0f}\n"
            f"Unrealized: Rp {unrealized_pnl:+,.0f}\n"
            f"Net PnL: Rp {net_total_pnl:+,.0f}\n"
            f"Momentum: {latest_velocity:+.1f} IDR/s\n"
            f"⏸️ System Paused. Manual review required."
        )
        log_message(telegram_msg.replace('*', '').replace('\n', ' | '))
        send_telegram_message(telegram_msg)

        # ── Reset HWM and execute BASKET_EXIT ────────────────────────────────
        self._portfolio_hwm = 0.0
        self._was_above_safety_zone = False
        self._peak_net_pnl_session = 0.0
        self._target_fired_session.discard("TARGET_REACHED")
        try:
            db.save_state("target_protection_active", "false")
            db.save_state("peak_net_pnl_session", "0")
        except Exception:
            pass

        for pair, data, current_price, amount in open_pairs_for_basket:
            reason = "NET_ZERO_SURVIVAL"
            log_msg = f"🛡️ [NET-ZERO SURVIVAL] {pair}: Emergency exit at Rp {current_price:,.0f}."
            log_message(log_msg)
            try:
                db.log_system_event(pair, "NET_ZERO_SURVIVAL", log_msg, "CRITICAL")
            except Exception:
                pass

            order_id = self.execute_exit(
                pair, amount, current_price, reason,
                entry_price=data.get('entry_price', 0),
                amount_idr=data.get('amount_idr', 0),
                position_data=data,
            )
            if order_id:
                mark_exit_requested(pair, order_id)

        # Defensive sweep: close any orphan exchange positions not tracked in local DB.
        if current_tickers is not None:
            self._defensive_close_exchange_positions(current_tickers, reason="NET_ZERO_SURVIVAL")

        self.save_positions(positions, modified_pairs)

    def _order_id_variants(self, order_id):
        """Generate comparable variants for heterogeneous order IDs.

        Examples:
        - 16309610
        - opidr-limit-16309610
        - buy-16309610
        """
        raw = str(order_id or '').strip()
        if not raw:
            return set()

        variants = {raw}
        if '-' in raw:
            variants.add(raw.split('-')[-1])

        tail_num = re.search(r"(\d+)$", raw)
        if tail_num:
            variants.add(tail_num.group(1))

        return {v for v in variants if v}

    def _order_id_match(self, a, b):
        return bool(self._order_id_variants(a) & self._order_id_variants(b))

    def monitor_and_execute(self, current_tickers, price_history=None, get_prediction_fn=None):
        # Prevent concurrent execution from the background thread and any other caller.
        if not self._monitor_lock.acquire(blocking=False):
            return  # Previous call is still running; skip this tick.
        try:
            self._monitor_and_execute_inner(current_tickers, price_history, get_prediction_fn)
        finally:
            self._monitor_lock.release()

    def _monitor_and_execute_inner(self, current_tickers, price_history=None, get_prediction_fn=None):
        all_positions = self.get_open_positions()
        current_dr_val = 1 if self.dry_run else 0
        
        # Filter: Hanya pantau posisi yang sesuai dengan mode saat ini
        positions = {p: d for p, d in all_positions.items() if int(d.get('dry_run', 0)) == current_dr_val}
        
        modified_pairs = set()

        # --- [ACTIVE DISCOVERY] ---
        # [Senior Fix] Jalankan Discovery di LUAR cek 'if not positions'
        # Agar koin yang ada di exchange tetap terdeteksi meskipun DB kosong.
        if not self.dry_run and (time.time() - getattr(self, '_last_discovery_ts', 0) > 60):
            try:
                bal = self.exchange.fetch_balance()
                total_bal = bal.get('total', {})
                tickers = self.oracle.market_ws.get_tickers() if self.oracle else {}
                
                for coin, amount in total_bal.items():
                    if coin == 'IDR' or coin == 'info': continue
                    amount = float(amount)
                    if amount <= 0: continue
                    
                    pair = f"{coin.lower()}idr"
                    if pair not in all_positions:
                        price = float(tickers.get(pair, {}).get('last', 0))
                        log_message(f"🔍 [DISCOVERY] Adopting asset: {coin} ({amount})")
                        recovery_data = {
                            "pair": pair,
                            "entry_price": price if price > 0 else 0,
                            "amount_crypto": amount,
                            "amount_idr": amount * price if price > 0 else 0,
                            "status": "OPEN",
                            "entry_order_id": f"RECOVERED-{int(time.time())}",
                            "exit_order_id": None,
                            "timestamp": datetime.now().isoformat(),
                            "dry_run": False,
                            "reason_backup": "DISCOVERY"
                        }
                        db.upsert_position(pair, recovery_data)
                        all_positions[pair] = recovery_data
                
                self._last_discovery_ts = time.time()
                # Update positions list after discovery
                positions = {p: d for p, d in all_positions.items() if int(d.get('dry_run', 0)) == current_dr_val}
            except Exception as e:
                log_message(f"⚠️ Discovery Error: {e}")

        if not positions:
            return

        modified_pairs = set()

        # --- [AUTO-RECONCILIATION] ---
        # Selalu sinkronkan dengan Indodax: Hapus posisi di DB jika koin sudah tidak ada di exchange.
        # Dijalankan setiap 5 menit agar tidak membebani API rate limit.
        if not self.dry_run and (time.time() - self.last_balance_sync > 300):
            try:
                bal = self.exchange.fetch_balance()
                total_balances = bal.get('total', {})
                
                ghosts_removed = []
                for pair in list(positions.keys()):
                    coin = pair[:-3].upper() # Ambil simbol koin (e.g., 'AXL' dari 'axlidr')
                    amount_on_exchange = float(total_balances.get(coin, 0) or 0)
                    
                    # Jika saldo di exchange sangat kecil (dust) atau nol, anggap sudah terjual
                    if amount_on_exchange < 1e-8:
                        log_message(f"👻 [AUTO-RECONCILE] Removing ghost position {pair} (Balance on exchange: {amount_on_exchange})")
                        db.delete_position(pair)
                        positions.pop(pair, None)
                        ghosts_removed.append(pair)
                
                if ghosts_removed:
                    log_message(f"✅ Auto-reconcile complete. Cleaned: {ghosts_removed}")
                
                self.last_balance_sync = time.time()
            except Exception as e:
                log_message(f"⚠️ Auto-reconcile failed: {e}")

        def mark_exit_requested(target_pair, order_id):
            """Sinkronkan state exit untuk DRY RUN vs LIVE agar tidak ada posisi ghost."""
            if not order_id:
                return

            if self.dry_run:
                # execute_exit() di dry run langsung delete+catat history,
                # jadi jangan upsert ulang posisi pada akhir loop.
                positions.pop(target_pair, None)
                modified_pairs.discard(target_pair)
                return

            if target_pair in positions:
                positions[target_pair]["status"] = "EXITING"
                positions[target_pair]["exit_order_id"] = order_id
                positions[target_pair]["exit_requested_at"] = datetime.now().isoformat()
                modified_pairs.add(target_pair)
        
        # --- [MANUAL DASHBOARD EXIT] ---
        manual_exit_pair = db.get_state("trigger_exit_pair")
        if manual_exit_pair:
            db.save_state("trigger_exit_pair", "") # Reset immediately
            if manual_exit_pair in positions and positions[manual_exit_pair].get('status') == "OPEN":
                log_message(f"🚨 [MANUAL EXIT] Triggered from dashboard for {manual_exit_pair}")
                data = positions[manual_exit_pair]
                # Ambil harga terakhir dari ticker terbaru
                price = float(current_tickers[manual_exit_pair].get('last') or current_tickers[manual_exit_pair].get('buy') or 0)
                if price > 0:
                    order_id = self.execute_exit(
                        manual_exit_pair, data.get('amount_crypto', 0), price, "MANUAL_DASHBOARD_EXIT",
                        entry_price=data.get('entry_price', 0), amount_idr=data.get('amount_idr', 0),
                        position_data=data
                    )
                    if order_id:
                        mark_exit_requested(manual_exit_pair, order_id)
                        self.save_positions(positions, modified_pairs)
                        return # Exit early to process manual order


        # --- [PORTFOLIO BASKET EXIT: GUARANTEED PROFIT HARVESTER] ---
        # Automatic exits are gated by a strict net profit check so that every logged
        # BASKET_EXIT results in a real net gain on the portfolio.
        #   • All triggers require total_net_pnl > 0 (guaranteed profit after fees).
        #   • Exception: BTC crash kill switch (> BTC_CRASH_TRIGGER_PCT in 5 min) bypasses the gate.
        #   • Manual EXIT button handles cut-loss scenarios instead.
        STAGNANT_RESCUE_MINUTES = 20  # Ambang waktu mandek untuk trigger rescue basket

        # ── BTC Price Tracking (for 5-minute crash detection) ────────────────────────────
        btc_ticker_data = current_tickers.get('btcidr') or {}
        _btc_raw = btc_ticker_data.get('last') or btc_ticker_data.get('buy')
        btc_current = float(_btc_raw) if _btc_raw not in (None, '', 0, '0') else 0.0
        _now_ts = time.time()
        if btc_current > 0:
            self._btc_price_history.append((_now_ts, btc_current))
            # Trim buffer: keep only entries needed for the longest window used (5 min + buffer)
            self._btc_price_history = [
                (ts, p) for ts, p in self._btc_price_history if _now_ts - ts <= 360
            ]

        # Detect BTC drop > BTC_CRASH_TRIGGER_PCT in the last 5 minutes (kill switch)
        btc_crash_kill = False
        btc_drop_5min_pct = 0.0
        if btc_current > 0 and len(self._btc_price_history) >= 2:
            old_btc = [p for ts, p in self._btc_price_history if ts <= _now_ts - 300]
            if old_btc:
                btc_5min_ago = old_btc[-1]
                btc_drop_5min_pct = (btc_current - btc_5min_ago) / btc_5min_ago if btc_5min_ago > 0 else 0.0
                btc_crash_kill = btc_drop_5min_pct < -BTC_CRASH_TRIGGER_PCT

        # BTC momentum weakening: current price <= price from 1 min ago (or below short average)
        btc_weakening = False
        if self._btc_price_history and btc_current > 0:
            btc_1min_ago = [p for ts, p in self._btc_price_history if ts <= _now_ts - 60]
            if btc_1min_ago:
                btc_weakening = btc_current <= btc_1min_ago[-1]
            else:
                # Not enough 1-min history yet: compare to short buffer average
                all_btc_prices = [p for _, p in self._btc_price_history]
                btc_weakening = btc_current <= (sum(all_btc_prices) / len(all_btc_prices))

        # Read HARVEST_ONLY flag once — used in basket evaluation AND per-position TP tightening.
        harvest_only = (db.get_state("session_mode") == "HARVEST_ONLY")

        try:
            open_pairs_for_basket = []
            total_net_pnl    = 0.0
            total_gross_pnl  = 0.0
            total_fees_idr   = 0.0
            total_amount_idr = 0.0
            has_losers  = False
            has_stagnant = False

            for pair, data in positions.items():
                if data.get('status') != "OPEN":
                    continue
                # Resolve current price: prefer live ticker, fall back to last known
                # price history entry, then entry_price (conservative: no gain assumed).
                # This ensures ALL active positions contribute to the net PnL calculation
                # regardless of whether the pair has a fresh WebSocket tick.
                if pair in current_tickers:
                    current_price = float(
                        current_tickers[pair].get('last')
                        or current_tickers[pair].get('buy')
                        or 0
                    )
                else:
                    # Fallback: use last price from rolling price_history parameter
                    # (price_history is passed from _monitor_and_execute_inner scope).
                    _ph = price_history.get(pair) if price_history else None
                    if _ph and len(_ph) > 0:
                        current_price = float(list(_ph)[-1])
                    else:
                        current_price = float(data.get('entry_price') or 0)

                if current_price <= 0:
                    current_price = float(data.get('entry_price') or 0)

                entry_price = data.get('entry_price', 0)
                amount = data.get('amount_crypto', 0)
                amount_idr = data.get('amount_idr', 0)

                sell_value = amount * current_price
                fee_sell = sell_value * FEE_SELL_TAKER
                fee_buy = amount_idr * FEE_BUY_TAKER

                gross_pnl = (current_price - entry_price) * amount
                net_pnl_idr = gross_pnl - (fee_buy + fee_sell)

                if net_pnl_idr < 0:
                    has_losers = True

                # Deteksi posisi stagnant: mandek lama & masih merugi
                try:
                    _pos_ts = datetime.fromisoformat(data['timestamp'])
                    _pos_age = (datetime.now() - _pos_ts).total_seconds() / 60.0
                    if _pos_age >= STAGNANT_RESCUE_MINUTES and net_pnl_idr < 0:
                        has_stagnant = True
                except Exception:
                    pass

                total_gross_pnl  += gross_pnl
                total_fees_idr   += fee_buy + fee_sell
                total_net_pnl    += net_pnl_idr
                total_amount_idr += amount_idr
                # Only include in basket if we have a usable live price for execution
                if pair in current_tickers and current_price > 0:
                    open_pairs_for_basket.append((pair, data, current_price, amount))

            # ── High-water-mark tracking ─────────────────────────────────────────────────
            # Update HWM jika profit gabungan saat ini lebih tinggi dari sebelumnya.
            # Reset ke 0 ketika tidak ada posisi terbuka agar sesi berikutnya mulai bersih.
            if open_pairs_for_basket:
                if total_net_pnl > self._portfolio_hwm:
                    self._portfolio_hwm = total_net_pnl
            else:
                self._portfolio_hwm = 0.0
                self._was_above_safety_zone = False  # Reset when basket is empty.

            # ── PnL Velocity ──────────────────────────────────────────────────────────────
            self._update_pnl_velocity(total_net_pnl, _now_ts)

            # ── Snapshot unrealised PnL to bot_state for dashboard (throttled) ─────────
            if _now_ts - self._last_pnl_snapshot_ts >= 5.0:
                try:
                    db.save_state("net_pnl_unrealized", str(round(total_net_pnl, 2)))
                except Exception:
                    pass
                self._last_pnl_snapshot_ts = _now_ts

            if open_pairs_for_basket:
                n = len(open_pairs_for_basket)
                gross_pnl_pct = (total_gross_pnl  / total_amount_idr * 100) if total_amount_idr > 0 else 0.0
                fees_pct      = (total_fees_idr    / total_amount_idr * 100) if total_amount_idr > 0 else 0.0
                net_pnl_pct   = (total_net_pnl     / total_amount_idr * 100) if total_amount_idr > 0 else 0.0

                # ── Net-Zero Survival Guard (v3) ──────────────────────────────────────────
                # Fires BASKET_EXIT when the combined Net PnL (realized + unrealized)
                # breaches its active floor with negative momentum.
                #
                # v3 behaviour:
                #   • Before target reached: uses NET_ZERO_PNL_FLOOR_IDR as hard loss floor.
                #   • After target reached: floor ratchets upward with latest Net PnL,
                #     preserving profit and forcing unrealized-profit execution on breakdown.
                #   • Avg velocity + hysteresis remain to filter noise.
                if NET_ZERO_SURVIVAL_ENABLED:
                    try:
                        realized_pnl_today = db.get_daily_stats()['total_pnl_idr']
                        net_total_pnl = realized_pnl_today + total_net_pnl
                        # Save realized PnL snapshot for the dashboard gauge.
                        try:
                            db.save_state("net_pnl_realized", str(round(realized_pnl_today, 2)))
                        except Exception:
                            pass

                        # Resolve target-aware floor.
                        target_raw = db.get_state("daily_profit_target")
                        daily_target_idr = float(target_raw) if target_raw else float(MOMENTUM_TARGET_PNL_IDR or 0.0)
                        locked_target_raw = db.get_state("net_zero_target_floor")
                        locked_target_floor = float(locked_target_raw) if locked_target_raw else 0.0
                        active_target_floor = max(daily_target_idr, locked_target_floor)

                        peak_raw = db.get_state("peak_pnl_today")
                        peak_pnl_today = float(peak_raw) if peak_raw else 0.0
                        target_reached_once = (
                            (daily_target_idr > 0 and peak_pnl_today >= daily_target_idr)
                            or locked_target_floor > 0
                        )

                        # Ratchet target upward after target reached.
                        # Keep a breathing buffer so minor pullbacks do not instantly fire.
                        if target_reached_once and net_total_pnl > 0:
                            candidate_floor = max(
                                active_target_floor,
                                net_total_pnl - float(max(MOMENTUM_SAFETY_BUFFER_IDR, 0.0)),
                            )
                            if candidate_floor > active_target_floor:
                                active_target_floor = candidate_floor
                                try:
                                    db.save_state("net_zero_target_floor", str(round(active_target_floor, 2)))
                                    db.save_state("daily_profit_target", str(round(active_target_floor, 2)))
                                except Exception:
                                    pass
                                log_message(
                                    f"🎯 [NET-ZERO TARGET RATCHET] Floor naik ke Rp {active_target_floor:,.0f} "
                                    f"(Net PnL: Rp {net_total_pnl:,.0f})"
                                )

                        # Once profit floor is locked, switch to HARVEST_ONLY (sell-only)
                        # so the bot secures existing unrealized profit without opening new risk.
                        if target_reached_once and active_target_floor > 0:
                            _session_mode = db.get_state("session_mode") or "NORMAL"
                            _buy_enabled = (db.get_state("session_buy_enabled") or "true") == "true"
                            if _session_mode != "HARVEST_ONLY" or _buy_enabled:
                                try:
                                    db.save_state("session_buy_enabled", "false")
                                    db.save_state("session_mode", "HARVEST_ONLY")
                                    db.save_state("session_latched", "true")
                                    db.save_state("target_protection_active", "true")
                                except Exception:
                                    pass
                                log_message(
                                    f"🌾 [NET-ZERO PROFIT LOCK] HARVEST_ONLY aktif | "
                                    f"Buy blocked | Floor: Rp {active_target_floor:,.0f}"
                                )
                                send_telegram_message(
                                    f"🌾 *NET-ZERO Profit Lock*\n"
                                    f"Mode: HARVEST_ONLY (sell-only)\n"
                                    f"Target Floor: Rp {active_target_floor:,.0f}\n"
                                    f"Buy baru diblok untuk menjaga profit."
                                )

                        net_zero_floor = active_target_floor if target_reached_once else float(NET_ZERO_PNL_FLOOR_IDR)

                        # ── Auto-recovery check ───────────────────────────────────────────
                        # After the guard fires, give the session NET_ZERO_AUTO_RESET_SEC to
                        # recover.  If PnL is back above the floor when the timer expires,
                        # auto-re-enable buys so profitable sessions aren't permanently halted.
                        if self._net_zero_fired and NET_ZERO_AUTO_RESET_SEC > 0:
                            fired_at = float(db.get_state("net_zero_fired_at") or 0)
                            fired_floor = float(db.get_state("net_zero_floor_locked") or net_zero_floor)
                            if fired_at > 0 and (_now_ts - fired_at) >= NET_ZERO_AUTO_RESET_SEC:
                                if net_total_pnl > fired_floor:
                                    self._net_zero_fired = False
                                    self._net_zero_trigger_ts = 0.0
                                    try:
                                        if target_reached_once:
                                            db.save_state("session_buy_enabled", "false")
                                            db.save_state("session_mode", "HARVEST_ONLY")
                                            db.save_state("session_latched", "true")
                                        else:
                                            db.save_state("session_buy_enabled", "true")
                                            db.save_state("session_mode", "NORMAL")
                                            db.save_state("session_latched", "false")
                                        db.save_state("net_zero_survival_active", "false")
                                    except Exception:
                                        pass
                                    _recovery_msg = (
                                        f"🟢 [NET-ZERO GUARD] Auto-recovery: PnL Rp {net_total_pnl:+,.0f} "
                                        f"above floor Rp {fired_floor:,.0f} after {NET_ZERO_AUTO_RESET_SEC}s cooldown. "
                                        f"Mode: {'HARVEST_ONLY' if target_reached_once else 'NORMAL'}."
                                    )
                                    log_message(_recovery_msg)
                                    send_telegram_message(
                                        f"🟢 *NET-ZERO Auto-Recovery*\n"
                                        f"Net PnL: Rp {net_total_pnl:+,.0f} — kondisi pulih.\n"
                                        f"Mode aktif: {'HARVEST_ONLY' if target_reached_once else 'NORMAL'}."
                                    )

                        if not self._net_zero_fired:
                            # AVERAGE velocity over rolling window — filters noisy single-tick spikes.
                            avg_velocity = (
                                sum(self._velocity_readings) / len(self._velocity_readings)
                                if self._velocity_readings else 0.0
                            )
                            # LAST velocity — kept for the immediate-bypass crash detector only.
                            latest_velocity = self._velocity_readings[-1] if self._velocity_readings else 0.0

                            # Unrealised loss as fraction of total invested capital in this basket.
                            unrealized_loss_pct = (
                                total_net_pnl / total_amount_idr if total_amount_idr > 0 else 0.0
                            )

                            # Net-Zero is a pure profit-protection guard.
                            # It only arms after the daily target has been reached at least once.
                            # Before that point both the hysteresis AND the immediate bypass
                            # are completely disabled — pre-target losses are handled by
                            # individual per-position SL / DCA, not a basket nuke.
                            if target_reached_once:
                                nz_condition = (
                                    net_total_pnl <= net_zero_floor
                                    and avg_velocity < NET_ZERO_MOMENTUM_THRESHOLD
                                    and n >= NET_ZERO_MIN_POSITIONS
                                )
                                nz_immediate = (
                                    net_total_pnl <= net_zero_floor
                                    and latest_velocity < NET_ZERO_IMMEDIATE_VELOCITY
                                    and n >= NET_ZERO_MIN_POSITIONS
                                )
                            else:
                                nz_condition = False
                                nz_immediate = False
                                if self._net_zero_trigger_ts != 0.0:
                                    self._net_zero_trigger_ts = 0.0
                                if not self._net_zero_pretarget_logged:
                                    self._net_zero_pretarget_logged = True
                                    log_message(
                                        f"🔕 [NET-ZERO GUARD] PRE-TARGET: guard disarmed. "
                                        f"Aktif setelah target harian tercapai. "
                                        f"Floor (post-target): Rp {daily_target_idr:,.0f}"
                                    )

                            if nz_condition:
                                # Stamp the timer on first detection.
                                if self._net_zero_trigger_ts == 0.0:
                                    self._net_zero_trigger_ts = _now_ts
                                    log_message(
                                        f"⚠️ [NET-ZERO GUARD] Condition detected — hysteresis timer started. "
                                        f"Net PnL (R+U): Rp {net_total_pnl:+,.0f} | "
                                        f"Floor: Rp {net_zero_floor:,.0f} | "
                                        f"Avg velocity: {avg_velocity:+.1f} IDR/s | "
                                        f"Mode: PROFIT_PROTECT | "
                                        f"Unrealised: {unrealized_loss_pct*100:.2f}%"
                                    )
                                if nz_immediate or _now_ts - self._net_zero_trigger_ts >= NET_ZERO_HYSTERESIS_SEC:
                                    if nz_immediate:
                                        log_message(
                                            f"🚨 [NET-ZERO GUARD] IMMEDIATE exit — extreme crash velocity "
                                            f"{latest_velocity:+.1f} IDR/s bypasses hysteresis."
                                        )
                                    self._net_zero_fired = True
                                    try:
                                        db.save_state("net_zero_fired_at", str(_now_ts))
                                        db.save_state("net_zero_floor_locked", str(net_zero_floor))
                                    except Exception:
                                        pass
                                    self._fire_net_zero_survival(
                                        open_pairs_for_basket=open_pairs_for_basket,
                                        realized_pnl=realized_pnl_today,
                                        unrealized_pnl=total_net_pnl,
                                        net_total_pnl=net_total_pnl,
                                        latest_velocity=avg_velocity,
                                        mark_exit_requested=mark_exit_requested,
                                        positions=positions,
                                        modified_pairs=modified_pairs,
                                        current_tickers=current_tickers,
                                    )
                                    return  # basket exit submitted; exit evaluation frame
                            else:
                                # Condition no longer met — reset hysteresis timer.
                                if self._net_zero_trigger_ts != 0.0:
                                    self._net_zero_trigger_ts = 0.0
                    except Exception as _nz_err:
                        log_message(f"⚠️ [NET-ZERO GUARD] Check error: {_nz_err}")


                # Count positions moving in the same negative direction.  If the majority
                # are losing, it's High Correlation Risk — shrink the safety buffer so the
                # exit fires sooner and capital is better protected.
                _neg_count = sum(
                    1 for _, data, cp, _ in open_pairs_for_basket
                    if data.get('entry_price') is not None and cp < float(data['entry_price'])
                )
                _high_corr_risk = n > 0 and _neg_count > n / 2

                # ── Momentum Safety Zone Setup ────────────────────────────────────────────
                _momentum_enabled = MOMENTUM_TARGET_PNL_IDR > 0
                _safety_buffer = MOMENTUM_SAFETY_BUFFER_IDR
                if _high_corr_risk:
                    _safety_buffer *= (1.0 - MOMENTUM_HIGH_CORR_BUFFER_REDUCTION)
                _safety_zone_upper = MOMENTUM_TARGET_PNL_IDR + _safety_buffer

                # Track peak net PnL for the session
                if total_net_pnl > self._peak_net_pnl_session:
                    self._peak_net_pnl_session = total_net_pnl
                    try:
                        db.save_state("peak_net_pnl_session", str(round(self._peak_net_pnl_session, 2)))
                    except Exception:
                        pass

                # Latch: once PnL climbs above the safety zone, protect it on any decline.
                if _momentum_enabled and total_net_pnl >= _safety_zone_upper:
                    # Fire "Target Reached" exactly once per session (guarded by _target_fired_session)
                    if "TARGET_REACHED" not in self._target_fired_session:
                        self._target_fired_session.add("TARGET_REACHED")
                        _tr_msg = (
                            f"🎯 *TARGET REACHED* 🎯\n"
                            f"Net PnL: Rp {total_net_pnl:,.0f} ≥ "
                            f"Safety Zone Rp {_safety_zone_upper:,.0f}\n"
                            f"Floor Protection: ACTIVE"
                        )
                        log_message(_tr_msg.replace('*', '').replace('\n', ' | '))
                        send_telegram_message(_tr_msg)
                        try:
                            db.save_state("target_protection_active", "true")
                        except Exception:
                            pass
                    self._was_above_safety_zone = True

                # True when PnL is in the warning range [Target, Target + buffer).
                _in_safety_zone = (
                    _momentum_enabled
                    and self._was_above_safety_zone
                    and MOMENTUM_TARGET_PNL_IDR < total_net_pnl < _safety_zone_upper
                )

                # Hard floor: PnL has dropped to or below the protected target.
                _at_floor = (
                    _momentum_enabled
                    and self._was_above_safety_zone
                    and total_net_pnl <= MOMENTUM_TARGET_PNL_IDR
                )

                # Effective HWM pullback percentage — tightened inside the Safety Zone
                # when velocity is still positive (recovery in progress).
                _effective_hwm_pullback_pct = BASKET_HWM_PULLBACK_PCT
                if _in_safety_zone and self._momentum_state == "IMPROVING":
                    _effective_hwm_pullback_pct = MOMENTUM_TIGHT_TRAILING_PCT

                # ── HARVEST_ONLY status log (throttled) ───────────────────────────────────
                if harvest_only:
                    if _now_ts - self._last_basket_bypass_log_ts >= 60:
                        log_message(
                            f"🌾 [HARVEST_ONLY] Status: HARVEST_ONLY | BUY: Blocked | "
                            f"Strategy: Tightening TP positions | "
                            f"Net Unrealized: Rp {total_net_pnl:,.0f} | "
                            f"Target Floor: 0.85% (Net)"
                        )
                        self._last_basket_bypass_log_ts = _now_ts

                should_execute = False
                trigger_label  = ""
                action_label   = ""
                _BYPASS_LOG_INTERVAL = 60  # seconds between repeated bypass log messages

                # ── Exception: BTC Crash Kill Switch ─────────────────────────────────────
                if btc_crash_kill:
                    should_execute = True
                    trigger_label  = f"BTC CRASH KILL SWITCH ({btc_drop_5min_pct*100:.2f}%)"
                    action_label   = "EMERGENCY KILL SWITCH"

                # ── Net Profit Gate: disable automatic exits when portfolio is net negative ──
                elif total_net_pnl <= 0:
                    if _now_ts - self._last_basket_bypass_log_ts >= _BYPASS_LOG_INTERVAL:
                        log_message(
                            f"🔒 BASKET_EXIT Bypassed: Total Net PnL "
                            f"({net_pnl_pct:+.4f}%) is not yet profitable."
                        )
                        self._last_basket_bypass_log_ts = _now_ts

                elif harvest_only:
                    # HARVEST_ONLY: all basket triggers suppressed.
                    # Individual TPs are tightened in the per-position loop below.
                    pass

                else:
                    # ── Triggers (all gated by total_net_pnl > 0) ────────────────────────
                    _harvest_min_pct = AUTO_BASKET_HARVEST_MIN * 100  # precomputed once
                    # (a) profit gabungan melampaui minimum DAN ada posisi rugi
                    trigger_normal = n > 1 and total_net_pnl >= MIN_NET_PROFIT_IDR and has_losers
                    # (b) ada posisi stagnant, menutup semua setidaknya impas
                    trigger_stagnant_rescue = n > 1 and has_stagnant
                    # (c) IDR-based proactive harvest — combined PnL melebihi target IDR
                    trigger_harvest_idr = (
                        n >= 1
                        and BASKET_HARVEST_TARGET_IDR > 0
                        and total_net_pnl >= BASKET_HARVEST_TARGET_IDR
                    )
                    # (d) HIGH-WATER-MARK PULLBACK — combined PnL turun >X% dari peak
                    #     Uses _effective_hwm_pullback_pct which tightens inside Safety Zone.
                    trigger_hwm_pullback = (
                        n >= 1
                        and self._portfolio_hwm >= BASKET_HARVEST_TARGET_IDR * 0.5
                        and total_net_pnl < self._portfolio_hwm * (1.0 - _effective_hwm_pullback_pct)
                    )
                    # (e) Guaranteed Profit Harvest — BTC weakening AND equity has started to feel the impact
                    # Both signals must confirm together:
                    #   • btc_weakening: external warning that the market is turning against us.
                    #   • _equity_impacted: our portfolio equity has already begun to pull back from its
                    #     session High-Water-Mark, confirming the BTC move is affecting us — not just noise.
                    # If BTC dips but equity has NOT moved yet, do NOT harvest (wait for real confirmation).
                    _equity_impacted = (
                        self._portfolio_hwm > 0             # HWM must be established (≥1 tick of data)
                        and total_net_pnl < self._portfolio_hwm  # any decline from peak = equity is feeling it
                    )
                    trigger_harvest_pct = (
                        n >= 1
                        and net_pnl_pct >= _harvest_min_pct
                        and btc_weakening       # market signal: BTC turning
                        and _equity_impacted    # portfolio signal: equity already declining
                    )
                    # (f) SAFETY ZONE EXIT — PnL in safety zone with momentum stalling/dropping
                    trigger_safety_zone_exit = (
                        _in_safety_zone
                        and self._momentum_state in ("STALLING", "DROPPING")
                    )
                    # (g) EMERGENCY FLOOR — absolute hard-stop once the target has been achieved
                    trigger_emergency_floor = _at_floor

                    # ── Priority cascade for trigger selection ────────────────────────────
                    if trigger_emergency_floor:
                        should_execute = True
                        trigger_label  = (
                            f"EMERGENCY FLOOR (Target Rp{MOMENTUM_TARGET_PNL_IDR:,.0f} reached)"
                        )
                        action_label   = "EMERGENCY FLOOR EXIT"

                    elif trigger_safety_zone_exit:
                        should_execute = True
                        trigger_label  = (
                            f"SAFETY ZONE EXIT (Momentum: {self._momentum_state} | "
                            f"Corr: {'HIGH' if _high_corr_risk else 'LOW'})"
                        )
                        action_label   = "SAFETY ZONE EXIT"

                    elif _in_safety_zone and self._momentum_state == "IMPROVING":
                        # Recovery in Safety Zone: suppress triggers (a-e), allow only
                        # the tight-trailing HWM pullback so a reversal still fires.
                        if trigger_hwm_pullback:
                            should_execute = True
                            trigger_label  = (
                                f"TIGHT TRAILING STOP (Safety Zone, "
                                f"{_effective_hwm_pullback_pct*100:.0f}% from peak "
                                f"Rp{self._portfolio_hwm:,.0f})"
                            )
                            action_label   = "TIGHT TRAILING STOP"
                        else:
                            # Log that tight trailing stop is standing by.
                            if _now_ts - self._last_basket_bypass_log_ts >= _BYPASS_LOG_INTERVAL:
                                _latest_vel = self._velocity_readings[-1] if self._velocity_readings else 0.0
                                log_message(
                                    f"🎯 [TIGHT TRAILING STOP] Safety Zone + Recovery detected. "
                                    f"Net PnL: Rp {total_net_pnl:,.0f} | "
                                    f"Velocity: {_latest_vel:+.1f} IDR/s | "
                                    f"HWM Floor: Rp {self._portfolio_hwm * (1 - _effective_hwm_pullback_pct):,.0f}"
                                )
                                self._last_basket_bypass_log_ts = _now_ts

                    elif trigger_normal or trigger_stagnant_rescue or trigger_harvest_idr or trigger_hwm_pullback or trigger_harvest_pct:
                        should_execute = True
                        if trigger_hwm_pullback and not (trigger_normal or trigger_stagnant_rescue or trigger_harvest_idr or trigger_harvest_pct):
                            trigger_label = f"HWM PULLBACK (Peak Rp{self._portfolio_hwm:,.0f})"
                            action_label  = "HWM PULLBACK"
                        elif trigger_harvest_pct and not (trigger_normal or trigger_stagnant_rescue or trigger_harvest_idr):
                            trigger_label = "GUARANTEED PROFIT HARVEST"
                            action_label  = "EXECUTING HARVEST"
                        elif trigger_harvest_idr and not (trigger_normal or trigger_stagnant_rescue):
                            trigger_label = "PROACTIVE HARVEST"
                            action_label  = "EXECUTING HARVEST"
                        elif trigger_stagnant_rescue and not trigger_normal:
                            trigger_label = "STAGNANT RESCUE"
                            action_label  = "STAGNANT RESCUE"
                        else:
                            trigger_label = "PROFIT COVER"
                            action_label  = "PROFIT COVER"

                if should_execute:
                    analysis = (
                        f"BASKET_EXIT Analysis: Gross {gross_pnl_pct:+.4f}% | "
                        f"Fees -{fees_pct:.4f}% | Net {net_pnl_pct:+.4f}% | "
                        f"Action: {action_label}"
                    )
                    log_message(f"🧺 {analysis}")
                    msg = (
                        f"🧺 *PORTFOLIO BASKET EXIT [{trigger_label}]* 🧺\n"
                        f"Menutup {n} posisi serentak!\n"
                        f"Estimasi Global Net Profit: Rp {total_net_pnl:,.2f}"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)

                    # ── Supreme Commander Approval Gate ─────────────────────────────────
                    # BTC crash kill switch is an emergency — bypass the gate entirely.
                    # For all other basket exits:
                    #   • Profitable (net_pnl > 0): auto-execute without popup (profit path).
                    #   • Loss/neutral (net_pnl <= 0): show gate and wait for user approval.
                    if not btc_crash_kill:
                        if total_net_pnl > 0:
                            # AUTO-HARVEST: bypass approval gate for profitable basket exits.
                            log_message(
                                f"🚀 [AUTO-HARVEST] Profitable Basket Exit executed without confirmation. "
                                f"Net PnL: Rp {total_net_pnl:,.0f} ({net_pnl_pct:+.4f}%)"
                            )
                            # Queue a silent toast notification for the dashboard.
                            db.save_state("auto_harvest_notification", json.dumps({
                                "message": (
                                    f"Basket Exit Completed (Profit Secured) — "
                                    f"Net PnL: Rp {total_net_pnl:,.0f} ({net_pnl_pct:+.4f}%)"
                                ),
                            }))
                        else:
                            # SAFETY-GATE: protection for loss/neutral exits — require user approval.
                            # Preserve original timeout defaults: harvest-type → auto-reject;
                            # safety/rescue exits → auto-approve (consistent with prior behaviour).
                            log_message(
                                f"⚠️ [SAFETY-GATE] Protection active for neutral/loss exit. "
                                f"Awaiting user approval."
                            )
                            _is_harvest = (
                                trigger_harvest_idr or trigger_harvest_pct or trigger_hwm_pullback
                            )
                            _default_approve = not _is_harvest
                            _reason_text = (
                                f"Net PnL: Rp {total_net_pnl:,.0f} | "
                                f"Net {net_pnl_pct:+.4f}% | Trigger: {trigger_label}"
                            )
                            _pairs_desc = ", ".join(
                                p for p, _, _, _ in open_pairs_for_basket[:5]
                            ) + (f" +{n-5} more" if n > 5 else "")
                            _approved = self._request_approval(
                                action_type=f"BASKET EXIT [{trigger_label}]",
                                description=_pairs_desc,
                                reason_text=_reason_text,
                                impact_idr=total_net_pnl,
                                default_approve=_default_approve,
                            )
                            if not _approved:
                                log_message(
                                    f"🚫 [APPROVAL GATE] BASKET_EXIT [{trigger_label}] REJECTED — skipping execution"
                                )
                                return

                    # Reset HWM setelah semua posisi ditutup
                    self._portfolio_hwm = 0.0
                    self._was_above_safety_zone = False  # reset floor protection
                    self._peak_net_pnl_session = 0.0
                    self._target_fired_session.discard("TARGET_REACHED")
                    try:
                        db.save_state("target_protection_active", "false")
                        db.save_state("peak_net_pnl_session", "0")
                    except Exception:
                        pass

                    for pair, data, current_price, amount in open_pairs_for_basket:
                        reason  = "BASKET_EXIT"
                        log_msg = f"⚡ [BASKET_EXIT] {pair}: Liquidated as part of portfolio rescue."
                        log_message(log_msg)
                        db.log_system_event(pair, reason, log_msg, 'SUCCESS')

                        order_id = self.execute_exit(
                            pair, amount, current_price, reason,
                            entry_price=data.get('entry_price', 0),
                            amount_idr=data.get('amount_idr', 0),
                            position_data=data
                        )
                        if order_id:
                            mark_exit_requested(pair, order_id)

                    # Defensive sweep: close any orphan exchange positions not in the local DB.
                    self._defensive_close_exchange_positions(current_tickers, reason="BASKET_EXIT")

                    self.save_positions(positions, modified_pairs)
                    return  # Langsung exit frame evaluasi ini karena semua posisi sedang ditutup
        except Exception as e:
            log_message(f"🚨 Error in Portfolio Basket Evaluation: {e}")

        # --- [Shadow Recovery Monitoring] ---
        try:
            shadows = db.get_all_shadow_orders()
            for s in shadows:
                pair = s['pair']
                if pair not in current_tickers: continue
                
                # Load metadata
                meta_raw = db.get_state(f"shadow_meta_{pair}")
                if not meta_raw: continue
                meta = json.loads(meta_raw)
                
                current_price = float(current_tickers[pair].get('last', 0))
                target_tp = float(meta.get('target_tp', 0))
                target_sl = float(meta.get('target_sl', 0))
                
                # Check for exit
                if current_price >= target_tp:
                    # Shadow WIN
                    self.consecutive_shadow_wins += 1
                    log_message(f"🏆 [SHADOW WIN] {pair} hit TP! Streak: {self.consecutive_shadow_wins}/2")
                    db.delete_shadow_order(pair)
                    db.save_state(f"shadow_meta_{pair}", "")
                    
                    if self.consecutive_shadow_wins >= 2:
                        log_message("🔓 [SHADOW RECOVERY] 2 Consecutive Wins! Unlocking Global Pause.")
                        db.save_state("consecutive_losses", 0)
                        self.consecutive_shadow_wins = 0
                        send_telegram_message("🔓 *GLOBAL PAUSE UNLOCKED*\nPasar terdeteksi membaik via Shadow Probing (2x Win). Bot kembali beroperasi normal.")
                
                elif current_price <= target_sl:
                    # Shadow LOSS
                    log_message(f"💀 [SHADOW LOSS] {pair} hit SL. Streak reset.")
                    self.consecutive_shadow_wins = 0
                    db.delete_shadow_order(pair)
                    db.save_state(f"shadow_meta_{pair}", "")
        except Exception as e:
            log_message(f"⚠️ Shadow Monitoring Error: {e}")

        for pair, data in list(positions.items()):
            try:
                # Ambil harga terakhir dari ticker terbaru
                if pair not in current_tickers:
                    # log_message(f"Skipping {pair}: No ticker data")
                    continue

                # Jika posisi sedang EXITING, cek timeout agar profit tidak balik arah.
                if data.get('status') == "EXITING":
                    try:
                        exit_requested_at = data.get('exit_requested_at') or data.get('timestamp')
                        if isinstance(exit_requested_at, str):
                            exit_dt = datetime.fromisoformat(exit_requested_at)
                        else:
                            exit_dt = datetime.now()
                        exiting_age_sec = (datetime.now() - exit_dt).total_seconds()

                        # Biarkan order bekerja normal dulu selama 2 menit.
                        if exiting_age_sec < 120:
                            continue

                        current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                        entry_price = float(data.get('entry_price') or 0)
                        amount = float(data.get('amount_crypto') or 0)
                        if current_price <= 0 or amount <= 0:
                            continue

                        momentum_signal = 'HOLD'
                        live_tech_prob = -1.0
                        hist = []
                        if price_history and pair in price_history:
                            hist = list(price_history.get(pair, []))
                        if not hist or hist[-1] != current_price:
                            hist = (hist + [current_price])[-180:]

                        if len(hist) >= 2:
                            tech = get_technical_summary(hist)
                            depth_data = db.get_market_depth(pair)
                            if depth_data['bids'] and depth_data['asks']:
                                total_bid = sum(b['vol'] for b in depth_data['bids'])
                                total_ask = sum(a['vol'] for a in depth_data['asks'])
                                total_depth = total_bid + total_ask
                                if total_depth > 0:
                                    tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)
                            momentum_signal = derive_trade_signal(tech).get('verdict', 'HOLD')

                            if get_prediction_fn and len(hist) >= TECH_MIN_HISTORY_POINTS:
                                live_tech_prob = float(get_prediction_fn(hist, current_tickers.get(pair)))

                        # Force fallback jika terlalu lama + momentum memburuk, atau sangat lama tanpa fill.
                        is_momentum_bad = (momentum_signal == 'SELL') or (live_tech_prob >= 0 and live_tech_prob < 0.40)
                        is_underwater = entry_price > 0 and current_price < entry_price * 0.995
                        should_fallback = (exiting_age_sec >= 180 and (is_momentum_bad or is_underwater)) or exiting_age_sec >= 360

                        if not should_fallback or self.dry_run:
                            continue

                        # Pastikan order lama benar-benar dibatalkan dulu agar tidak dobel jual.
                        old_exit_order_id = data.get('exit_order_id')
                        if old_exit_order_id:
                            try:
                                symbol_cancel = f"{pair[:-3].upper()}/IDR"
                                self.exchange.cancel_order(
                                    old_exit_order_id,
                                    symbol_cancel,
                                    params={'pair': pair.replace("idr", "_idr"), 'side': 'sell'}
                                )
                            except Exception as cancel_err:
                                log_message(f"⚠️ EXITING fallback cancel skipped {pair}: {cancel_err}")

                        log_message(
                            f"⚠️ [EXIT TIMEOUT FALLBACK] {pair}: {int(exiting_age_sec)}s, "
                            f"Sig={momentum_signal}, TQ={live_tech_prob:.2f}. Switching to emergency exit."
                        )

                        new_order_id = self.execute_exit(
                            pair,
                            amount,
                            current_price,
                            "EXIT_TIMEOUT",
                            entry_price=entry_price,
                            amount_idr=data.get('amount_idr', 0),
                            position_data=data
                        )
                        if new_order_id:
                            mark_exit_requested(pair, new_order_id)
                    except Exception as e:
                        log_message(f"⚠️ EXITING timeout check error for {pair}: {e}")

                    continue
                    
                current_price = float(current_tickers[pair].get('last') or current_tickers[pair].get('buy') or 0)
                entry_price = data.get('entry_price', 0)
                amount = data.get('amount_crypto', 0)

                # [Inline Momentum Engine] Samakan basis BUY/SELL: technical + orderbook.
                # Semua keputusan exit murni dari indikator teknikal — tidak ada AI bias.
                momentum_signal = 'HOLD'
                momentum_conf = 50
                current_rsi = 50.0
                live_tech_prob = -1.0

                hist = []
                if price_history and pair in price_history:
                    hist = list(price_history.get(pair, []))

                # Pastikan harga terbaru selalu masuk evaluasi momentum.
                if not hist or hist[-1] != current_price:
                    hist = (hist + [current_price])[-180:]

                if len(hist) >= 2:
                    try:
                        tech = get_technical_summary(hist)

                        depth_data = db.get_market_depth(pair)
                        if depth_data['bids'] and depth_data['asks']:
                            total_bid = sum(b['vol'] for b in depth_data['bids'])
                            total_ask = sum(a['vol'] for a in depth_data['asks'])
                            total_depth = total_bid + total_ask
                            if total_depth > 0:
                                tech['buy_pressure'] = round((total_bid / total_depth) * 100, 1)

                        tech_sig = derive_trade_signal(tech)
                        momentum_signal = tech_sig.get('verdict', 'HOLD')
                        momentum_conf = int(tech_sig.get('confidence', 50) or 50)
                        current_rsi = float(tech.get('rsi', 50) or 50)
                    except Exception as e:
                        log_message(f"⚠️ Momentum eval error for {pair}: {e}")

                    if get_prediction_fn and len(hist) >= TECH_MIN_HISTORY_POINTS:
                        try:
                            live_tech_prob = float(get_prediction_fn(hist, current_tickers.get(pair)))
                        except Exception as e:
                            log_message(f"⚠️ Tech live-prob eval error for {pair}: {e}")
                
                # Diagnostic: Log status for open positions
                # log_message(f"Monitoring {pair}: Price={current_price:,.2f}, Entry={entry_price:,.2f}, Status={data.get('status')}")
            
            # --- AUTO-CANCEL LOGIC UNTUK PENDING_BUY ---
                if data.get('status') == "PENDING_BUY":
                    if self.dry_run:
                        # Di mode simulasi, anggap selalu filled seketika (sudah dihandle OPEN di main.py, 
                        # fallback hapus aja kalau somehow nyangkut)
                        continue
    
                    order_id = data.get('entry_order_id')
                    if not order_id:
                        continue
                        
                    entry_time = datetime.fromisoformat(data['timestamp'])
                    time_elapsed = (datetime.now() - entry_time).total_seconds() / 60.0
                    
                    # Cek jarak harga: Jika harga sekarang sudah lebih tinggi X% dari harga antri kita
                    spread_pct = (current_price - entry_price) / entry_price
                    
                    is_timeout = time_elapsed > PENDING_BUY_TIMEOUT_MINUTES
                    is_too_far = spread_pct > PENDING_BUY_MAX_SPREAD_PCT
                    
                    if is_timeout or is_too_far:
                        reason = "Timeout (3m+)" if is_timeout else f"Harga menjauh (+{spread_pct*100:.2f}%)"
                        try:
                            # [Senior Fix] Standard CCXT Symbol for Validation
                            symbol_cancel = f"{pair[:-3].upper()}/IDR"
                            self.exchange.cancel_order(order_id, symbol_cancel, params={'pair': pair.replace("idr", "_idr"), 'side': 'buy'})
                            # Hapus lokal sebagai fallback jika PrivateWS miss event
                            db.delete_position(pair)
                            
                            msg = f"⚠️ *AUTO-CANCEL BUY* ⚠️\nPair: `{pair}`\nAlasan: {reason}\nHarga Antri: Rp {entry_price:,.2f}"
                            print(msg.replace('*', '').replace('\n', ' | '))
                            send_telegram_message(msg)
                        except Exception as e:
                            error_msg = str(e).lower()
                            print(f"Failed to auto-cancel {pair}: {e}")
                            if "order not found" in error_msg or "not found" in error_msg:
                                # Jika order tidak ada di bursa (sudah dicancel manual / filled saat WS mati), 
                                # hapus dari DB agar tidak stuck selamanya.
                                print(f"⚠️ {pair} order {order_id} not found on exchange. Cleaning up DB...")
                                db.delete_position(pair)
                        
                        # Jangan teruskan eksekusi TP/SL untuk pesanan yang masih pending
                    continue
                # --- END AUTO-CANCEL ---
    
                # Base TP calculation from percentage
                dynamic_tp_pct = data.get('target_tp_pct') if data.get('target_tp_pct') is not None else TP_PCT

                # HARVEST_ONLY: tighten TP gradually toward SMART_BREAK_EVEN_PCT (0.85%) as day-end approaches.
                # Formula: floor + (original - floor) * time_remaining_fraction
                # time_remaining_frac: 1.0 at midnight (full day ahead) → 0.0 at end of day.
                # This lowers the TP trigger so the market reaches it more easily, without ever
                # dropping below the 0.85% Net Profit Floor (covering round-trip fee + slippage).
                if harvest_only:
                    _now = datetime.now()
                    _seconds_since_midnight = _now.hour * 3600 + _now.minute * 60 + _now.second
                    # Fraction of day REMAINING (1.0 = day just started, 0.0 = end of day)
                    _time_remaining_frac = max(0.0, 1.0 - _seconds_since_midnight / 86400)
                    dynamic_tp_pct = max(
                        SMART_BREAK_EVEN_PCT,
                        SMART_BREAK_EVEN_PCT + (dynamic_tp_pct - SMART_BREAK_EVEN_PCT) * _time_remaining_frac,
                    )

                base_tp_price = entry_price * (1 + dynamic_tp_pct)

                # Rumus TP: (Target_IDR + Entry_Value * (1 + Buy_Fee)) / (Amount * (1 - Sell_Fee - Slippage))
                # [Fee Fix] TP exits menggunakan taker → gunakan FEE_BUY_TAKER + FEE_SELL_TAKER
                from config import FEE_SLIPPAGE_BUFFER
                tp_slippage = FEE_SLIPPAGE_BUFFER * 0.5
                denom_tp = amount * max(0.01, (1 - FEE_SELL_TAKER - tp_slippage))
                # [SMART_BREAK_EVEN] Minimum net profit agar trade masuk zona REAL_PROFIT (> 0.85% gross).
                # 0.85% = 0.6322% round-trip taker fee + 0.21% slippage buffer.
                amount_idr_pos = float(data.get('amount_idr', 0))
                dynamic_min_net = max(300.0, min(MIN_NET_PROFIT_IDR, amount_idr_pos * SMART_BREAK_EVEN_PCT))
                exact_tp_price = (dynamic_min_net + (entry_price * amount * (1 + FEE_BUY_TAKER))) / denom_tp

                # Target TP minimal agar tidak rugi fee
                tp_price = max(base_tp_price, exact_tp_price)
                
                # Default Initial Stop Loss
                dynamic_sl_pct = data.get('target_sl_pct') if data.get('target_sl_pct') is not None else SL_PCT
                initial_sl_price = entry_price * (1 + dynamic_sl_pct)
                
                current_profit_pct = (current_price - entry_price) / entry_price
                pnl_pct = current_profit_pct * 100
                
                # Hitung umur posisi
                pos_ts = datetime.fromisoformat(data['timestamp'])
                pos_age_min = (datetime.now() - pos_ts).total_seconds() / 60.0
                duration_str = f"{int(pos_age_min)}m"

                active_sl_price = initial_sl_price
    
                # --- [Aggressive] Rapid Take Profit (Scalp Focus) ---
                # Menggunakan kalkulasi Net PnL IDR yang sudah memotong semua fee (Taker Exit)
                try:
                    sell_value_est = amount * current_price
                    fee_buy_est = data.get('amount_idr', 0) * FEE_BUY_TAKER
                    # [Senior Scaling Fix] Karena kita sekarang menggunakan Market Order (Taker) untuk TP,
                    # estimasi fee harus menggunakan Taker Rate (0.72%) agar profit tetap aman.
                    fee_sell_est = sell_value_est * FEE_SELL_TAKER
                    net_pnl_idr = (current_price - entry_price) * amount - (fee_buy_est + fee_sell_est)

                    # [Senior Global Strategy] Multi-Tiered Take Profit berbasis teknikal murni.
                    # Tier 1: Small Profit (Ride the Trend)
                    # Tier 2: Good Profit (Fast Exit on any weakness — dikonfirmasi multi-indicator)
                    # Tier 3: High Profit (FORCE EXIT NOW - Lock it in!)

                    is_tier2_good_profit = net_pnl_idr >= 7500 or current_profit_pct >= 0.015
                    is_tier3_high_profit = net_pnl_idr >= 15000 or current_profit_pct >= 0.045

                    # Logika Tier 3: FORCE EXIT (Profit sangat bagus, amankan)
                    if is_tier3_high_profit:
                        reason_tp = "FORCE_PROFIT_HIGH_TIER"
                        log_message(f"💎 [GOD-MODE EXIT] {pair}: High Profit {current_profit_pct*100:.2f}% secured instantly!")
                        order_id = self.execute_exit(pair, amount, current_price, reason_tp,
                                                   entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                        if order_id: mark_exit_requested(pair, order_id)
                        continue

                    # Logika Tier 2: FAST EXIT — hanya jika ada konfirmasi MULTI-INDIKATOR melemah.
                    # Tidak lagi bergantung pada AI prob saja; butuh technical sell signal.
                    is_momentum_sell = momentum_signal == 'SELL'

                    # Tambahkan konfirmasi MACD dan Stochastic RSI untuk exit
                    is_tech_weak = False
                    try:
                        if len(hist) >= 35:
                            _, _, _mh = calculate_macd(hist)
                            _sk, _sd = calculate_stochastic_rsi(hist)
                            # Lemah jika MACD histogram negatif DAN Stoch overbought
                            is_tech_weak = (_mh < 0 and _sk > 65) or (_sk > 80 and current_rsi > 65)
                    except Exception:
                        pass

                    if is_tier2_good_profit and (is_momentum_sell or is_tech_weak or current_rsi > 75):
                        reason_tp = "RAPID_TP_GOOD_PROFIT"
                        log_message(
                            f"⚡ [GOOD-PROFIT EXIT] {pair}: Profit {current_profit_pct*100:.2f}% "
                            f"secured on tech weakness (rsi={current_rsi:.0f}, tech_weak={is_tech_weak})."
                        )
                        order_id = self.execute_exit(pair, amount, current_price, reason_tp,
                                                   entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                        if order_id: mark_exit_requested(pair, order_id)
                        continue

                    # Logika Tier 1: NORMAL SCALP (Ikuti Trailing Stop / TP target standar)
                except Exception as e:
                    log_message(f"⚠️ Error in Global Tiered TP for {pair}: {e}")
                    continue
    
                # --- [AI-Free] Emergency exit signals are handled by Tier 2 technical exit
                # (momentum SELL + tech weakness) and Tier 3 force-exit above. Any
                # remaining position continues to hard SL / manual exit below.

                # 2. Take Profit (Hard TP)
                if current_price >= tp_price:
                    msg = (
                        f"🎯 *TAKE PROFIT* 🎯\n"
                        f"Pair: `{pair}`\n"
                        f"Entry: Rp {entry_price:,.2f}\n"
                        f"Exit: Rp {current_price:,.2f}\n"
                        f"PnL: {pnl_pct:+.2f}%\n"
                        f"Hold: {duration_str}"
                    )
                    print(msg.replace('*', '').replace('\n', ' | '))
                    send_telegram_message(msg)
                    
                    order_id = self.execute_exit(pair, amount, current_price, "TAKE_PROFIT",
                                      entry_price=entry_price, amount_idr=data.get('amount_idr', 0), position_data=data)
                    if order_id:
                        mark_exit_requested(pair, order_id)
    
                # 3. [Senior Strategy] Averaging Down (DCA) or Stop Loss
                # [Fix DCA Bug] Periksa DCA secara INDEPENDEN (sebelum SL), pada level DCA_STEP_PCT (-2.5%).
                # Sebelumnya DCA diperiksa di dalam blok SL (-1%), sehingga kondisi -2.5% tidak pernah terpenuhi.
                current_tier = data.get('dca_tier', 0)
                can_dca = (
                    DCA_ENABLED and
                    current_tier < DCA_MAX_TIERS and
                    current_profit_pct <= DCA_STEP_PCT and
                    current_price > active_sl_price and  # Belum kena hard SL
                    live_tech_prob >= DCA_MIN_TECH_CONFIDENCE  # Probabilitas teknikal cukup kuat
                )

                if can_dca:
                    reason_dca = f"STRATEGIC_DCA_TIER_{current_tier+1}"
                    log_msg = (
                        f"⚓ [AVERAGING DOWN] {pair}: Menambah muatan di {current_profit_pct*100:.2f}% "
                        f"(Tier {current_tier+1}/{DCA_MAX_TIERS}, TQ: {live_tech_prob:.2f})"
                    )
                    log_message(log_msg)
                    db.log_system_event(pair, "DCA", log_msg, 'INFO')

                    success = self.execute_averaging_down(pair, data, current_price)
                    if success:
                        # Update dca_tier dan biarkan posisi tetap terbuka
                        positions[pair]['dca_tier'] = current_tier + 1
                        modified_pairs.add(pair)
                        continue

                elif current_price <= active_sl_price:
                    # [Manual Control Mode] Automatic STOP_LOSS is DISABLED.
                    # Posisi rugi harus ditutup manual via dashboard EXIT button.
                    log_message(
                        f"🚫 [AUTO-SL DISABLED] {pair}: Price {current_price:,.2f} below SL {active_sl_price:,.2f} "
                        f"({pnl_pct:+.2f}%). Holding — use dashboard EXIT button to close manually."
                    )
                    continue
            except Exception as e:
                log_message(f"🚨 Error in monitor loop: {e}")
                import traceback
                log_message(traceback.format_exc())

        self.save_positions(positions, modified_pairs)

    def _defensive_close_exchange_positions(self, current_tickers: dict, reason: str = "BASKET_EXIT") -> None:
        """Defensive sweep: close any open exchange positions/orders not tracked in the local DB.

        Called as a second pass after the DB-tracked BASKET_EXIT loop so that orphan
        positions (e.g. opened via the exchange UI, or created before a crash that wiped
        the DB) are not left dangling on the exchange.

        Steps:
          1. Cancel all open BUY orders on the exchange (prevents new positions accumulating).
          2. Fetch exchange balance and sell any coin with a meaningful holding that has no
             corresponding OPEN record in the local DB.
        """
        if self.dry_run:
            return  # No real exchange in dry-run mode

        try:
            # ── 1. Cancel open BUY limit orders ──────────────────────────────────
            try:
                open_orders = self.exchange.fetch_open_orders()
                for order in open_orders or []:
                    if (order.get('side') or '').lower() == 'buy':
                        try:
                            symbol = order.get('symbol') or ''
                            pair = symbol.replace('/', '').lower()
                            self.exchange.cancel_order(
                                order['id'],
                                symbol,
                                params={'pair': pair.replace('idr', '_idr'), 'side': 'buy'},
                            )
                            log_message(
                                f"🛡️ [DEFENSIVE_CLOSE] Cancelled open BUY order {order['id']} for {symbol}"
                            )
                        except Exception as _ce:
                            log_message(f"⚠️ [DEFENSIVE_CLOSE] Cancel order error: {_ce}")
            except Exception as _oe:
                log_message(f"⚠️ [DEFENSIVE_CLOSE] fetch_open_orders error: {_oe}")

            # ── 2. Sell orphan coin balances ─────────────────────────────────────
            try:
                bal = self.exchange.fetch_balance()
                total_bal = bal.get('total', {})
                # Collect pairs already being closed via the DB-tracked path
                tracked_open_pairs = set()
                for pair, data in self.get_open_positions().items():
                    if data.get('status') == 'OPEN':
                        tracked_open_pairs.add(pair)

                for coin, amount in total_bal.items():
                    if coin in ('IDR', 'info'):
                        continue
                    amount = float(amount or 0)
                    if amount <= 0:
                        continue
                    pair = f"{coin.lower()}idr"
                    if pair in tracked_open_pairs:
                        continue  # Already handled by the DB-tracked basket exit loop
                    # Estimate value in IDR to filter out dust
                    price = float(
                        (current_tickers.get(pair) or {}).get('last')
                        or (current_tickers.get(pair) or {}).get('buy')
                        or 0
                    )
                    if price <= 0:
                        continue
                    idr_value = amount * price
                    if idr_value < 10000:
                        continue  # Skip dust (< Rp 10.000)
                    try:
                        symbol = f"{coin.upper()}/IDR"
                        self.exchange.create_order(
                            symbol, 'market', 'sell', amount, price,
                            {'pair': pair.replace('idr', '_idr')}
                        )
                        log_message(
                            f"🛡️ [DEFENSIVE_CLOSE] Sold orphan {coin} ({amount}) "
                            f"≈ Rp {idr_value:,.0f} via {reason}"
                        )
                    except Exception as _se:
                        log_message(f"⚠️ [DEFENSIVE_CLOSE] Sell orphan {coin} error: {_se}")
            except Exception as _be:
                log_message(f"⚠️ [DEFENSIVE_CLOSE] fetch_balance error: {_be}")
        except Exception as _e:
            log_message(f"⚠️ [DEFENSIVE_CLOSE] Unexpected error: {_e}")

    def _request_approval(self, action_type, description, reason_text, impact_idr, default_approve=True):
        """Supreme Commander Approval Gate — blocks up to 30 s waiting for user response.

        Writes a pending approval record to the DB (picked up by the dashboard via
        push_data_loop and forwarded to the frontend as a 'pending_action' Socket.IO event).
        Polls the DB for the user's decision every 0.5 s. On timeout, applies the
        supplied default (AUTO-APPROVE for SL-type exits, AUTO-REJECT for harvest exits).

        Returns True if approved, False if rejected.
        """
        import uuid
        action_id = str(uuid.uuid4())[:8]

        payload = {
            "action_id": action_id,
            "action_type": action_type,
            "description": description,
            "reason": reason_text,
            "impact_idr": impact_idr,
            "default_approve": default_approve,
            "status": "PENDING",
            "expires_at": time.time() + 10,
            "created_at": time.time(),
        }
        db.save_state("pending_approval", json.dumps(payload))

        log_msg = (
            f"⏸️ [APPROVAL GATE] User Approval Requested for {action_type} | "
            f"Waiting max 10s... (default: {'APPROVE' if default_approve else 'REJECT'})"
        )
        log_message(log_msg)
        db.log_system_event(description, "APPROVAL_PENDING", log_msg, 'WARNING')

        deadline = time.time() + 10
        while time.time() < deadline:
            time.sleep(0.5)
            raw = db.get_state("pending_approval")
            if not raw:
                break
            try:
                current = json.loads(raw)
            except Exception:
                break
            if current.get("action_id") != action_id:
                break  # A newer action replaced ours
            status = current.get("status", "PENDING")
            if status == "APPROVED":
                log_msg = (
                    f"✅ [APPROVAL GATE] User Approval Requested for {action_type} | Response: Approved"
                )
                log_message(log_msg)
                db.log_system_event(description, "APPROVAL_APPROVED", log_msg, 'SUCCESS')
                db.save_state("pending_approval", "")
                return True
            elif status == "REJECTED":
                log_msg = (
                    f"🚫 [APPROVAL GATE] User Approval Requested for {action_type} | Response: Rejected"
                )
                log_message(log_msg)
                db.log_system_event(description, "APPROVAL_REJECTED", log_msg, 'WARNING')
                db.save_state("pending_approval", "")
                return False

        # Timeout — apply the configured default
        action_taken = "AUTO-APPROVED" if default_approve else "AUTO-REJECTED"
        log_msg = (
            f"⏰ [APPROVAL GATE] User Approval Requested for {action_type} | "
            f"Response: Timeout → {action_taken}"
        )
        log_message(log_msg)
        db.log_system_event(description, f"APPROVAL_{action_taken.replace('-', '_')}", log_msg, 'WARNING')
        db.save_state("pending_approval", "")
        return default_approve

    def execute_exit(self, pair, amount, price, reason, entry_price=None, amount_idr=0, position_data=None):
        try:
            # Tentukan apakah ini exit darurat (harus instant taker) atau target profit (maker)
            is_emergency = reason in ("STOP_LOSS", "HARD_STOP_LOSS", "EXIT_TIMEOUT", "BASKET_EXIT", "MANUAL_DASHBOARD_EXIT")
            # TP exits menggunakan market sell untuk eksekusi instan
            is_tp_exit = reason in ("RAPID_TP_SECURE", "RAPID_TP_MOMENTUM", "STAGNANT_EXIT", "TAKE_PROFIT", "FORCE_PROFIT_HIGH_TIER", "RAPID_TP_GOOD_PROFIT", "STAGNANT_PROFIT_EXIT")
            symbol = f"{pair[:-3].upper()}/IDR"

            if is_emergency or is_tp_exit:
                # [Senior Scaling Fix] Gunakan Market Order untuk kecepatan maksimal
                # TP dan SL darurat tidak boleh antre, harus hajar BID pembeli sekarang juga.
                exit_price = price * (1 - 0.001) # Buffer tipis untuk estimasi
                fee_sell_rate = FEE_SELL_TAKER
            else:
                # Maker mode (masih digunakan untuk beberapa strategi jaring/limit)
                exit_price = price * (1 + SELL_MAKER_OFFSET)
                fee_sell_rate = FEE_SELL_MAKER

            # [Senior Scaling Fix] PIGGYBACK: Ambil seluruh saldo koin yang tersedia
            coin = pair.replace('idr', '').upper()
            try:
                if not self.dry_run:
                    balance = self.exchange.fetch_balance()
                    real_balance = float(balance['free'].get(coin, 0))
                    if real_balance > (amount * 0.9): # Jika ada saldo koin nyata
                        amount = real_balance
            except Exception: pass

            # Use position's original mode if available, fallback to current global mode
            use_dry_run = self.dry_run
            if position_data and 'dry_run' in position_data:
                use_dry_run = bool(position_data['dry_run'])

            if use_dry_run:
                order = {
                    "id": f"DRY-EXIT-{int(time.time())}",
                    "status": "closed",
                    "price": exit_price,
                    "amount": amount,
                    "symbol": symbol
                }
            else:
                params = {'pair': pair.replace("idr", "_idr")}
                if is_emergency or is_tp_exit:
                    # [Final Fix] Use explicit create_order for market sell
                    order = self.exchange.create_order(symbol, 'market', 'sell', amount, price, params)
                    # Harga eksekusi asli mungkin beda, kita pakai estimasi market untuk pencatatan awal
                    exit_price = order.get('price') or price
                else:
                    params['post_only'] = True
                    # [Final Fix] Use explicit create_order for limit sell
                    order = self.exchange.create_order(symbol, 'limit', 'sell', amount, exit_price, params)

            # Audit log
            db.log_order(pair, "SELL", exit_price, amount, order.get('status'), order)

            order_status = order.get('status', 'closed')
            order_id = order.get('id')
            
            # Tentukan fee jual berdasarkan strategi exit
            exec_type = "TAKER" if (is_emergency or is_tp_exit) else "MAKER"
            fee_sell_rate = FEE_SELL_TAKER if (is_emergency or is_tp_exit) else FEE_SELL_MAKER
            
            # Entry fee sekarang Taker (karena bot hajar kanan/market buy)
            fee_buy = amount_idr * FEE_BUY_TAKER
            
            # Gunakan exit_price (harga limit order) bukan price (ticker mentah) untuk akurasi PnL
            sell_value = amount * exit_price
            fee_sell = sell_value * fee_sell_rate
            gross_pnl = (exit_price - entry_price) * amount
            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)

            # Catat ke system_events agar muncul di Activity Feed dashboard
            pnl_label = f"+Rp {net_pnl_idr:,.0f}" if net_pnl_idr >= 0 else f"-Rp {abs(net_pnl_idr):,.0f}"
            db.log_system_event(
                pair,
                "SELL",
                f"{reason} | Rp {exit_price:,.0f} | PnL:{pnl_label}",
                'SUCCESS' if net_pnl_idr >= 0 else 'WARN'
            )

            if order_status in ['closed', 'canceled', 'done', 'fill'] or self.dry_run:
                db.delete_position(pair)
                
                pnl_idr = net_pnl_idr
                pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                
                # Use provided position_data for timestamp, fallback to now
                timestamp_str = position_data.get('timestamp') if position_data else datetime.now().isoformat()
                entry_time = datetime.fromisoformat(timestamp_str)
                duration_sec = (datetime.now() - entry_time).total_seconds()
                
                try:
                    db.insert_trade_history(
                        pair=pair,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        amount_crypto=amount,
                        amount_idr=amount_idr,
                        pnl_idr=pnl_idr,
                        pnl_pct=pnl_pct,
                        reason=reason,
                        exec_type=exec_type,
                        duration_seconds=int(duration_sec),
                        dry_run=use_dry_run,
                        exit_order_id=order_id,
                        ai_confidence=position_data.get('ai_confidence', 0.0)
                    )
                    
                    # Update Consecutive Losses state
                    current_cl = int(db.get_state("consecutive_losses", 0))
                    if pnl_idr > 0:
                        db.save_state("consecutive_losses", 0)
                    else:
                        db.save_state("consecutive_losses", current_cl + 1)
                        self._set_pair_cooldown(pair, minutes=20, reason=reason)

                    # ── Project Evolution: Learning Loop ─────────────────────────────
                    db.update_trade_outcome(pair, pnl_idr)
                    if self.oracle:
                        self.oracle.learn_from_trade(pair, pnl_idr)

                except Exception as db_err:
                    print(f"Failed to save trade history/state for {pair}: {db_err}")
            else:
                # Simpan metadata ke position agar PrivateWS (handle_order_update) tahu reason-nya
                # [Senior Fix] Update position status AND save the backup reason
                db.update_position_status(pair, "EXITING", exit_order_id=order_id)
                # Tambahkan field extra ke pos di memori/DB
                if position_data:
                    position_data['status'] = "EXITING"
                    position_data['exit_order_id'] = order_id
                    position_data['exit_requested_at'] = datetime.now().isoformat()
                    position_data['reason_backup'] = reason
                    db.upsert_position(pair, position_data)
            
            return order.get('id')
        except Exception as e:
            err_msg = f"Critical error in execute_exit for {pair}: {e}"
            print(err_msg)
            db.log_system_event(pair, "EXIT_ERROR", err_msg, 'ERROR')
            return None
                
    def execute_averaging_down(self, pair, data, current_price):
        """
        [Senior Strategy] Menambah muatan (DCA) pada posisi yang sedang drawdown.
        Menghitung ulang harga rata-rata dan memperbarui state di DB.
        """
        from config import CAPITAL_PER_TRADE_PCT, INITIAL_CAPITAL, DCA_MULTIPLIER, BUY_MAKER_OFFSET, MIN_TRADE_IDR
        
        try:
            old_entry_price = float(data['entry_price'])
            old_amount_crypto = float(data['amount_crypto'])
            old_amount_idr = float(data['amount_idr'])
            
            # 1. Tentukan nominal penambahan muatan
            # Default: Tambahkan nominal yang sama dengan muatan sebelumnya
            add_amount_idr = old_amount_idr * DCA_MULTIPLIER
            
            # [Senior Fix] Guard: Pastikan nominal DCA memenuhi minimum order exchange
            if add_amount_idr < MIN_TRADE_IDR:
                log_message(
                    f"⚠️ [DCA SKIP] {pair}: Nominal DCA Rp {add_amount_idr:,.0f} "
                    f"di bawah minimum Rp {MIN_TRADE_IDR:,.0f}. Batalkan DCA."
                )
                return False
            
            # [Senior Fix] Konversi nominal IDR → jumlah koin SEBELUM kirim ke exchange.
            # Indodax create_order market buy membutuhkan quantity koin (amount_crypto),
            # bukan nilai IDR. Mengirim IDR langsung menyebabkan 'Minimum order X COIN' error.
            add_amount_crypto = add_amount_idr / current_price
            
            symbol = f"{pair[:-3].upper()}/IDR"
            
            if self.dry_run:
                # Simulasi penambahan muatan
                log_message(
                    f"🧪 [DRY RUN DCA] {pair}: Added {add_amount_idr:,.0f} IDR "
                    f"= {add_amount_crypto:.8f} koin at Rp {current_price:,.2f}"
                )
            else:
                # Eksekusi Limit Buy (Maker) untuk hemat fee: 0.1% vs 0.51% taker
                # Pasang sedikit di bawah harga pasar agar antri sebagai maker
                limit_price = current_price * (1 - BUY_MAKER_OFFSET)
                params = {'pair': pair.replace("idr", "_idr"), 'post_only': True}
                order = self.exchange.create_order(
                    symbol, 'limit', 'buy', add_amount_crypto, limit_price, params
                )
                # Gunakan nilai aktual dari order response jika tersedia
                exec_price = float(order.get('price') or current_price)
                executed_qty = float(order.get('amount') or order.get('executedQty') or 0)
                if executed_qty > 0:
                    add_amount_crypto = executed_qty
                # Rekonstruksi actual IDR spent berdasarkan harga & qty eksekusi asli
                add_amount_idr = add_amount_crypto * exec_price
                current_price = exec_price  # Update to real execution price

            # 2. Hitung Harga Rata-Rata Baru (Weighted Average)
            new_total_amount_idr = old_amount_idr + add_amount_idr
            new_total_amount_crypto = old_amount_crypto + add_amount_crypto
            
            # Rumus: (Total Modal / Total Koin)
            new_avg_entry_price = new_total_amount_idr / new_total_amount_crypto
            
            # 3. Update Database
            new_tier = data.get('dca_tier', 0) + 1
            db.update_position_after_dca(
                pair, 
                new_avg_entry_price, 
                new_total_amount_crypto, 
                new_total_amount_idr, 
                new_tier
            )
            
            msg = (
                f"⚓ *DCA SUCCESS* ⚓\n"
                f"Pair: `{pair}` (Tier {new_tier})\n"
                f"Old Entry: Rp {old_entry_price:,.2f}\n"
                f"DCA Price: Rp {current_price:,.2f}\n"
                f"New Average: Rp {new_avg_entry_price:,.2f} 🔥\n"
                f"Total Load: Rp {new_total_amount_idr:,.0f} ({new_total_amount_crypto:.8f} koin)"
            )
            send_telegram_message(msg)
            return True
            
        except Exception as e:
            log_message(f"🚨 Critical error in execute_averaging_down for {pair}: {e}")
            return False

    def handle_order_update(self, order):
        """
        Callback dari PrivateWS. Memproses order yang terisi (Filled).
        """
        try:
            # [Senior Fix] Support both CCXT format and Native Indodax WS format
            order_id = str(
                order.get('id')
                or order.get('orderId')
                or order.get('order_id')
                or order.get('clientOrderId')
                or ''
            )
            pair = str(order.get('pair') or order.get('symbol') or '').lower().replace('/', '')
            side = str(order.get('side') or '').upper()
            status = str(order.get('status') or '').lower()

            if not order_id:
                return

            # Simpan jejak event order dari PrivateWS untuk audit desync.
            try:
                db.log_order(
                    pair or 'unknown',
                    side or 'UNKNOWN',
                    float(order.get('price') or 0),
                    float(order.get('executedQty') or order.get('origQty') or order.get('amount') or 0),
                    status.upper() if status else 'UNKNOWN',
                    response=order,
                )
            except Exception:
                pass

            done_statuses = {'filled', 'closed', 'done'}
            fill_statuses = {'fill'}
            cancel_statuses = {'canceled', 'cancelled', 'rejected'}

            # Status 'fill' pada PWS bisa partial fill. Final dianggap selesai bila status done/closed/filled
            # atau unfilledQty sudah nol.
            unfilled_qty = float(order.get('unfilledQty') or 0)
            executed_qty = float(order.get('executedQty') or order.get('amount') or 0)
            is_terminal_fill = (status in done_statuses) or (status in fill_statuses and unfilled_qty <= 0)
            
            # [Senior Reactive Sync] Trigger balance update on any significant order change
            if not self.dry_run and self.bot and (status in done_statuses or status in fill_statuses or status in cancel_statuses):
                # log_message(f"🔄 Re-syncing balance due to order event: {status}")
                self.bot._sync_balances_to_db()

            # Event NEW tidak mengubah state lokal.
            if status not in done_statuses and status not in fill_statuses and status not in cancel_statuses:
                return
            
            # 1. Cek apakah ini Jaring yang terisi
            for p, jaring in list(self.jaring_orders.items()):
                if self._order_id_match(jaring.get('id'), order_id):
                    log_message(f"🎯 JARING FILLED! {p} bought at Rp {jaring['price']:,.2f} via Liquidity Grab.")
                    
                    # Pindahkan dari Shadow ke Positions
                    data = {
                        "entry_price": float(order.get('price', jaring['price'])),
                        "amount":      float(order.get('executedQty', jaring['amount'])),
                        "amount_crypto": float(order.get('executedQty', jaring['amount'])),
                        "amount_idr":  float(order.get('price', jaring['price'])) * float(order.get('executedQty', jaring['amount'])),
                        "timestamp":   datetime.now().isoformat(),
                        "dry_run":     self.dry_run,
                        "status":      "OPEN",
                        "entry_order_id": order_id,
                        "exit_order_id": None,
                        "reason_backup": "LIQUIDITY_GRAB"
                    }
                    db.upsert_position(p, data)
                    db.delete_shadow_order(p)
                    if p in self.jaring_orders: del self.jaring_orders[p]
                    
                    send_telegram_message(f"🎯 *JARING FILLED* 🎯\nPair: `{p}`\nPrice: Rp {jaring['price']:,.2f}")
                    return

            # 2. Cek apakah ini Buy Order reguler yang baru terisi
            positions = db.get_open_positions()
            for p_pair, p_data in positions.items():
                if self._order_id_match(p_data.get('entry_order_id'), order_id) and p_data.get('status') == "PENDING_BUY":
                    if status in cancel_statuses:
                        log_message(f"🧹 BUY {status.upper()} {p_pair} ({order_id}). Deleting stale PENDING_BUY.")
                        db.delete_position(p_pair)
                        return

                    # [Senior Fix] Capture actual fill price and amount for accurate basis
                    fill_price = float(order.get('price', p_data.get('entry_price')))
                    fill_amount = executed_qty if executed_qty > 0 else float(p_data.get('amount_crypto', 0))

                    if is_terminal_fill:
                        log_message(f"✅ BUY FILLED! {p_pair} order {order_id} is now OPEN.")
                        p_data['status'] = "OPEN"
                        p_data['entry_price'] = fill_price
                        p_data['amount_crypto'] = fill_amount
                        p_data['amount_idr'] = fill_price * fill_amount
                        db.upsert_position(p_pair, p_data)
                        send_telegram_message(f"✅ *BUY FILLED* ✅\nPair: `{p_pair}`\nPrice: Rp {fill_price:,.2f}\nStatus: `OPEN`")
                    else:
                        # Partial buy fill: pertahankan PENDING_BUY sampai benar-benar selesai.
                        p_data['entry_price'] = fill_price
                        p_data['amount_crypto'] = max(fill_amount, float(p_data.get('amount_crypto', 0)))
                        p_data['amount_idr'] = fill_price * p_data['amount_crypto']
                        db.upsert_position(p_pair, p_data)
                        log_message(
                            f"⏳ BUY PARTIAL {p_pair} ({order_id}): exec={fill_amount:.8f}, unfilled={unfilled_qty:.8f}"
                        )
                    return

            # 3. Cek apakah ini Sell Order (Take Profit/Stop Loss) yang terisi
            for p_pair, p_data in positions.items():
                if self._order_id_match(p_data.get('exit_order_id'), order_id) and p_data.get('status') == "EXITING":
                    if status in cancel_statuses:
                        # Exit dibatalkan/ditolak: balikkan ke OPEN agar dievaluasi ulang oleh monitor loop.
                        p_data['status'] = 'OPEN'
                        p_data['exit_order_id'] = None
                        db.upsert_position(p_pair, p_data)
                        log_message(f"↩️ EXIT {status.upper()} {p_pair} ({order_id}). Reverting position to OPEN.")
                        return

                    if not is_terminal_fill:
                        # Partial sell fill: simpan status EXITING, tunggu event DONE agar histori tidak double.
                        log_message(
                            f"⏳ SELL PARTIAL {p_pair} ({order_id}): exec={executed_qty:.8f}, unfilled={unfilled_qty:.8f}. Waiting DONE."
                        )
                        return

                    log_message(f"💰 SELL FILLED! {p_pair} order {order_id} is now CLOSED.")
                    
                    try:
                        exit_price = float(order.get('price', 0))
                        # Fallback
                        if exit_price == 0: exit_price = float(p_data.get('entry_price', 0)) * (1 + p_data.get('target_tp_pct', TP_PCT))
                        
                        amount_crypto = executed_qty if executed_qty > 0 else float(p_data.get('amount_crypto', 0))
                        entry_price = float(p_data.get('entry_price', 0))
                        amount_idr = float(p_data.get('amount_idr', 0))
                        
                        # --- [Senior Fix] Real Fee Extraction dari fillInformation ---
                        fill_info = order.get('fillInformation', {})
                        participant = fill_info.get('participant', 'MAKER') # Default to MAKER
                        
                        # Ambil fee, tax, clearing aktual jika ada (sudah dalam IDR)
                        real_fee = float(fill_info.get('fee', 0))
                        real_tax = float(fill_info.get('tax', 0))
                        real_clearing = float(fill_info.get('clearing', 0))
                        
                        sell_value = amount_crypto * exit_price
                        
                        # Fallback jika fill_info tidak lengkap (misal legacy WS)
                        if real_fee == 0:
                            fee_rate = FEE_SELL_TAKER if participant == 'TAKER' else FEE_SELL_MAKER
                            fee_sell = sell_value * fee_rate
                        else:
                            # Gunakan total biaya aktual
                            fee_sell = real_fee + real_tax + real_clearing
                        
                        # Entry fee (selalu maker kecuali koin nyangkut/orphans yang diadopsi)
                        fee_buy = amount_idr * FEE_BUY_MAKER
                        
                        gross_pnl = (exit_price - entry_price) * amount_crypto
                        net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                        pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                        
                        # Reason determination
                        reason = p_data.get('reason_backup', "TAKE_PROFIT" if net_pnl_idr > 0 else "STOP_LOSS")
                        ws_trade_id = str(order.get('tradeId', '') or '') or None
                        
                        timestamp_str = p_data.get('timestamp') or datetime.now().isoformat()
                        duration_sec = (datetime.now() - datetime.fromisoformat(timestamp_str)).total_seconds()
                        
                        db.insert_trade_history(
                            pair=p_pair, entry_price=entry_price, exit_price=exit_price,
                            amount_crypto=amount_crypto, amount_idr=amount_idr,
                            pnl_idr=net_pnl_idr, pnl_pct=pnl_pct, reason=reason,
                            duration_seconds=int(duration_sec), dry_run=p_data.get('dry_run'),
                            trade_id=ws_trade_id,
                            exit_order_id=order_id,
                            ai_confidence=p_data.get('ai_confidence', 0.0)
                        )
                        
                        current_cl = int(db.get_state("consecutive_losses", 0))
                        if net_pnl_idr > 0:
                            db.save_state("consecutive_losses", 0)
                        else:
                            db.save_state("consecutive_losses", current_cl + 1)
                            self._set_pair_cooldown(p_pair, minutes=20, reason=reason)
                        
                        db.update_trade_outcome(p_pair, net_pnl_idr)
                        if self.oracle: self.oracle.learn_from_trade(p_pair, net_pnl_idr)
                        
                        db.delete_position(p_pair)
                        send_telegram_message(f"💰 *EXIT FILLED* 💰\nPair: `{p_pair}`\nReason: `{reason}`\nPnL: Rp {net_pnl_idr:,.2f}")

                        # [Manual Exit Post-Processing] Re-evaluate session status with post-exit PnL
                        if reason == "MANUAL_DASHBOARD_EXIT" and self.oracle:
                            try:
                                updated_status = self.oracle.get_daily_target_status()
                                session_info = self.oracle.evaluate_and_update_session_mode(updated_status)
                                log_message(
                                    f"🔄 [Manual Exit] Post-exit session re-eval: "
                                    f"mode={session_info.get('session_mode', 'NORMAL')} | "
                                    f"PnL Rp {updated_status.get('current_pnl', 0):,.0f} | "
                                    f"Peak Rp {updated_status.get('peak_pnl_today', 0):,.0f}"
                                )
                                # Flag for dashboard: all positions cleared — session reset is available
                                current_dr_val = 1 if self.dry_run else 0
                                remaining = {
                                    p: d for p, d in db.get_open_positions().items()
                                    if int(d.get('dry_run', 0)) == current_dr_val
                                }
                                if not remaining:
                                    db.save_state("session_reset_available", "true")
                                    log_message("📭 [Manual Exit] All positions cleared. Session reset available.")
                            except Exception as ex:
                                log_message(f"⚠️ [Manual Exit] Post-exit session re-eval error: {ex}")
                    except Exception as ex:
                        log_message(f"Error calculating final PnL for {p_pair}: {ex}")
                    return
            
        except Exception as e:
            print(f"Error handling order update: {e}")

    def _fetch_v2_trades(self, pair, startTime=None, endTime=None):
        """Precision manual fetch for Indodax v2 (Strict parameter ordering)."""
        try:
            import hmac, hashlib, requests, urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            api_key = self.exchange.apiKey
            api_secret = self.exchange.secret
            if not api_key or not api_secret: return []

            symbol = pair.lower().replace("/", "")
            timestamp = int(time.time() * 1000)
            
            # [Senior Audit] Strict ordering as per v2 documentation example
            params = [
                ("symbol", symbol),
                ("limit", 1000)
            ]
            if startTime: params.append(("startTime", int(startTime)))
            if endTime: params.append(("endTime", int(endTime)))
            params.extend([
                ("timestamp", timestamp),
                ("recvWindow", 60000)
            ])
            
            query_str = "&".join([f"{k}={v}" for k, v in params])
            sign = hmac.new(api_secret.encode(), query_str.encode(), hashlib.sha512).hexdigest()
            
            headers = {
                "X-APIKEY": api_key,
                "Sign": sign,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            url = f"https://tapi.indodax.com/api/v2/myTrades?{query_str}"
            resp = requests.get(url, headers=headers, timeout=15, verify=True)
            
            if resp.status_code != 200:
                if resp.status_code == 403:
                    logger.error(f"⚠️ 403 Forbidden for {pair}: Possible API Key restriction or IP block.")
                    # log first 500 chars to see if there's a specific message
                    logger.error(f"Response snippet: {resp.text[:500]}")
                return []
                
            data = resp.json()
            if data.get("code") or data.get("error"):
                if data.get("code") != 1110:
                    logger.error(f"V2 API Error for {pair}: {data.get('code')} - {data.get('error')}")
                return []
            
            return data.get('data', [])
        except Exception as e:
            logger.error(f"V2 Manual Fetch Error for {pair}: {e}")
            return []

    def _fetch_v2_order_histories(self, pair, startTime=None, endTime=None, limit=200):
        """Fetch order history v2 untuk validasi status order (FILLED/CANCELLED/REJECTED)."""
        try:
            import hmac, hashlib, requests

            api_key = self.exchange.apiKey
            api_secret = self.exchange.secret
            if not api_key or not api_secret:
                return []

            symbol = pair.lower().replace('/', '')
            timestamp = int(time.time() * 1000)

            params = [
                ("symbol", symbol),
                ("limit", int(limit)),
                ("sort", "desc"),
            ]
            if startTime:
                params.append(("startTime", int(startTime)))
            if endTime:
                params.append(("endTime", int(endTime)))
            params.extend([
                ("timestamp", timestamp),
                ("recvWindow", 60000),
            ])

            query_str = "&".join([f"{k}={v}" for k, v in params])
            sign = hmac.new(api_secret.encode(), query_str.encode(), hashlib.sha512).hexdigest()

            headers = {
                "X-APIKEY": api_key,
                "Sign": sign,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            url = f"https://tapi.indodax.com/api/v2/order/histories?{query_str}"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return []

            data = resp.json()
            if data.get("code") or data.get("error"):
                return []
            return data.get("data", [])
        except Exception as e:
            logger.error(f"V2 order history fetch error for {pair}: {e}")
            return []

    def sync_all_history(self):
        """
        [Senior Quant Audit] Full Account Deep Sync.
        Iterates through all pairs and performs windowed history fetching (7-day chunks)
        to overcome Indodax API limitations.
        """
        log_message("🔄 Starting Deep Account Sync (30-Day History)...")
        try:
            markets = self.exchange.fetch_markets()
            idr_pairs = [m['id'] for m in markets if m['quoteId'] == 'idr' and m['active']]
            total_pairs = len(idr_pairs)
            total_new_trades = 0
            
            # [Fix] Gunakan tanggal dinamis: 30 hari ke belakang
            start_dt = datetime.now() - timedelta(days=30)
            START_TARGET_MS = int(start_dt.timestamp() * 1000)
            NOW_MS = int(time.time() * 1000)
            
            log_message(f"🔄 Range: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} to Now")
            
            for idx, pair_id in enumerate(idr_pairs):
                # Rate limit protection
                time.sleep(0.2)
                
                if idx % 10 == 0:
                    log_message(f"🔄 Sync Progress: {idx}/{total_pairs} pairs scanned...")
                
                # Hanya satu request karena rentang < 7 hari
                trades = self._fetch_v2_trades(pair_id, startTime=START_TARGET_MS, endTime=NOW_MS)
                
                if not trades: continue

                # Urutkan Ascending
                trades.sort(key=lambda x: x.get('time', 0))
                
                last_buy_price = 0
                last_buy_qty = 0
                
                for t in trades:
                    try:
                        trade_id = str(t.get('tradeId'))
                        price = float(t.get('price', 0))
                        qty = float(t.get('qty', 0))
                        is_buyer = t.get('isBuyer')
                        
                        if is_buyer:
                            last_buy_price = price
                            last_buy_qty = qty
                            continue 
                        
                        # Jika ini SELL, hitung estimasi PnL
                        exit_price = price
                        entry_price = last_buy_price if last_buy_price > 0 else exit_price
                        
                        amount_crypto = qty
                        amount_idr = amount_crypto * entry_price
                        
                        # Fee Estimation (0.11%)
                        fee_buy = amount_idr * 0.0011 
                        fee_sell = (qty * exit_price) * 0.0011 
                        
                        gross_pnl = (exit_price - entry_price) * amount_crypto
                        net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                        pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0
                        
                        # Format timestamp
                        raw_time = t.get('time', 0)
                        dt_obj = datetime.fromtimestamp(raw_time / 1000.0)
                        created_at_str = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
                        
                        db.insert_trade_history(
                            pair=pair_id, 
                            entry_price=entry_price, 
                            exit_price=exit_price,
                            amount_crypto=amount_crypto, 
                            amount_idr=amount_idr,
                            pnl_idr=net_pnl_idr, 
                            pnl_pct=pnl_pct, 
                            reason="MANUAL_SYNC",
                            exec_type="MAKER" if t.get('isMaker') else "TAKER",
                            duration_seconds=3600, 
                            dry_run=False,
                            trade_id=trade_id,
                            is_manual=1,
                            created_at=created_at_str
                        )
                        total_new_trades += 1
                        if total_new_trades % 5 == 0:
                            log_message(f"✅ Found {total_new_trades} trades so far...")
                    except Exception as e:
                        logger.error(f"Error inserting sync trade: {e}")
                        continue

            log_message(f"✅ Deep Sync Complete! Added {total_new_trades} historical trades since 26/04.")
            db.save_state("trigger_sync_all", "0")
            return True

        except Exception as e:
            log_message(f"❌ Deep Sync Failed: {e}")
            db.save_state("trigger_sync_all", "0")
            return False

    def reconcile_with_exchange(self):
        """
        [Anti-Hallucination] 
        Sinkronisasi posisi di Database dengan saldo asli di bursa.
        """
        try:
            log_message("Reality Check: Reconciling positions with Indodax balance...")
            if self.dry_run: return # Dry run tidak punya saldo asli untuk di-cek
            
            balance = self.exchange.fetch_balance()
            open_positions = db.get_open_positions()
            
            for pair, pos in open_positions.items():
                coin = pair[:-3].upper()  # Semua pair format '{coin}idr'; slice -3 hapus suffix 'idr'
                amount_on_exchange = float(balance['total'].get(coin, 0))
                
                # [Fix] Promosikan PENDING_BUY ke OPEN jika saldo sudah ada di bursa
                if pos.get('status') == "PENDING_BUY":
                    if amount_on_exchange >= (pos['amount_crypto'] * 0.9):
                        log_message(f"🔄 Reality Sync: {pair} was PENDING_BUY but balance exists. Promoting to OPEN and syncing amount.")
                        pos['status'] = "OPEN"
                        pos['amount_crypto'] = amount_on_exchange
                        db.upsert_position(pair, pos)
                        continue
                    else:
                        # Order belum tersentuh/fill, jadi wajar saldonya 0. Skip cek hallucination.
                        continue

                # [Fix] Selalu sync amount dengan bursa jika ada perbedaan tipis (fee/rounding)
                if abs(amount_on_exchange - pos['amount_crypto']) > 0 and amount_on_exchange > 0:
                    if 0.8 < (amount_on_exchange / pos['amount_crypto']) < 1.2:
                        # log_message(f"⚖️ Amount Sync: {pair} amount adjusted {pos['amount_crypto']} -> {amount_on_exchange}")
                        pos['amount_crypto'] = amount_on_exchange
                        db.upsert_position(pair, pos)
                # Gunakan amount_crypto yang konsisten dengan schema DB
                target_amount = pos.get('amount_crypto') or pos.get('amount', 0)
                if amount_on_exchange < (target_amount * 0.8): # Toleransi lebih longgar 20%
                    log_message(f"🚨 Reality Desync Detected! {pair} not found on exchange. Checking history v2...")
                    
                    # Cek apakah ini sebenarnya sudah laku (terjual saat bot offline)
                    trades_v2 = self._fetch_v2_trades(pair)
                    found_fill = False
                    
                    for t in trades_v2:
                        # [Fix] Hanya cocokkan berdasarkan exit_order_id yang spesifik
                        # Jangan gunakan `or status == 'EXITING'` karena itu terlalu longgar
                        # dan bisa salah menggunakan BUY trade untuk menutup posisi SELL
                        order_id_match = pos.get('exit_order_id') and self._order_id_match(t.get('orderId'), pos.get('exit_order_id'))
                        # Fallback: jika EXITING dan ada trade SELL setelah timestamp entry posisi
                        entry_ts_ms = 0
                        try:
                            from datetime import datetime as _dt
                            entry_ts_ms = int(_dt.fromisoformat(str(pos['timestamp'])).timestamp() * 1000)
                        except Exception:
                            pass
                        trade_is_sell = not t.get('isBuyer', True)
                        trade_after_entry = int(t.get('time', 0)) > entry_ts_ms
                        exiting_sell_match = (pos.get('status') == 'EXITING' and trade_is_sell and trade_after_entry)

                        if order_id_match or exiting_sell_match:
                            log_message(f"✅ Recovered missing trade for {pair} from v2 history! Recording PnL...")
                            
                            # Rekonstruksi data trade untuk DB
                            exit_price = float(t.get('price', 0))
                            real_fee = float(t.get('commission', 0))
                            is_maker = t.get('isMaker', False)
                            exec_type = "MAKER" if is_maker else "TAKER"
                            
                            entry_price = float(pos.get('entry_price', 0))
                            amount_crypto = float(t.get('qty', pos.get('amount_crypto', 0)))
                            amount_idr = float(pos.get('amount_idr', 0))
                            
                            # Hitung PnL dengan fee aktual v2
                            fee_buy = amount_idr * FEE_BUY_MAKER
                            fee_sell = real_fee # v2 commission sudah mencakup segalanya
                            
                            gross_pnl = (exit_price - entry_price) * amount_crypto
                            net_pnl_idr = gross_pnl - (fee_buy + fee_sell)
                            pnl_pct = (net_pnl_idr / amount_idr) * 100 if amount_idr > 0 else 0

                            # Metadata untuk dedup history dengan sync_all_history.
                            ws_trade_id = str(t.get('tradeId', '') or '') or None
                            reason = pos.get('reason_backup', 'RECONCILE_EXIT_FILL')
                            participant = exec_type
                            try:
                                duration_sec = int((datetime.now() - datetime.fromisoformat(str(pos.get('timestamp')))).total_seconds())
                            except Exception:
                                duration_sec = 300
                            db.insert_trade_history(
                                pair=pair, entry_price=entry_price, exit_price=exit_price,
                                amount_crypto=amount_crypto, amount_idr=amount_idr,
                                pnl_idr=net_pnl_idr, pnl_pct=pnl_pct, reason=reason,
                                exec_type=participant,
                                duration_seconds=int(duration_sec), dry_run=False,
                                trade_id=ws_trade_id,
                                exit_order_id=pos.get('exit_order_id'),
                                ai_confidence=pos.get('ai_confidence', 0.0)
                            )
                            found_fill = True
                            break
                    
                    if not found_fill:
                        log_message(f"⚠️ Could not find fill for {pair} in v2 history. Attempting CCXT order fetch...")
                        # Coba ambil fill price aktual dari CCXT sebagai last resort
                        _exit_oid = pos.get('exit_order_id')
                        _entry_price = float(pos.get('entry_price', 0))
                        _amount_crypto = float(pos.get('amount_crypto', 0))
                        _amount_idr = float(pos.get('amount_idr', 0))
                        _symbol = f"{pair[:-3].upper()}/IDR"
                        _reconcile_exit_price = None
                        _reconcile_exec_type = "MAKER"
                        _reconcile_real_fee = 0
                        if _exit_oid and not _exit_oid.startswith('ADOPT-'):
                            try:
                                ccxt_order = self.exchange.fetch_order(_exit_oid, _symbol)
                                if ccxt_order and ccxt_order.get('status') in ['closed', 'filled', 'done']:
                                    _reconcile_exit_price = float(ccxt_order.get('price') or ccxt_order.get('average') or 0)
                                    _reconcile_exec_type = "TAKER" if not ccxt_order.get('postOnly') else "MAKER"
                                    log_message(f"✅ Got fill price from CCXT for {pair}: {_reconcile_exit_price}")
                            except Exception as ccxt_err:
                                log_message(f"CCXT fetch_order failed for {pair}: {ccxt_err}")
                        if not _reconcile_exit_price or _reconcile_exit_price <= 0:
                            # Fallback: gunakan harga pasar saat ini sebagai estimasi
                            try:
                                ticker = self.exchange.fetch_ticker(_symbol)
                                _reconcile_exit_price = float(ticker.get('last', _entry_price))
                            except Exception:
                                _reconcile_exit_price = _entry_price
                            log_message(f"⚠️ Using estimated exit price for {pair}: {_reconcile_exit_price}")
                        _fee_rate = FEE_SELL_TAKER if _reconcile_exec_type == "TAKER" else FEE_SELL_MAKER
                        _gross = (_reconcile_exit_price - _entry_price) * _amount_crypto
                        _net = _gross - (_amount_idr * FEE_BUY_MAKER) - (_amount_crypto * _reconcile_exit_price * _fee_rate)
                        _pct = (_net / _amount_idr * 100) if _amount_idr > 0 else 0
                        db.insert_trade_history(
                            pair=pair, entry_price=_entry_price, exit_price=_reconcile_exit_price,
                            amount_crypto=_amount_crypto, amount_idr=_amount_idr,
                            pnl_idr=_net, pnl_pct=_pct,
                            reason=pos.get('reason_backup', 'RECONCILE_GHOST'),
                            exec_type=_reconcile_exec_type,
                            duration_seconds=300,
                            dry_run=False,
                            exit_order_id=_exit_oid if _exit_oid and not _exit_oid.startswith('ADOPT-') else None
                        )
                        log_message(f"📝 Recorded best-effort trade for {pair} (RECONCILE_GHOST) pnl={_net:+,.0f} IDR")
                    
                    db.delete_position(pair)
                    open_positions.pop(pair, None)  # Keep in-memory dict in sync so orphan adoption below can re-adopt this coin
                    if pair in self.jaring_orders:
                        del self.jaring_orders[pair]
                        db.delete_shadow_order(pair)
            
            # ── [Auto-Adoption] Sync orphans from wallet to DB ──
            _blacklist = set(db.get_blacklist())
            for coin, coin_amount in balance['total'].items():
                if coin == 'IDR' or coin_amount <= 0:
                    continue
                    
                pair = f"{coin.lower()}idr"
                if pair in _blacklist:
                    continue
                if pair not in open_positions:
                    try:
                        # Gunakan REST API karena ExitManager tidak punya akses ke Market WS
                        ticker_rest = self.exchange.fetch_ticker(pair.upper().replace("IDR", "/IDR"))
                        current_price = float(ticker_rest['last'])
                            
                        value_idr = coin_amount * current_price
                        
                        # Adopsi hanya jika nilainya di atas Rp 10.000 (mengabaikan dust)
                        if value_idr >= 10000:
                            log_message(f"👼 Orphan Adoption: Found {coin_amount} {coin} in wallet (Value: Rp {value_idr:,.0f}). Adopting into Dashboard!")
                            
                            adopt_data = {
                                "entry_price": current_price,
                                "amount":      coin_amount,
                                "amount_crypto": coin_amount,
                                "amount_idr":  value_idr,
                                "timestamp":   datetime.now().isoformat(),
                                "dry_run":     False,
                                "status":      "OPEN",
                                "entry_order_id": f"ADOPT-{int(time.time())}",
                                "exit_order_id": None,
                                "target_tp_pct": TP_PCT,
                                "target_sl_pct": SL_PCT,
                                "reason_backup": "ORPHAN_ADOPTION"
                            }
                            db.upsert_position(pair, adopt_data)
                            send_telegram_message(f"👼 *ORPHAN ADOPTED* 👼\nPair: `{pair}`\nQty: {coin_amount}\nEst. Value: Rp {value_idr:,.0f}")
                    except Exception as e:
                        log_message(f"Failed to adopt orphan {pair}: {e}")
            
            # ── [Active Order Sync] Sync manual Open Orders to DB ──
            try:
                open_orders_res = self.exchange.privatePostOpenOrders()
                if open_orders_res and str(open_orders_res.get('success')) == '1':
                    orders_dict = open_orders_res.get('return', {}).get('orders', {})
                    
                    # Flatten active order IDs for quick lookup
                    all_active_order_ids = set()
                    if isinstance(orders_dict, dict):
                        for pk, ol in orders_dict.items():
                            for o in ol: all_active_order_ids.add(str(o.get('order_id')))

                    # [Anti-Stuck] Detect and force-sell stagnant EXITING orders
                    for p, pos in list(open_positions.items()):
                        if pos.get('status') == "EXITING":
                            oid = str(pos.get('exit_order_id', ''))
                            if oid in all_active_order_ids:
                                # Order still open on exchange. Check duration since position opened.
                                pos_ts = datetime.fromisoformat(pos['timestamp'])
                                age_sec = (datetime.now() - pos_ts).total_seconds()
                                if age_sec > 300: # 5 minutes
                                    log_message(f"⚠️ [Stuck Exit] {p} order {oid} is stuck for {age_sec:.0f}s. Cancelling and performing Emergency Exit...")
                                    try:
                                        # Format Indodax: aih_idr, Format CCXT: AIH/IDR
                                        ccxt_symbol = p.upper().replace("IDR", "/IDR")
                                        # Indodax cancel_order requires 'type' (buy/sell) and 'pair' (with underscore)
                                        self.exchange.cancel_order(oid, ccxt_symbol, params={'pair': p.replace("idr", "_idr"), 'type': 'sell'})
                                        # Revert to OPEN so the main monitor loop can re-process it (now as Taker due to is_emergency)
                                        pos['status'] = "OPEN"
                                        pos['exit_order_id'] = None
                                        db.upsert_position(p, pos)
                                    except Exception as ce:
                                        log_message(f"Failed to cancel stuck order {oid}: {ce}")
                            elif oid:
                                # Jika order EXITING tidak aktif lagi, cek status final dari endpoint order history.
                                hist = self._fetch_v2_order_histories(p, limit=50)
                                matched = next((h for h in hist if self._order_id_match(h.get('orderId'), oid)), None)
                                if matched:
                                    final_status = str(matched.get('status') or '').upper()
                                    if final_status in ('CANCELLED', 'REJECTED'):
                                        pos['status'] = 'OPEN'
                                        pos['exit_order_id'] = None
                                        db.upsert_position(p, pos)
                                        log_message(f"↩️ Reality Sync: {p} exit order {oid} {final_status}. Position reverted to OPEN.")
                                    elif final_status == 'FILLED':
                                        log_message(f"✅ Reality Sync: {p} exit order {oid} already FILLED. Waiting trade reconcile.")

                    if isinstance(orders_dict, dict):
                        for pair_key, order_list in orders_dict.items():
                            pair = pair_key.replace('_', '')
                            pos = open_positions.get(pair)
                            
                            for order in order_list:
                                o_type = order.get('type')
                                o_id = str(order.get('order_id'))
                                o_price = float(order.get('price', 0))
                                
                                if pos is None:
                                    if o_type == 'buy':
                                        remain_idr = float(order.get('remain_idr', 0))
                                        amount_crypto_est = remain_idr / o_price if o_price > 0 else 0
                                        
                                        log_message(f"🔄 Active Order Sync: Found untracked BUY order for {pair}. Adopting as PENDING_BUY.")
                                        adopt_data = {
                                            "entry_price": o_price,
                                            "amount": amount_crypto_est,
                                            "amount_crypto": amount_crypto_est,
                                            "amount_idr": remain_idr,
                                            "timestamp": datetime.now().isoformat(),
                                            "dry_run": False,
                                            "status": "PENDING_BUY",
                                            "entry_order_id": o_id,
                                            "exit_order_id": None,
                                            "target_tp_pct": TP_PCT,
                                            "target_sl_pct": SL_PCT
                                        }
                                        db.upsert_position(pair, adopt_data)
                                        send_telegram_message(f"🔄 *SYNC PENDING BUY* 🔄\nPair: `{pair}`\nPrice: Rp {o_price:,.0f}")
                                        pos = adopt_data
                                        open_positions[pair] = adopt_data
                                        
                                    elif o_type == 'sell':
                                        coin_sym = pair.replace('idr', '')
                                        remain_coin = float(order.get(f'remain_{coin_sym}', 0))
                                        
                                        log_message(f"🔄 Active Order Sync: Found untracked SELL order for {pair}. Adopting as EXITING.")
                                        adopt_data = {
                                            "entry_price": o_price * 0.95,
                                            "amount": remain_coin,
                                            "amount_crypto": remain_coin,
                                            "amount_idr": remain_coin * o_price,
                                            "timestamp": datetime.now().isoformat(),
                                            "dry_run": False,
                                            "status": "EXITING",
                                            "entry_order_id": f"ADOPT-{int(time.time())}",
                                            "exit_order_id": o_id,
                                            "target_tp_pct": TP_PCT,
                                            "target_sl_pct": SL_PCT
                                        }
                                        db.upsert_position(pair, adopt_data)
                                        send_telegram_message(f"🔄 *SYNC EXITING SELL* 🔄\nPair: `{pair}`\nSell Price: Rp {o_price:,.0f}")
                                        pos = adopt_data
                                        open_positions[pair] = adopt_data
                                else:
                                    if o_type == 'sell' and pos.get('status') == 'OPEN':
                                        log_message(f"🔄 Active Order Sync: {pair} was OPEN but has active SELL order. Updating to EXITING.")
                                        pos['status'] = 'EXITING'
                                        pos['exit_order_id'] = o_id
                                        db.upsert_position(pair, pos)
            except Exception as e:
                log_message(f"Failed to sync active orders: {e}")
            
            log_message("Reality Check: Sync complete. No hallucinations found.")
        except Exception as e:
            log_message(f"Reality Check Failed: {e}")
            # [Fix BUG #2] Dead code dihapus — 'order' tidak terdefinisi di scope ini

    # ── Shadow Jaring (Liquidity Grab) ───────────────────────────────────

    def _sync_jaring_from_db(self):
        """Recover active jaring orders from SQLite on startup."""
        saved = db.get_all_shadow_orders()
        for o in saved:
            self.jaring_orders[o['pair']] = o
    def manage_shadow_jaring(self, ranked_pairs, current_tickers, balance=0):
        """
        Main entry for jaring logic. Called from the main loop.
        """
        try:
            # 1. Trailing: Move existing jaring up if price is too far (protect from missing pumps)
            for pair, o in list(self.jaring_orders.items()):
                if pair in current_tickers:
                    curr_price = current_tickers[pair]['last']
                    
                    # [Fix] Tambah Cooldown 5 Menit agar tidak spam cancel/replace
                    last_update = self.last_jaring_update.get(pair, 0)
                    if time.time() - last_update < 300: continue 

                    # Jika harga pasar sudah naik > 10% dari jaring kita
                    if curr_price > o['price'] * 1.10:
                        log_message(f"Jaring: Price moved up for {pair}. Trailing jaring...")
                        if not self.dry_run: self.exchange.cancel_order(o['id'])
                        db.delete_shadow_order(pair)
                        del self.jaring_orders[pair]
                        self.last_jaring_update[pair] = time.time()

            # 2. Placement: Fill empty slots (Max 3 jaring)
            if len(self.jaring_orders) < 3:
                candidates = [p for p in ranked_pairs if p['pair'] not in self.jaring_orders][:3]
                for c in candidates:
                    pair = c['pair']
                    jaring_price = c['last'] * (1 + JARING_DEPTH_PCT)
                    # Use provided balance if in live mode, fallback to INITIAL_CAPITAL for dry run
                    effective_capital = balance if not self.dry_run else INITIAL_CAPITAL
                    amount_idr = effective_capital * JARING_ALLOCATION_PCT
                    amount_crypto = amount_idr / jaring_price
                    self.place_jaring(pair, amount_crypto, jaring_price)
                    
        except Exception as e:
            print(f"Jaring Manager Error: {e}")

    def place_jaring(self, pair, amount, price):
        """Place a deep-limit buy order."""
        try:
            log_message(f"Jaring: Placing Liquidity Grab on {pair} at Rp {price:,.2f}")
            if self.dry_run:
                order_id = f"JARING-{int(time.time())}"
                order = {"id": order_id, "status": "open", "price": price, "amount": amount}
            else:
                # [Final Fix] Use explicit create_order for limit buy (Jaring)
                order = self.exchange.create_order(pair.upper().replace("IDR", "/IDR"), 'limit', 'buy', amount, price, {'post_only': True})
                order_id = order['id']
            
            self.jaring_orders[pair] = {"id": order_id, "price": price, "amount": amount}
            db.upsert_shadow_order(pair, order_id, price, amount)
            
        except Exception as e:
            print(f"Failed to place jaring for {pair}: {e}")