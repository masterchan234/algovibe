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

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}"
    print(formatted_msg)
    with open(LOG_FILE, "a") as f:
        f.write(formatted_msg + "\n")

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

        spread     = (sell - buy) / sell * 100 if sell else 0
        volatility = (h - l) / l * 100 if l else 0

        if spread < 0.5 and 0.3 <= volatility <= 6.0 and vol > MIN_VOLUME_FILTER:
            # score = (volume × 0.5) + (volatility × 0.3) - (spread × 0.2)
            score = (vol * 0.5) + (volatility * 0.3) - (spread * 0.2)
            return {
                "pair": pair,
                "last": last,
                "spread": spread,
                "volatility": volatility,
                "score": score,
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

        # Jika order DONE / FILLED — hapus posisi terbuka dari file
        if status in ("DONE", "FILL", "CANCELLED", "REJECTED"):
            pos_file = f"{RAM_PATH}/open_positions.json"
            if os.path.exists(pos_file):
                try:
                    with open(pos_file, "r") as f:
                        positions = json.load(f)

                    # Cocokkan berdasarkan symbol (pair format: btcidr → BTC/IDR)
                    pair_normalized = symbol  # indodax pakai lowercase pair
                    removed = False
                    for key in list(positions.keys()):
                        if key.replace("/", "").lower() == pair_normalized.lower():
                            del positions[key]
                            removed = True
                            break

                    if removed:
                        with open(pos_file, "w") as f:
                            json.dump(positions, f, indent=4)
                        log_message(
                            f"[PWS] Position cleared: {symbol} "
                            f"(status={status}, orderId={order_id})"
                        )
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

        # Logic UP: Last 4 increasing
        is_up = len(history) >= 4 and all(history[-i] > history[-i-1] for i in range(1, 4))
        # Logic Breakout: Current > Max of prev 5
        is_breakout = len(history) >= 6 and history[-1] > max(history[-6:-1])

        return "UP" if is_up else "SIDEWAYS", is_breakout, history

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
                log_message(
                    f"DRY RUN: Virtual Order Placed | {pair} | "
                    f"Amt: {amount_idr:,.0f} IDR | "
                    f"Price: {buy_price:,.2f} | "
                    f"Crypto: {amount_crypto:.6f}"
                )

                # Simpan ke open_positions.json agar ExitManager bisa memantau
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
                    "dry_run":     True,
                }

                with open(pos_file, "w") as f:
                    json.dump(positions, f, indent=4)

                log_message(f"DRY RUN: Position saved → {pos_file}")

            else:
                # ── Real Order ────────────────────────────────────────────────
                log_message(f"EXEC BUY: {pair} | Amt: {amount_idr:,.0f} IDR | Price: {buy_price:,.2f}")
                order = self.exchange.create_limit_buy_order(
                    pair, amount_crypto, buy_price, {'post_only': True}
                )
                with open(EXECUTION_LOG, "a") as f:
                    f.write(json.dumps(order) + "\n")

            return True

        except Exception as e:
            log_message(f"Trade Error: {e}")
            return False

    def run(self):
        mode_label = "DRY RUN (Simulasi)" if DRY_RUN else "LIVE TRADING"
        log_message(f"Scalper Bot Initialized on 6 vCPUs. Mode: {mode_label}.")

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

            # Monitor & Execute Exits (pakai harga terbaru dari WS)
            self.exit_manager.monitor_and_execute(tickers)

            # Parallel Ranking (using persistent pool)
            ranked = [r for r in self.pool.map(calculate_score, tickers.items()) if r]
            ranked.sort(key=lambda x: x['score'], reverse=True)
            log_message(f"Filtering: {len(ranked)} pairs passed base criteria (Vol > 100M, Sprd < 0.5%)")

            # Take Top N for Micro-tracking
            high_speed = [p for p in ranked if p['volatility'] > 0.5][:MAX_TRACKED_PAIRS]

            for p in high_speed:
                trend, breakout, history = self.update_history_and_trend(p['pair'], p['last'])

                # Get Prediction Probability
                tq_prob = get_prediction(history) if trend == "UP" else 0.0

                # Diagnostic Log
                log_message(
                    f"Track: {p['pair']:10} | Trend: {trend:9} | "
                    f"BO: {str(breakout):5} | Sprd: {p['spread']:.3f}% | "
                    f"TQ_Prob: {tq_prob*100:.0f}%"
                )

                # Signal Trigger (Predictive Filter > 75%)
                if trend == "UP" and breakout and p['spread'] < 0.5 and tq_prob > 0.75:
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