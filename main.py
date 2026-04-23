import os
import json
import time
import ccxt
import multiprocessing
import collections
from datetime import datetime

from config import (
    LOG_FILE, INITIAL_CAPITAL, MIN_BALANCE_THRESHOLD, MIN_TRADE_IDR, 
    DRY_RUN, RAM_PATH, MAX_TRADES_PER_DAY, DATA_PATH, EXECUTION_LOG,
    MAX_TRACKED_PAIRS, MIN_VOLUME_FILTER
)
from engines.exit_manager import ExitManager
from engines.performance_tracker import run_performance_report
from engines.predictor import get_prediction
from engines.ws_market import MarketDataWS
from engines.ws_private import PrivateWS
from engines.telegram_bot import send_telegram_message

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}"
    print(formatted_msg)

# ==========================================
# ENGINES
# ==========================================
def calculate_score(item):
    pair, d = item
    try:
        last = float(d['last'])
        buy  = float(d.get('buy',  last))
        sell = float(d.get('sell', last))
        h    = float(d['high'])
        l    = float(d['low'])
        vol  = float(d['vol_idr'])
        
        # Kedalaman Order Book
        bid_depth = float(d.get('bid_depth', 0))
        ask_depth = float(d.get('ask_depth', 0))

        spread     = (sell - buy) / sell * 100 if sell else 0
        volatility = (h - l) / l * 100 if l else 0
        
        # Kalkulasi Imbalance Ratio
        imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 0

        # Filter dasar: spread, volatility, volume minimum
        if spread < 0.5 and 0.2 <= volatility <= 8.0 and vol > MIN_VOLUME_FILTER:
            # Imbalance & bid_depth sebagai bonus score (opsional, tidak wajib ada data OB)
            imb_bonus = (imbalance * 50_000_000) if imbalance > 1.2 else 0
            depth_bonus = (bid_depth * 0.1) if bid_depth > 10_000_000 else 0
            score = (vol * 0.5) + (volatility * 0.3) - (spread * 0.2) + imb_bonus + depth_bonus
            return {
                    "pair": pair,
                    "last": last,
                    "spread": spread,
                    "volatility": volatility,
                    "score": score,
                    "imbalance": imbalance,
                    "bid_depth": bid_depth
                }
    except Exception:
        pass
    return None


