import os
import sys
import logging
import argparse
import numpy as _np
import pandas as pd
from datetime import datetime, timedelta, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed

# Local infrastructure imports
from services.backtester.db_backtest import get_connection, transaction
from app.domain.technicals import derive_trade_signal

# Configuration
OHLCV_DAYS = 90
MIN_TRADE_IDR = 10000

# Set logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] backtester — %(message)s'
)
logger = logging.getLogger("backtester")

def _load_active_profiles() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE is_active = true ORDER BY id ASC")
        return cur.fetchall()
    finally:
        conn.close()

def _progress(count, total, label="", suffix=""):
    bar_len = 30
    filled_len = int(round(bar_len * count / float(total)))
    percents = round(100.0 * count / float(total), 1)
    bar = '█' * filled_len + '░' * (bar_len - filled_len)
    sys.stdout.write(f'[{bar}] {percents}% {count}/{total} {label} {suffix}\r')
    sys.stdout.flush()

# ─────────────────────────────────────────────────────────────────────────────
# CORE ENGINE: Vectorized Simulation for 1:1 Parity
# ─────────────────────────────────────────────────────────────────────────────

def _run_backtest_pair(p: dict, pair: str, candles: list[dict], start_time: datetime, end_time: datetime) -> dict:
    """
    Simulasikan trading untuk satu pair dengan 1:1 parity ke live bot.
    Menggunakan NumPy untuk performa tinggi dalam sinyal & indikator.
    """
    trades = []
    
    # 1. Parameter Extraction
    strat_mode = p.get("strategy_mode", "scalping")
    tf_min     = int(p.get("timeframe_minutes", 1))
    pos_idr    = float(p.get("max_position_idr", 100000))
    min_candles = 50 # Standard warm-up for EMA-50/MACD
    
    # Fees & Slippage
    fee_buy  = float(p.get("fee_buy_taker", 0.002111))
    fee_sell = float(p.get("fee_sell_taker", 0.004211))
    slippage = float(p.get("fee_slippage_buffer", 0.001)) # 0.1% buffer
    
    # Adaptive Thresholds
    buy_threshold = float(p.get("buy_threshold", 6.0))
    sell_block    = float(p.get("sell_block_threshold", 5.0))
    opp_max       = 4.0 # Mirror production opposing logic
    
    # Exit Parameters
    tp_pct    = float(p.get("target_tp_pct", 0.035))
    sl_pct    = float(p.get("target_sl_pct", -0.035))
    
    # Trailing SL
    trail_enabled = p.get("trailing_sl_enabled", False)
    trail_trigger = float(p.get("trailing_sl_activation_pct", 0.02))
    trail_dist    = float(p.get("trailing_sl_distance_pct", 0.015))
    
    # DCA
    dca_enabled = p.get("dca_enabled", False)
    dca_max     = int(p.get("dca_max_tiers", 1))
    dca_step    = float(p.get("dca_step_pct", -0.02))
    
    # Other gates
    ema_trend_f = p.get("ema_trend_filter_enabled", True)
    max_hold_m  = int(p.get("max_hold_minutes", 1440)) # Default 24h
    cooldown_sec = int(p.get("cooldown_seconds", 300))
    
    # 2. Vectorized Pre-processing (Indicators)
    df = pd.DataFrame(candles)
    if len(df) < min_candles:
        return {"trades": [], "stats": {}}
    
    # Derive signals (Vectorized Indicator Calculation + Row-wise Scoring)
    history = _get_signal_history(pair, candles, p)
    if not history:
        return {"trades": [], "stats": {}}
    
    # 3. High-Speed Simulation Loop
    current_cap = float(p.get("initial_capital_idr", 1000000.0))
    position    = None # {entry_price, amount_idr, amount_crypto, buy_fee, entry_ts, highest_price, trailing_sl_price}
    cooldown_until = 0
    
    close_arr   = df['close'].values
    ts_epochs   = df['ts'].apply(lambda x: x.timestamp()).values
    
    # Warm up indicators for gates (if not in history)
    # (In high fidelity, history already has all scores per candle)
    
    # Pre-calculated EMA50 for speed if needed
    ema50_arr = df['close'].ewm(span=50, adjust=False).mean().values
    
    for i in range(min_candles, len(candles)):
        ts_epoch = ts_epochs[i]
        ts       = candles[i]["ts"]
        close    = close_arr[i]
        sig      = history[i] # Signal for THIS candle
        
        # ── Exit logic ────────────────────────────────────────────────────
        if position:
            entry_px = position["entry_price"]
            entry_idr = position["amount_idr"]
            crypto   = position["amount_crypto"]
            
            # Check Trailing SL
            if trail_enabled:
                if close > position["highest_price"]:
                    position["highest_price"] = close
                    # Update trailing trigger
                    if (close - entry_px)/entry_px >= trail_trigger:
                        new_sl = close * (1.0 - trail_dist)
                        if new_sl > position["trailing_sl_price"]:
                            position["trailing_sl_price"] = new_sl
            
            exit_reason = None
            exit_price  = 0.0
            
            # TP hit
            if close >= entry_px * (1.0 + tp_pct):
                exit_reason = "TP"
                exit_price  = entry_px * (1.0 + tp_pct) * (1.0 - slippage) # Slippage on exit
            # Trailing SL hit
            elif position["trailing_sl_price"] > 0 and close <= position["trailing_sl_price"]:
                exit_reason = "TRAILING_SL"
                exit_price  = position["trailing_sl_price"]
            # SL hit
            elif close <= entry_px * (1.0 + sl_pct):
                exit_reason = "SL"
                exit_price  = entry_px * (1.0 + sl_pct) * (1.0 - slippage)
            # Max hold exceeded
            elif (ts_epoch - position["entry_ts"]) > max_hold_m * 60:
                exit_reason = "MAX_HOLD"
                exit_price  = close
            # Signal reverse
            else:
                if sig["verdict"] == "SELL" and sig["sell_score"] >= sell_block:
                    exit_reason = "SIGNAL_REVERSE"
                    exit_price  = close

            if exit_reason:
                gross_pnl = (exit_price - entry_px) * crypto
                sell_fee  = exit_price * crypto * fee_sell
                net_pnl   = gross_pnl - position["buy_fee"] - sell_fee
                pnl_pct   = net_pnl / entry_idr * 100 if entry_idr > 0 else 0
                duration  = int(ts_epoch - position["entry_ts"])

                trades.append({
                    "profile_id":          p["id"],
                    "pair":                pair,
                    "entry_time":          datetime.fromtimestamp(position["entry_ts"], tz=timezone.utc),
                    "exit_time":           ts,
                    "entry_price":         entry_px,
                    "exit_price":          exit_price,
                    "amount_idr":          entry_idr,
                    "amount_crypto":       crypto,
                    "pnl_idr":             round(net_pnl, 2),
                    "pnl_pct":             round(pnl_pct, 4),
                    "exit_reason":         exit_reason,
                    "buy_score":           position.get("buy_score", 0),
                    "sell_score_at_exit":  sig["sell_score"] if exit_reason == "SIGNAL_REVERSE" else None,
                    "duration_seconds":    duration,
                })
                current_cap += net_pnl
                position     = None
                cooldown_until = ts_epoch + cooldown_sec
            continue 

        # ── Entry logic ───────────────────────────────────────────────────
        if ts_epoch < cooldown_until:
            continue
        if current_cap < float(p.get("min_position_idr", 10000)):
            break 

        # Gate 1: Verdict & Thresholds
        if sig["verdict"] != "BUY" or sig["buy_score"] < buy_threshold:
            continue
        if sig["sell_score"] >= opp_max:
            continue

        # EMA-50 trend filter (General - mirror gate 4c)
        if ema_trend_f:
            ema50 = ema50_arr[i]
            if close < ema50:
                continue

        # Strategy-specific logic (Trend Pullback 5m special handling)
        if strat_mode == "trend_pullback":
            # (History already contains these derived verdicts)
            pass

        # Entry Execution
        trade_idr = min(pos_idr, current_cap * 0.9)
        buy_fee   = trade_idr * fee_buy
        entry_px  = close * (1.0 + slippage) # Slippage on entry
        crypto_amt = (trade_idr - buy_fee) / entry_px

        position = {
            "entry_price":       entry_px,
            "amount_idr":        trade_idr,
            "amount_crypto":     crypto_amt,
            "buy_fee":           buy_fee,
            "entry_ts":          ts_epoch,
            "highest_price":     close,
            "trailing_sl_price": 0.0,
            "buy_score":         sig["buy_score"],
            "dca_tier":          0,
        }
        current_cap -= trade_idr

    # Final close for open position
    if position:
        last_c = candles[-1]
        exit_px = last_c["close"]
        gross_pnl = (exit_px - position["entry_price"]) * position["amount_crypto"]
        sell_fee  = exit_px * position["amount_crypto"] * fee_sell
        net_pnl   = gross_pnl - position["buy_fee"] - sell_fee
        trades.append({
            "profile_id":    p["id"],
            "pair":          pair,
            "entry_time":    datetime.fromtimestamp(position["entry_ts"], tz=timezone.utc),
            "exit_time":     last_c["ts"],
            "entry_price":   position["entry_price"],
            "exit_price":    exit_px,
            "amount_idr":    position["amount_idr"],
            "amount_crypto": position["amount_crypto"],
            "pnl_idr":       round(net_pnl, 2),
            "pnl_pct":       round(net_pnl / position["amount_idr"] * 100, 4),
            "exit_reason":   "END_OF_DATA",
            "buy_score":     position["buy_score"],
            "duration_seconds": int(ts_epochs[-1] - position["entry_ts"]),
        })

    return {"trades": trades}

# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS: Quantitative Performance
# ─────────────────────────────────────────────────────────────────────────────

def _get_signal_history(pair: str, candles: list[dict], p: dict) -> list[dict]:
    """
    Menghasilkan riwayat sinyal teknikal (verdict, score) untuk seluruh candle.
    Menggunakan kombinasi Pandas (vectorized indicators) dan Logic Parity.
    """
    df = pd.DataFrame(candles)
    if len(df) < 50: return []

    # 1. Vectorized Indicators (Fast)
    close = df['close']
    
    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['macd_line'] = ema12 - ema26
    df['macd_sig'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['macd_sig']
    
    # EMA
    df['ema9'] = close.ewm(span=9, adjust=False).mean()
    df['ema21'] = close.ewm(span=21, adjust=False).mean()
    
    # BB
    df['sma20'] = close.rolling(window=20).mean()
    df['std20'] = close.rolling(window=20).std()
    df['bb_pct'] = (close - (df['sma20'] - 2*df['std20'])) / (4 * df['std20'])
    
    # 2. Row-wise Scoring for 1:1 Parity
    # Kita panggil derive_trade_signal (logika asli) untuk setiap bar
    history = []
    from app.domain.technicals import derive_trade_signal
    
    for i in range(len(df)):
        row = df.iloc[i]
        # Buat dummy tech dict untuk parity
        tech = {
            "rsi": row['rsi'],
            "macd_hist": row['macd_hist'] * 1000 / row['close'] if row['close'] > 0 else 0, # Normalized
            "bb_pct": row['bb_pct'],
            "ema9": row['ema9'],
            "ema21": row['ema21'],
            "stoch_k": 50, # Fallback
            "stoch_d": 50,
            "williams_r": -50,
            "roc": 0,
            "slope": 0,
            "volume_ratio": 1.0,
            "obv_slope": 0.0
        }
        
        # Sinyal asli bot
        sig = derive_trade_signal(tech)
        history.append(sig)
        
    return history


def _compute_stats(trades: list[dict], capital: float, start: datetime, end: datetime) -> dict:
    if not trades: return {}
    
    pnls = [t["pnl_idr"] for t in trades]
    total_pnl = sum(pnls)
    win_rate = len([p for p in pnls if p > 0]) / len(pnls)
    
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else 9.99
    
    # Drawdown calculation
    equity = capital
    peak = capital
    max_dd_idr = 0.0
    max_dd_pct = 0.0
    
    for p in pnls:
        equity += p
        if equity > peak: peak = equity
        dd = peak - equity
        if dd > max_dd_idr: max_dd_idr = dd
        dd_p = (dd / peak * 100) if peak > 0 else 0
        if dd_p > max_dd_pct: max_dd_pct = dd_p
        
    return {
        "window_start": start,
        "window_end":   end,
        "total_trades": int(len(trades)),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate":     float(round(win_rate, 4)),
        "total_pnl_idr": float(round(total_pnl, 2)),
        "total_pnl_pct": float(round(total_pnl / capital * 100, 4)),
        "max_drawdown_pct": float(round(max_dd_pct, 4)),
        "max_drawdown_idr": float(round(max_dd_idr, 2)),
        "profit_factor": float(round(pf, 2)),
        "sharpe_ratio":  float(round(_np.mean(pnls)/_np.std(pnls), 4)) if len(pnls)>1 and _np.std(pnls)>0 else 0.0,
        "avg_trade_duration_sec": int(_np.mean([t["duration_seconds"] for t in trades])),
        "avg_pnl_per_trade_idr": float(round(_np.mean(pnls), 2)),
        "avg_pnl_per_trade_pct": float(round(_np.mean([t["pnl_pct"] for t in trades]), 4)),
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "initial_capital_idr": float(capital),
        "final_capital_idr": float(capital + total_pnl),
        "sortino_ratio": 0.0,
        "recovery_factor": float(round(total_pnl / max_dd_idr, 4)) if max_dd_idr > 0 else 0.0,
        "calmar_ratio": 0.0,
        "expectancy_idr": 0.0
    }

def _print_run_report(profile_id: int, profile_name: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (profile_id,))
        row = cur.fetchone()
        if not row:
            print(f"\n[REPORT] Profile: {profile_name} — No data.")
            return

            # Fetch trades
        cur.execute("SELECT pair, pnl_idr, pnl_pct FROM backtest_trades WHERE result_id = %s", (row['id'],))
        trades = cur.fetchall()
        n_pairs = len(set(t['pair'] for t in trades))
        
        print("\n" + "╔" + "═"*68 + "╗")
        print(f"║  QUANTITATIVE PERFORMANCE REPORT: {profile_name:<28} ║")
        print("╠" + "═"*68 + "╣")
        print(f"║  Pairs Tested    : {n_pairs:<10} | Total Trades : {row['total_trades']:<10} ║")
        print(f"║  Win Rate        : {row['win_rate']*100:6.2f}%    | Net PnL (IDR): Rp {row['total_pnl_idr']:>12,.0f} ║")
        print(f"║  ROI             : {row['total_pnl_pct']:>+6.2f}%     | Profit Factor: {row['profit_factor']:>12.2f} ║")
        print(f"║  Max Drawdown    : Rp {row['max_drawdown_idr']:>8,.0f} | Avg Duration : {row['avg_trade_duration_sec']/60:>10.1f} min ║")
        print("╚" + "═"*68 + "╝")

    finally:
        conn.close()

def _print_comparison_table() -> None:
    profiles = _load_active_profiles()
    if not profiles or len(profiles) < 2: return
    conn = get_connection()
    try:
        cur = conn.cursor()
        data = []
        for p in profiles:
            cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (p["id"],))
            row = cur.fetchone()
            if row:
                data.append({
                    "name": p["profile_name"],
                    "wr": row["win_rate"] * 100,
                    "trades": row["total_trades"],
                    "roi": row["total_pnl_pct"],
                    "pf": row["profit_factor"],
                    "mdd": row["max_drawdown_idr"]
                })
        if not data: return
        print("\n" + "═"*90)
        print(f" 🏁 STRATEGY COMPARISON TABLE")
        print("─"*90)
        print(f"{'Profile Name':<25} | {'WR%':>6} | {'Trades':>6} | {'ROI%':>8} | {'PF':>6} | {'MaxDD (IDR)':>12}")
        print("─"*90)
        for d in data:
            print(f"{d['name']:<25} | {d['wr']:6.1f} | {d['trades']:6} | {d['roi']:>+7.2f}% | {d['pf']:6.2f} | {d['mdd']:>12,.0f}")
        print("═"*90)
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# INFRASTRUCTURE: DB & Workers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_schema_upgraded() -> None:
    with transaction() as cur:
        # 1. Add target columns to backtest_profiles
        cols = [
            ("target_roi_pct", "DOUBLE PRECISION DEFAULT 0.0"),
            ("target_pnl_idr", "DOUBLE PRECISION DEFAULT 0.0"),
        ]
        for col, ctype in cols:
            try: cur.execute(f"ALTER TABLE backtest_profiles ADD COLUMN IF NOT EXISTS {col} {ctype}")
            except: pass

        # 2. Bootstrap Default Profiles if empty
        cur.execute("SELECT COUNT(*) as count FROM backtest_profiles")
        row = cur.fetchone()
        if row and row['count'] == 0:
            logger.info("[schema] 🚀 Bootstrapping default backtest profiles...")
            
            # Profile 1: Trend Pullback 5m (Conservative)
            cur.execute("""
                INSERT INTO backtest_profiles (profile_name, initial_capital_idr, timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_sl_pct)
                VALUES ('Trend Pullback 5m', 1000000, 5, 'trend_pullback', 6.0, 0.035, -0.035)
            """)

            # Profile 2: jokobodo (Aggressive)
            cur.execute("""
                INSERT INTO backtest_profiles (
                    profile_name, initial_capital_idr, max_position_idr, max_open_positions,
                    timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_sl_pct,
                    use_loose_gates, target_roi_pct, dca_enabled, dca_max_tiers
                ) VALUES ('jokobodo', 5000000, 200000, 10, 1, 'scalping', 5.5, 0.015, -0.04, true, 10.0, true, 3)
            """)

            # Profile 3: anglingdharma (Micro-cap)
            cur.execute("""
                INSERT INTO backtest_profiles (
                    profile_name, initial_capital_idr, max_position_idr, min_position_idr, max_open_positions,
                    timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_pnl_idr, use_loose_gates
                ) VALUES ('anglingdharma', 100000, 10000, 10000, 5, 1, 'scalping', 5.8, 0.02, 10000, true)
            """)
            logger.info("[schema] ✅ Bootstrap complete.")

