import os
import json
import time
import requests
import ccxt
import multiprocessing
from datetime import datetime

from config import (
    LOG_FILE, INITIAL_CAPITAL, MIN_BALANCE_THRESHOLD, MIN_TRADE_IDR, 
    DRY_RUN, RAM_PATH, MAX_TRADES_PER_DAY, DATA_PATH, EXECUTION_LOG
)
from engines.exit_manager import ExitManager
from engines.performance_tracker import run_performance_report

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}"
    print(formatted_msg)
    with open(LOG_FILE, "a") as f:
        f.write(formatted_msg + "\n")

def fetch_tickers():
    try:
        res = requests.get("https://indodax.com/api/tickers", timeout=10)
        return res.json()['tickers'] if res.status_code == 200 else None
    except: return None

# ==========================================
# ENGINES
# ==========================================
def calculate_score(item):
    pair, d = item
    try:
        buy, sell = float(d['buy']), float(d['sell'])
        h, l = float(d['high']), float(d['low'])
        vol = float(d['vol_idr'])
        
        spread = (sell - buy) / sell * 100
        volatility = (h - l) / l * 100
        
        if spread < 0.5 and 0.3 <= volatility <= 6.0 and vol > 100_000_000:
            # score = (volume × 0.5) + (volatility × 0.3) - (spread × 0.2)
            score = (vol * 0.5) + (volatility * 0.3) - (spread * 0.2)
            return {"pair": pair, "last": float(d['last']), "spread": spread, "volatility": volatility, "score": score}
    except: pass
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

    def update_history_and_trend(self, pair, price):
        file_path = f"{RAM_PATH}/{pair.replace('/', '_')}_history.json"
        history = []
        if os.path.exists(file_path):
            with open(file_path, 'r') as f: history = json.load(f)
        
        history.append(price)
        if len(history) > 40: history.pop(0)
        
        with open(file_path, 'w') as f: json.dump(history, f)
        
        # Logic UP: Last 4 increasing
        is_up = len(history) >= 4 and all(history[-i] > history[-i-1] for i in range(1, 4))
        # Logic Breakout: Current > Max of prev 5
        is_breakout = len(history) >= 6 and history[-1] > max(history[-6:-1])
        
        return "UP" if is_up else "SIDEWAYS", is_breakout

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

            amount_crypto = amount_idr / price
            buy_price     = price * 0.9995   # Maker offset 0.05%

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
        
        while True:
            # Daily Reset — cetak laporan performa 24 jam sebelum reset
            if datetime.now().day != self.start_day:
                run_performance_report(log_fn=log_message)
                self.daily_trades = 0
                self.start_day = datetime.now().day
            
            tickers = fetch_tickers()
            if not tickers:
                time.sleep(10); continue
            
            # Parallel Ranking
            with multiprocessing.Pool(6) as pool:
                ranked = [r for r in pool.map(calculate_score, tickers.items()) if r]
            ranked.sort(key=lambda x: x['score'], reverse=True)
            
            # Take Top 3 for Micro-tracking
            high_speed = [p for p in ranked if p['volatility'] > 1.0][:3]
            
            for p in high_speed:
                trend, breakout = self.update_history_and_trend(p['pair'], p['last'])
                
                # Diagnostic Log
                log_message(f"Track: {p['pair']:10} | Trend: {trend:9} | BO: {str(breakout):5} | Sprd: {p['spread']:.3f}%")
                
                # Signal Trigger
                if trend == "UP" and breakout and p['spread'] < 0.5:
                    if self.daily_trades < MAX_TRADES_PER_DAY:
                        success = self.execute_trade(p['pair'], p['last'])
                        if success: self.daily_trades += 1
            
            time.sleep(30)

if __name__ == "__main__":
    bot = ScalperBot()
    bot.run()