class ScalperBot:
    def __init__(self):
        self.exchange = ccxt.indodax({
            'apiKey': os.getenv('INDODAX_API_KEY'),
            'secret': os.getenv('INDODAX_API_SECRET'),
            'enableRateLimit': True,
        })
        self.exit_manager = ExitManager(self.exchange, dry_run=DRY_RUN)
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.start_day = datetime.now().day
        
        # Memory-based history tracking
        self.price_history = collections.defaultdict(lambda: collections.deque(maxlen=40))

        # Directory Safety
        os.makedirs(RAM_PATH, exist_ok=True)
        os.makedirs(DATA_PATH, exist_ok=True)

        # Process Pool Optimization (Initialized once)
        self.pool = multiprocessing.Pool(6)

        # ── WebSocket Clients ───────────────────────────────────────────────
        # Market Data WS — menggantikan REST polling fetch_tickers()
        self.market_ws = MarketDataWS()

        # Private WS — menerima order update real-time
        self.private_ws = PrivateWS(
            api_key    = os.getenv('INDODAX_API_KEY', ''),
            api_secret = os.getenv('INDODAX_API_SECRET', ''),
            on_order_update = self._handle_order_update,
        )

    # ── Order Update Handler (dari Private WS) ─────────────────────────────
    def _handle_order_update(self, order: dict):
        """
        Dipanggil setiap kali ada event order_update dari Private WS.
        Status yang mungkin: NEW, FILL, DONE, CANCELLED, REJECTED
        """
        order_id = order.get("orderId", "?")
        symbol   = order.get("symbol", "?")
        status   = order.get("status", "?")
        side     = order.get("side", "?")
        price    = order.get("price", "?")

        log_message(
            f"[PWS] Order Update | {symbol} | {side} | "
            f"Status: {status} | OrderID: {order_id} | Price: {price}"
        )

        # Tentukan apakah kita perlu menghapus posisi dari tracker
        should_delete = False
        revert_to_open = False
        
        # Jika order SELL berhasil (keluar market), hapus posisi
        if side == "sell" and status in ("DONE", "FILL"):
            should_delete = True
            
        # Jika order BUY berhasil masuk market, ubah PENDING_BUY jadi OPEN
        mark_buy_open = False
        if side == "buy" and status in ("DONE", "FILL"):
            mark_buy_open = True
            
        # Jika order dibatalkan/ditolak
        if status in ("CANCELLED", "REJECTED"):
            if side == "buy":
                should_delete = True
            elif side == "sell":
                revert_to_open = True

        if should_delete or revert_to_open or mark_buy_open:
            pos_file = f"{RAM_PATH}/open_positions.json"
            if os.path.exists(pos_file):
                try:
                    with open(pos_file, "r") as f:
                        positions = json.load(f)

                    # Cocokkan berdasarkan symbol
                    pair_normalized = symbol
                    modified = False
                    for key in list(positions.keys()):
                        if key.replace("/", "").lower() == pair_normalized.lower():
                            if should_delete:
                                del positions[key]
                                log_message(f"[PWS] Position cleared: {symbol} (status={status})")
                            elif revert_to_open:
                                positions[key]["status"] = "OPEN"
                                positions[key]["exit_order_id"] = None
                                log_message(f"[PWS] Position Reverted to OPEN: {symbol} (sell {status})")
                            elif mark_buy_open:
                                positions[key]["status"] = "OPEN"
                                log_message(f"[PWS] Order Filled. Position OPEN: {symbol}")
                            modified = True
                            break

                    if modified:
                        with open(pos_file, "w") as f:
                            json.dump(positions, f, indent=4)
                except Exception as e:
                    log_message(f"[PWS] Error updating positions: {e}")

    # ── Ticker Source (WS-based) ────────────────────────────────────────────
    def get_tickers(self) -> dict | None:
        """
        Ambil snapshot ticker dari buffer MarketDataWS.
        Return None jika belum ada data (WS belum ready).
        """
        tickers = self.market_ws.get_tickers()
        return tickers if tickers else None

    def update_history_and_trend(self, pair, price):
        history_deque = self.price_history[pair]
        history_deque.append(price)
        history = list(history_deque)

        # Logic UP: Last 3 increasing (lebih sensitif)
        is_up = len(history) >= 3 and all(history[-i] > history[-i-1] for i in range(1, 3))
        # Logic Breakout: Current > Max of prev 5
        is_breakout = len(history) >= 6 and history[-1] > max(history[-6:-1])
        # Logic Flat Breakout: Current price > Avg of prev 10 + 0.3%  (tangkap awal pergerakan)
        is_flat_breakout = len(history) >= 10 and history[-1] > (sum(history[-10:])/10) * 1.003
        
        return "UP" if is_up else "SIDEWAYS", (is_breakout or is_flat_breakout), history

    def execute_trade(self, pair, price):
        try:
            # ── Sizing ───────────────────────────────────────────────────────
            if DRY_RUN:
                # Dry run: gunakan modal virtual dari INITIAL_CAPITAL
                amount_idr = INITIAL_CAPITAL * 0.60 * 0.25
            else:
                balance = self.exchange.fetch_balance()['free']['IDR']
                if balance < MIN_BALANCE_THRESHOLD:
                    log_message(f"STOP: Balance below {MIN_BALANCE_THRESHOLD}")
                    return False
                amount_idr = balance * 0.60 * 0.25

            if amount_idr < MIN_TRADE_IDR:
                log_message(f"SKIP: Capital {amount_idr:.0f} IDR below Min Trade {MIN_TRADE_IDR}")
                return False

            buy_price     = price * 0.9995   # Maker offset 0.05%
            amount_crypto = amount_idr / buy_price

            if DRY_RUN:
                # ── Virtual Order ─────────────────────────────────────────────
                msg = (
                    f"🟢 *DRY RUN BUY* 🟢\n"
                    f"Pair: `{pair}`\n"
                    f"Modal: Rp {amount_idr:,.0f}\n"
                    f"Harga: Rp {buy_price:,.2f}"
                )
                log_message(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
            else:
                # ── Real Order ────────────────────────────────────────────────
                msg = (
                    f"🟢 *LIVE BUY* 🟢\n"
                    f"Pair: `{pair}`\n"
                    f"Modal: Rp {amount_idr:,.0f}\n"
                    f"Harga: Rp {buy_price:,.2f}"
                )
                log_message(msg.replace('*', '').replace('\n', ' | '))
                send_telegram_message(msg)
                
                order = self.exchange.create_limit_buy_order(
                    pair, amount_crypto, buy_price, {'post_only': True}
                )
                order_id = order.get('id')
                with open(EXECUTION_LOG, "a") as f:
                    f.write(json.dumps(order) + "\n")

            # ── Simpan Posisi ke Tracker (Berlaku untuk DRY RUN maupun LIVE) ──
            pos_file = f"{RAM_PATH}/open_positions.json"
            positions = {}
            if os.path.exists(pos_file):
                with open(pos_file, "r") as f:
                    try:
                        positions = json.load(f)
                    except json.JSONDecodeError:
                        positions = {}

            positions[pair] = {
                "entry_price": buy_price,
                "amount":      amount_crypto,
                "amount_idr":  amount_idr,
                "timestamp":   datetime.now().isoformat(),
                "dry_run":     DRY_RUN,
                "status":      "OPEN" if DRY_RUN else "PENDING_BUY",
                "entry_order_id": order_id if not DRY_RUN else None,
                "exit_order_id": None
            }

            with open(pos_file, "w") as f:
                json.dump(positions, f, indent=4)

            log_message(f"Position tracking saved → {pos_file}")
            
            return True

        except Exception as e:
            log_message(f"Trade Error: {e}")
            return False

    def run(self):
        mode_label = "DRY RUN (Simulasi)" if DRY_RUN else "LIVE TRADING"
        init_msg = f"🚀 *Algovibe Scalper Bot Online*\nMode: `{mode_label}`\nCores: `6 vCPUs`"
        log_message(init_msg.replace('*', '').replace('\n', ' | '))
        send_telegram_message(init_msg)

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
            # Daily Reset — cetak laporan performa 24 jam sebelum reset
            if datetime.now().day != self.start_day:
                run_performance_report(log_fn=log_message)
                self.daily_trades = 0
                self.start_day = datetime.now().day

            # ── Ambil tickers dari WS buffer (bukan REST) ──────────────────
            tickers = self.get_tickers()
            if not tickers:
                log_message("Market WS: no data yet, waiting…")
                time.sleep(5)
                continue

            # Monitor & Execute Exits (pakai harga terbaru dari WS, beserta Predictive SL Override)
            self.exit_manager.monitor_and_execute(
                current_tickers=tickers,
                price_history=self.price_history,
                get_prediction_fn=get_prediction
            )

            # Parallel Ranking (using persistent pool)
            ranked = [r for r in self.pool.map(calculate_score, tickers.items()) if r]
            ranked.sort(key=lambda x: x['score'], reverse=True)
            log_message(f"Filtering: {len(ranked)} pairs passed base criteria (Vol > 100M, Sprd < 0.5%)")

            # Take Top N for Micro-tracking
            high_speed = [p for p in ranked if p['volatility'] > 0.5][:MAX_TRACKED_PAIRS]

            # Ambil open positions agar tidak melakukan double-buy (Orphaned Entry fix)
            open_positions = self.exit_manager.get_open_positions()

            for p in high_speed:
                if p['pair'] in open_positions:
                    continue  # Lewati pair yang sedang dipegang
                
                trend, breakout, history = self.update_history_and_trend(p['pair'], p['last'])

                # Get Prediction Probability
                tq_prob = get_prediction(history) if trend == "UP" else 0.0

                # Diagnostic Log
                imbalance_ratio = p.get('imbalance', 0)
                log_message(
                    f"Track: {p['pair']:10} | Trend: {trend:9} | "
                    f"BO: {str(breakout):5} | Sprd: {p['spread']:.3f}% | "
                    f"Imb: {imbalance_ratio:.1f}x | TQ_Prob: {tq_prob*100:.0f}%"
                )

                # Signal Trigger: Trend UP + Breakout OR Sideways + Breakout dengan Imbalance tinggi
                has_strong_imb = p.get('imbalance', 0) > 2.0
                if breakout and (trend == "UP" or has_strong_imb):
                    if self.daily_trades < MAX_TRADES_PER_DAY:
                        success = self.execute_trade(p['pair'], p['last'])
                        if success:
                            self.daily_trades += 1

            # ── Loop interval ──────────────────────────────────────────────
            # WS sudah push data real-time; sleep pendek hanya untuk
            # throttle loop CPU, bukan menunggu data baru
            time.sleep(5)


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    bot = ScalperBot()
    bot.run()