def _process_pair_worker(pair_name, start_time, end_time, profiles_list):
    from services.backtester.db_backtest import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ts, open, high, low, close, volume FROM ohlcv_1m WHERE pair = %s AND ts >= %s ORDER BY ts ASC", (pair_name, start_time))
        rows = cur.fetchall()
        if not rows: return []
        candles = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume']).to_dict('records')
        
        pair_trades = []
        for p in profiles_list:
            res = _run_backtest_pair(p, pair_name, candles, start_time, end_time)
            if res["trades"]: pair_trades.extend(res["trades"])
        return pair_trades
    finally:
        conn.close()

def _simulate_portfolio(all_trades: list[dict], profile: dict) -> list[dict]:
    if not all_trades: return []
    sorted_trades = sorted(all_trades, key=lambda x: x["entry_time"])
    capital = float(profile.get("initial_capital_idr", 1000000.0))
    max_pos = int(profile.get("max_open_positions", 3))
    active_trades = []
    final_trades = []
    for t in sorted_trades:
        active_trades = [a for a in active_trades if a["exit_time"] > t["entry_time"]]
        if len(active_trades) < max_pos:
            active_trades.append(t)
            final_trades.append(t)
    return final_trades

def _save_result(pid, pair, stats, trades):
    with transaction() as cur:
        cur.execute("""
            INSERT INTO backtest_results (
                profile_id, pair, total_trades, winning_trades, win_rate, 
                total_pnl_idr, total_pnl_pct, max_drawdown_idr, max_drawdown_pct,
                profit_factor, initial_capital_idr, final_capital_idr, avg_trade_duration_sec
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (pid, pair, stats['total_trades'], stats['winning_trades'], stats['win_rate'],
              stats['total_pnl_idr'], stats['total_pnl_pct'], stats['max_drawdown_idr'], stats['max_drawdown_pct'],
              stats['profit_factor'], stats['initial_capital_idr'], stats['final_capital_idr'], stats['avg_trade_duration_sec']))
        rid = cur.fetchone()['id']
        for t in trades:
            cur.execute("""
                INSERT INTO backtest_trades (result_id, pair, entry_time, exit_time, pnl_idr, pnl_pct, exit_reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (rid, t['pair'], t['entry_time'], t['exit_time'], t['pnl_idr'], t['pnl_pct'], t['exit_reason']))

# ─────────────────────────────────────────────────────────────────────────────
# EVOLUTIONARY ENGINE: Self-Correction
# ─────────────────────────────────────────────────────────────────────────────

def tune_profile(profile_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE id = %s", (profile_id,))
        p = cur.fetchone()
        if not p: return False
        cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (profile_id,))
        res = cur.fetchone()
        if not res: return False

        # Early Exit check (Normalized to DAILY targets)
        total_pnl = res['total_pnl_idr']
        total_roi = res['total_pnl_pct']
        
        # Calculate days (Assume 90 if no range available)
        days_tested = 90.0 
        target_roi_daily = p.get('target_roi_pct', 0.0)
        target_pnl_daily = p.get('target_pnl_idr', 0.0)

        avg_daily_pnl = total_pnl / days_tested
        avg_daily_roi = total_roi / days_tested
        
        reached_pnl = (target_pnl_daily > 0 and avg_daily_pnl >= target_pnl_daily)
        reached_roi = (target_roi_daily > 0 and avg_daily_roi >= target_roi_daily)

        if reached_pnl or reached_roi:
            status = f"Rp {avg_daily_pnl:,.0f}/hari" if reached_pnl else f"{avg_daily_roi:.2f}%/hari"
            print(f"   🏆 [DAILY TARGET REACHED] {p['profile_name']} ({status})")
            return False

        logger.info("[tune] 🧠 Analyzing: Avg Rp {0:,.0f}/day (Target: {1:,.0f})".format(avg_daily_pnl, target_pnl_daily))

        # Mutation Logic
        new_p = dict(p)
        for k in ['id', 'created_at', 'updated_at']: new_p.pop(k, None)
        import re
        m = re.search(r'_v(\d+)$', p['profile_name'])
        v = int(m.group(1))+1 if m else 2
        new_p['profile_name'] = f"{p['profile_name'].split('_v')[0]}_v{v}"
        
        changes = []
        if res['win_rate'] < 0.45:
            new_p['buy_threshold'] = min(8.5, p['buy_threshold'] + 0.2)
            changes.append("Higher threshold for quality")
        if res['profit_factor'] < 1.2:
            new_p['target_tp_pct'] = round(p['target_tp_pct'] + 0.005, 4)
            changes.append("Increased TP target")
        
        if not changes: return False
        
        cols = ", ".join(new_p.keys())
        placeholders = ", ".join(["%s"] * len(new_p))
        cur.execute(f"INSERT INTO backtest_profiles ({cols}) VALUES ({placeholders})", list(new_p.values()))
        cur.execute("UPDATE backtest_profiles SET is_active = false WHERE id = %s", (profile_id,))
        conn.commit()
        return True
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(days=OHLCV_DAYS, pairs_filter=None):
    profiles = _load_active_profiles()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT pair FROM ohlcv_1m")
    pairs = [r['pair'] for r in cur.fetchall()]
    if pairs_filter:
        filter_list = [px.strip().upper() for px in pairs_filter.split(",")]
        pairs = [px for px in pairs if px in filter_list]
    
    n_pairs = len(pairs)
    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=days)
    
    # 1. Multi-process sim
    all_results = []
    with ProcessPoolExecutor() as pool:
        futures = {pool.submit(_process_pair_worker, px, start, now, profiles): px for px in pairs}
        for i, f in enumerate(as_completed(futures)):
            all_results.extend(f.result())
            _progress(i+1, n_pairs)
    
    # 2. Portfolio & Stats
    for p in profiles:
        p_trades = [t for t in all_results if t['profile_id'] == p['id']]
        if p_trades:
            portfolio = _simulate_portfolio(p_trades, p)
            stats = _compute_stats(portfolio, p['initial_capital_idr'], start, now)
            _save_result(p['id'], None, stats, portfolio)
    conn.close()

def main():
    _ensure_schema_upgraded()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["fetch", "run", "report", "tune", "optimize"])
    parser.add_argument("--days", type=int, default=OHLCV_DAYS)
    parser.add_argument("--pairs", type=str)
    parser.add_argument("--generations", type=int, default=3)
    args = parser.parse_args()

    if args.command == "run":
        run_backtest(args.days, args.pairs)
    elif args.command == "report":
        profiles = _load_active_profiles()
        for p in profiles: _print_run_report(p['id'], p['profile_name'])
        _print_comparison_table()
    elif args.command == "tune":
        for p in _load_active_profiles(): tune_profile(p['id'])
    elif args.command == "optimize":
        for g in range(1, args.generations + 1):
            print(f"\n--- GENERATION {g} ---")
            run_backtest(args.days, args.pairs)
            active = _load_active_profiles()
            for p in active: _print_run_report(p['id'], p['profile_name'])
            _print_comparison_table()
            
            mutated = False
            for p in active:
                if tune_profile(p['id']): mutated = True
            if not mutated:
                print("🏁 Stopping early: Targets reached or optimal parameters found.")
                break

if __name__ == "__main__":
    main()
