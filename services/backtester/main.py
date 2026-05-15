import os
import sys
import json
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
DEFAULT_DAILY_TARGET_PNL_IDR = 1000.0
DEFAULT_BALANCE_IDR = 100000.0
DEFAULT_MIN_LIQUIDITY_24H_IDR = 0.0
DEFAULT_MIN_PRICE_IDR = 0.0

RSI_OVERSOLD_BASE = 35.0
RSI_OVERBOUGHT_BASE = 70.0
RSI_ADAPT_SCALE = 8.0
RSI_OVERSOLD_MIN = 20.0
RSI_OVERSOLD_MAX = 45.0
RSI_OVERBOUGHT_MIN = 55.0
RSI_OVERBOUGHT_MAX = 85.0

# Set logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] backtester — %(message)s'
)
logger = logging.getLogger("backtester")

def _is_running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    cgroup_paths = [
        "/proc/1/cgroup",
        "/proc/self/cgroup",
    ]
    for path in cgroup_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
            if any(token in txt for token in ("docker", "containerd", "kubepods")):
                return True
        except Exception:
            pass
    return False


def _ensure_container_runtime(command: str) -> None:
    if os.getenv("BACKTEST_ALLOW_HOST_RUN", "0") == "1":
        logger.warning("[runtime] BACKTEST_ALLOW_HOST_RUN=1 set, bypassing container-only guard.")
        return
    if _is_running_in_container():
        return

    print("❌ Backtester must run inside Docker container.")
    print("   Use one of these commands:")
    print(f"   docker compose run --rm backtester {command}")
    print(f"   rtk docker compose run --rm backtester {command}")
    print("   (Emergency bypass: BACKTEST_ALLOW_HOST_RUN=1)")
    raise SystemExit(2)

def _load_active_profiles() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE is_active = true ORDER BY id ASC")
        return cur.fetchall()
    finally:
        conn.close()


def _get_latest_ohlcv_ts() -> datetime | None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(ts) AS max_ts FROM ohlcv_1m")
        row = cur.fetchone()
        return row["max_ts"] if row else None
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
    pair_cfg = p.get("pair_setup") or {}
    
    # 1. Parameter Extraction
    strat_mode = p.get("strategy_mode", "scalping")
    tf_min     = int(p.get("timeframe_minutes", 1))
    pos_idr    = float(pair_cfg.get("max_position_idr", p.get("max_position_idr", 100000)))
    min_candles = 50 # Standard warm-up for EMA-50/MACD
    
    # Fees & Slippage
    fee_buy  = float(p.get("fee_buy_taker", 0.002111))
    fee_sell = float(p.get("fee_sell_taker", 0.004211))
    slippage = float(p.get("fee_slippage_buffer", 0.001)) # 0.1% buffer
    
    # Adaptive Thresholds
    buy_threshold = float(pair_cfg.get("buy_score_threshold", p.get("buy_threshold", 6.0)))
    sell_block    = float(pair_cfg.get("sell_block_threshold", p.get("sell_block_threshold", 5.0)))
    opp_max       = float(pair_cfg.get("opposing_max", 4.0)) # Mirror production opposing logic
    
    # Exit Parameters
    tp_pct    = float(pair_cfg.get("target_tp_pct", p.get("target_tp_pct", 0.035)))
    sl_pct    = float(pair_cfg.get("target_sl_pct", p.get("target_sl_pct", -0.035)))
    
    # Trailing SL
    trail_enabled = p.get("trailing_sl_enabled", False)
    trail_trigger = float(p.get("trailing_sl_activation_pct", 0.02))
    trail_dist    = float(p.get("trailing_sl_distance_pct", 0.015))
    
    # DCA
    dca_enabled = pair_cfg.get("dca_enabled", p.get("dca_enabled", False))
    dca_max     = int(pair_cfg.get("dca_max_tiers", p.get("dca_max_tiers", 1)))
    dca_step    = float(p.get("dca_step_pct", -0.02))
    
    # Other gates
    ema_trend_f = pair_cfg.get("ema_trend_filter_enabled", p.get("ema_trend_filter_enabled", True))
    max_hold_m  = int(pair_cfg.get("max_hold_minutes", p.get("max_hold_minutes", 1440))) # Default 24h
    cooldown_sec = int(pair_cfg.get("cooldown_seconds", p.get("cooldown_seconds", 300)))
    min_position_idr = float(pair_cfg.get("min_position_idr", p.get("min_position_idr", MIN_TRADE_IDR)))
    
    # 2. Vectorized Pre-processing (Indicators)
    df = pd.DataFrame(candles)
    if len(df) < min_candles:
        return {"trades": [], "stats": {}}
    
    # Derive signals (Vectorized Indicator Calculation + Row-wise Scoring)
    if tf_min > 1:
        # Resample dataframe for indicators
        df_ts = df.copy()
        df_ts['ts'] = pd.to_datetime(df_ts['ts'])
        df_res = df_ts.set_index('ts').resample(f'{tf_min}min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        resampled_candles = df_res.reset_index().to_dict('records')
        res_history = _get_signal_history(pair, resampled_candles, p, pair_cfg)
        
        if not res_history:
            return {"trades": [], "stats": {}}
            
        # Forward-fill signals back to 1m resolution
        # Shift signals by 1 period to eliminate look-ahead bias
        # (Signal from period N is only available at the start of period N+1)
        sig_map = pd.Series(res_history, index=df_res.index).shift(1)
        
        df_sig = df_ts[['ts']].set_index('ts')
        df_sig['sig'] = sig_map
        df_sig['sig'] = df_sig['sig'].ffill()
        
        empty_sig = {"verdict": "HOLD", "buy_score": 0.0, "sell_score": 0.0}
        history = [s if isinstance(s, dict) else empty_sig for s in df_sig['sig'].tolist()]
    else:
        history = _get_signal_history(pair, candles, p, pair_cfg)

    if not history or len(history) != len(candles):
        return {"trades": [], "stats": {}}
    
    # 3. High-Speed Simulation Loop
    current_cap = float(p.get("initial_capital_idr", 1000000.0))
    position    = None # {entry_price, amount_idr, amount_crypto, buy_fee, entry_ts, highest_price, trailing_sl_price}
    cooldown_until = 0
    
    close_arr   = df['close'].values
    ts_epochs   = _np.array([c["ts"].timestamp() for c in candles])
    
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
                exit_price  = position["trailing_sl_price"] * (1.0 - slippage)
            # SL hit
            elif close <= entry_px * (1.0 + sl_pct):
                exit_reason = "SL"
                exit_price  = entry_px * (1.0 + sl_pct) * (1.0 - slippage)
            # Max hold exceeded
            elif (ts_epoch - position["entry_ts"]) > max_hold_m * 60:
                exit_reason = "MAX_HOLD"
                exit_price  = close * (1.0 - slippage)
            # Signal reverse
            else:
                if sig["verdict"] == "SELL" and sig["sell_score"] >= sell_block:
                    exit_reason = "SIGNAL_REVERSE"
                    exit_price  = close * (1.0 - slippage)

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
                # Return principal + realized PnL back to free capital.
                current_cap += entry_idr + net_pnl
                position     = None
                cooldown_until = ts_epoch + cooldown_sec
            continue 

        # ── Entry logic ───────────────────────────────────────────────────
        if ts_epoch < cooldown_until:
            continue
        if current_cap < min_position_idr:
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
        exit_px = last_c["close"] * (1.0 - slippage)
        gross_pnl = (exit_px - position["entry_price"]) * position["amount_crypto"]
        sell_fee  = exit_px * position["amount_crypto"] * fee_sell
        net_pnl   = gross_pnl - position["buy_fee"] - sell_fee
        current_cap += position["amount_idr"] + net_pnl
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
            "sell_score_at_exit": None,
            "duration_seconds": int(ts_epochs[-1] - position["entry_ts"]),
        })

    return {"trades": trades}

# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS: Quantitative Performance
# ─────────────────────────────────────────────────────────────────────────────

def _get_signal_history(pair: str, candles: list[dict], p: dict, pair_cfg: dict | None = None) -> list[dict]:
    """
    Menghasilkan riwayat sinyal teknikal (verdict, score) untuk seluruh candle.
    Menggunakan kombinasi Pandas (vectorized indicators) dan Logic Parity.
    """
    df = pd.DataFrame(candles)
    if len(df) < 50: return []

    # 1. Vectorized Indicators (Wilder's Smoothing for Parity)
    close = df['close']
    high  = df['high']
    low   = df['low']
    volume = df['volume']
    
    # Wilder's RSI (EWM with alpha=1/period)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
    loss = -delta.where(delta < 0, 0).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, _np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))
    df['rsi'] = df['rsi'].fillna(50.0)
    
    # ATR (Wilder's Smoothing)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    df['tr'] = _np.maximum(tr1, _np.maximum(tr2, tr3))
    df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
    df['atr_pct'] = (df['atr'] / close) * 100.0
    
    # MACD & EMA
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['macd_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    
    df['ema9']  = close.ewm(span=9, adjust=False).mean()
    df['ema21'] = close.ewm(span=21, adjust=False).mean()
    df['ema50'] = close.ewm(span=50, adjust=False).mean()
    
    # BB
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_denom = (4 * std20).replace(0, _np.nan)
    df['bb_pct'] = ((close - (sma20 - 2*std20)) / bb_denom).fillna(0.5)

    # Stochastic RSI (14,3,3)
    rsi_min = df['rsi'].rolling(14).min()
    rsi_max = df['rsi'].rolling(14).max()
    stoch_denom = (rsi_max - rsi_min).replace(0, _np.nan)
    stoch_raw = ((df['rsi'] - rsi_min) / stoch_denom) * 100.0
    df['stoch_k'] = stoch_raw.rolling(3, min_periods=1).mean().fillna(50.0)
    df['stoch_d'] = df['stoch_k'].rolling(3, min_periods=1).mean().fillna(50.0)

    # Williams %R (14)
    hh14 = high.rolling(14).max()
    ll14 = low.rolling(14).min()
    wr_denom = (hh14 - ll14).replace(0, _np.nan)
    df['williams_r'] = (-100.0 * (hh14 - close) / wr_denom).fillna(-50.0)

    # ROC-10 (%)
    df['roc'] = (close.pct_change(10) * 100.0).fillna(0.0)

    # Momentum slope (5-candle percent change)
    df['slope'] = close.pct_change(5).fillna(0.0)

    # Volume ratio vs MA-20 volume
    vol_ma20 = volume.rolling(20).mean().replace(0, _np.nan)
    df['volume_ratio'] = (volume / vol_ma20).fillna(1.0)

    # OBV slope as 5-candle percent change (%)
    obv_delta = _np.sign(close.diff().fillna(0.0)) * volume.fillna(0.0)
    obv = obv_delta.cumsum()
    obv_prev = obv.shift(5).abs().replace(0, _np.nan)
    df['obv_slope'] = (((obv - obv.shift(5)) / obv_prev) * 100.0).fillna(0.0)
    
    # 2. Row-wise Scoring with Entry Gates (EMA-50 Trend + ATR Filter)
    history = []
    
    records = df.to_dict('records')
    for row in records:
        # Gate 4c: EMA-50 Trend Filter (Standard Spot Scalping Safety)
        cfg = pair_cfg or {}
        ema_enabled = cfg.get("ema_trend_filter_enabled", p.get("ema_trend_filter_enabled", True))
        is_uptrend = (not ema_enabled) or (row['close'] > row['ema50'])
        
        # Gate 4b: ATR Volatility Filter
        atr_limit = float(cfg.get("atr_max_pct", p.get("atr_max_pct", 3.0)))
        is_stable = row['atr_pct'] < atr_limit
        
        # Buat dummy tech dict untuk parity
        tech = {
            "rsi": row['rsi'],
            "macd_hist": row['macd_hist'],
            "last_price": row['close'],
            "bb_pct": row['bb_pct'],
            "ema9": row['ema9'],
            "ema21": row['ema21'],
            "ema_cross": "BULLISH" if row['ema9'] > row['ema21'] else "BEARISH",
            "stoch_k": row['stoch_k'],
            "stoch_d": row['stoch_d'],
            "williams_r": row['williams_r'],
            "roc": row['roc'],
            "slope": row['slope'],
            "volume_ratio": row['volume_ratio'],
            "obv_slope": row['obv_slope'],
        }
        
        sig = derive_trade_signal(tech, adaptive_thresholds=cfg)
        
        # Enforce Entry Gates
        if sig["verdict"] == "BUY":
            if not is_uptrend:
                sig["verdict"] = "HOLD"
                sig["reason"]  = "REJECTED (EMA50 Trend Filter)"
            elif not is_stable:
                sig["verdict"] = "HOLD"
                sig["reason"]  = "REJECTED (ATR Volatility Filter)"
        
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

def _run_backtest_migrations():
    """Run Alembic migrations for the backtest database."""
    try:
        from alembic import command
        from alembic.config import Config
        from services.backtester.db_backtest import DATABASE_URL_BACKTEST
        
        logger.info(f"[schema] 🚀 Running backtest migrations for {DATABASE_URL_BACKTEST}...")
        alembic_cfg = Config("alembic_backtest.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL_BACKTEST)
        command.upgrade(alembic_cfg, "head")
        logger.info("[schema] ✅ Backtest migrations complete.")
    except Exception as e:
        logger.warning(f"[schema] ⚠️ Backtest migration failed (might already be up to date): {e}")

def _ensure_schema_upgraded() -> None:
    # First, run alembic migrations
    _run_backtest_migrations()
    
    with transaction() as cur:
        # Check if backtest_profiles table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'backtest_profiles'
            );
        """)
        if not cur.fetchone()['exists']:
            logger.error("[schema] ❌ Table 'backtest_profiles' still missing after migrations. Check your DB setup.")
            return

        # 0. Per-coin quantitative analysis table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS backtest_coin_analysis (
                id BIGSERIAL PRIMARY KEY,
                analysis_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                window_start TIMESTAMPTZ NOT NULL,
                window_end TIMESTAMPTZ NOT NULL,
                pair TEXT NOT NULL,
                n_candles BIGINT NOT NULL,
                n_days BIGINT NOT NULL,
                mu_1m DOUBLE PRECISION,
                sigma_1m DOUBLE PRECISION,
                mad_1m DOUBLE PRECISION,
                up_ratio DOUBLE PRECISION,
                jump_1pct_ratio DOUBLE PRECISION,
                acf1 DOUBLE PRECISION,
                avg_day_range DOUBLE PRECISION,
                sigma_day DOUBLE PRECISION,
                mu_day DOUBLE PRECISION,
                sharpe_day_like DOUBLE PRECISION,
                momentum_quality DOUBLE PRECISION,
                target_daily_pnl DOUBLE PRECISION NOT NULL,
                balance_idr DOUBLE PRECISION NOT NULL,
                fee_rt DOUBLE PRECISION,
                p_hat DOUBLE PRECISION,
                breakeven_p DOUBLE PRECISION,
                win_net DOUBLE PRECISION,
                loss_net DOUBLE PRECISION,
                edge_trade_raw DOUBLE PRECISION,
                edge_trade DOUBLE PRECISION,
                edge_trade_effective DOUBLE PRECISION,
                trades_needed_full_cap DOUBLE PRECISION,
                rec_buy_threshold DOUBLE PRECISION,
                rec_sell_block_threshold DOUBLE PRECISION,
                rec_buy_score_threshold DOUBLE PRECISION,
                rec_sell_score_threshold DOUBLE PRECISION,
                rec_opposing_max DOUBLE PRECISION,
                rec_target_tp_pct DOUBLE PRECISION,
                rec_target_sl_pct DOUBLE PRECISION,
                rec_cooldown_seconds INTEGER,
                rec_max_hold_minutes INTEGER,
                rec_ema_trend_filter_enabled BOOLEAN,
                rec_atr_max_pct DOUBLE PRECISION,
                rec_max_position_idr DOUBLE PRECISION,
                rec_max_open_positions INTEGER,
                rec_dca_enabled BOOLEAN,
                rec_dca_max_tiers INTEGER,
                rec_rsi_oversold DOUBLE PRECISION,
                rec_rsi_overbought DOUBLE PRECISION,
                rec_bb_extreme_low DOUBLE PRECISION,
                rec_bb_low DOUBLE PRECISION,
                rec_bb_high DOUBLE PRECISION,
                rec_bb_extreme_high DOUBLE PRECISION,
                rec_stoch_k_oversold DOUBLE PRECISION,
                rec_stoch_d_oversold DOUBLE PRECISION,
                rec_stoch_k_overbought DOUBLE PRECISION,
                rec_stoch_d_overbought DOUBLE PRECISION,
                rec_williams_deep_oversold DOUBLE PRECISION,
                rec_williams_oversold DOUBLE PRECISION,
                rec_williams_overbought DOUBLE PRECISION,
                rec_williams_deep_overbought DOUBLE PRECISION,
                rec_slope_strong_up DOUBLE PRECISION,
                rec_slope_up DOUBLE PRECISION,
                rec_slope_down DOUBLE PRECISION,
                rec_slope_strong_down DOUBLE PRECISION,
                rec_roc_oversold DOUBLE PRECISION,
                rec_roc_mild_down DOUBLE PRECISION,
                rec_roc_mild_up DOUBLE PRECISION,
                rec_roc_overbought DOUBLE PRECISION,
                rec_ob_strong_bid DOUBLE PRECISION,
                rec_ob_bid_dom DOUBLE PRECISION,
                rec_ob_ask_dom DOUBLE PRECISION,
                rec_ob_strong_ask DOUBLE PRECISION,
                rec_volume_spike DOUBLE PRECISION,
                rec_volume_high DOUBLE PRECISION,
                rec_volume_low DOUBLE PRECISION,
                rec_volume_dry DOUBLE PRECISION,
                rec_obv_accumulate DOUBLE PRECISION,
                rec_obv_rising DOUBLE PRECISION,
                rec_obv_falling DOUBLE PRECISION,
                rec_obv_distribute DOUBLE PRECISION,
                UNIQUE (pair, window_start, window_end, target_daily_pnl, balance_idr)
            )
        """)

        # Backward compatibility for earlier schema versions.
        coin_cols = [
            ("rec_buy_score_threshold", "DOUBLE PRECISION"),
            ("rec_sell_score_threshold", "DOUBLE PRECISION"),
            ("rec_opposing_max", "DOUBLE PRECISION"),
            ("rec_rsi_oversold", "DOUBLE PRECISION"),
            ("rec_rsi_overbought", "DOUBLE PRECISION"),
            ("rec_bb_extreme_low", "DOUBLE PRECISION"),
            ("rec_bb_low", "DOUBLE PRECISION"),
            ("rec_bb_high", "DOUBLE PRECISION"),
            ("rec_bb_extreme_high", "DOUBLE PRECISION"),
            ("rec_stoch_k_oversold", "DOUBLE PRECISION"),
            ("rec_stoch_d_oversold", "DOUBLE PRECISION"),
            ("rec_stoch_k_overbought", "DOUBLE PRECISION"),
            ("rec_stoch_d_overbought", "DOUBLE PRECISION"),
            ("rec_williams_deep_oversold", "DOUBLE PRECISION"),
            ("rec_williams_oversold", "DOUBLE PRECISION"),
            ("rec_williams_overbought", "DOUBLE PRECISION"),
            ("rec_williams_deep_overbought", "DOUBLE PRECISION"),
            ("rec_slope_strong_up", "DOUBLE PRECISION"),
            ("rec_slope_up", "DOUBLE PRECISION"),
            ("rec_slope_down", "DOUBLE PRECISION"),
            ("rec_slope_strong_down", "DOUBLE PRECISION"),
            ("rec_roc_oversold", "DOUBLE PRECISION"),
            ("rec_roc_mild_down", "DOUBLE PRECISION"),
            ("rec_roc_mild_up", "DOUBLE PRECISION"),
            ("rec_roc_overbought", "DOUBLE PRECISION"),
            ("rec_ob_strong_bid", "DOUBLE PRECISION"),
            ("rec_ob_bid_dom", "DOUBLE PRECISION"),
            ("rec_ob_ask_dom", "DOUBLE PRECISION"),
            ("rec_ob_strong_ask", "DOUBLE PRECISION"),
            ("rec_volume_spike", "DOUBLE PRECISION"),
            ("rec_volume_high", "DOUBLE PRECISION"),
            ("rec_volume_low", "DOUBLE PRECISION"),
            ("rec_volume_dry", "DOUBLE PRECISION"),
            ("rec_obv_accumulate", "DOUBLE PRECISION"),
            ("rec_obv_rising", "DOUBLE PRECISION"),
            ("rec_obv_falling", "DOUBLE PRECISION"),
            ("rec_obv_distribute", "DOUBLE PRECISION"),
            ("edge_trade_raw", "DOUBLE PRECISION"),
            ("edge_trade_effective", "DOUBLE PRECISION"),
        ]
        for col, ctype in coin_cols:
            sp = f"sp_coin_{col}"
            try:
                cur.execute(f"SAVEPOINT {sp}")
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_name='backtest_coin_analysis' AND column_name='{col}'
                    """
                )
                if cur.fetchone()["count"] == 0:
                    cur.execute(f"ALTER TABLE backtest_coin_analysis ADD COLUMN {col} {ctype}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")

        # 0b. Snapshot table: setup teknikal per-pair yang dipakai pada setiap result run.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_result_pair_setups (
                id BIGSERIAL PRIMARY KEY,
                result_id BIGINT NOT NULL,
                profile_id BIGINT NOT NULL,
                pair TEXT NOT NULL,
                setup_json JSONB NOT NULL,
                source_analysis_ts TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (result_id, pair)
            )
            """
        )

        # 0c. Scope pair per profile agar profile quant tidak diterapkan lintas pair secara tidak sengaja.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_profile_pair_scope (
                id BIGSERIAL PRIMARY KEY,
                profile_id BIGINT NOT NULL,
                pair TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (profile_id, pair)
            )
            """
        )

        # 1. Add target columns to backtest_profiles (Legacy support for old DBs)
        cols = [
            ("target_roi_pct", "DOUBLE PRECISION DEFAULT 0.0"),
            ("target_pnl_idr", "DOUBLE PRECISION DEFAULT 0.0"),
        ]
        for col, ctype in cols:
            savepoint = f"sp_add_col_{col}"
            try:
                cur.execute(f"SAVEPOINT {savepoint}")
                cur.execute(f"""
                    SELECT COUNT(*) FROM information_schema.columns 
                    WHERE table_name='backtest_profiles' AND column_name='{col}'
                """)
                if cur.fetchone()['count'] == 0:
                    cur.execute(f"ALTER TABLE backtest_profiles ADD COLUMN {col} {ctype}")
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                logger.warning(f"[schema] Failed to add column {col}: {e}")

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
                    timeframe_minutes, strategy_mode, buy_threshold, target_tp_pct, target_sl_pct,
                    target_pnl_idr, use_loose_gates, max_hold_minutes
                ) VALUES ('anglingdharma', 100000, 10000, 10000, 5, 15, 'scalping', 4.5, 0.035, -0.02, 10000, true, 2880)
            """)
            logger.info("[schema] ✅ Bootstrap complete.")

def _process_pair_worker(pair_name, start_time, end_time, profiles_list):
    from services.backtester.db_backtest import get_connection
    
    # Dynamic Time Bucket optimization
    min_tf = min([int(p.get("timeframe_minutes", 1)) for p in profiles_list] + [1])
    if min_tf < 1: min_tf = 1

    conn = get_connection()
    try:
        cur = conn.cursor()
        if min_tf > 1:
            query = f"""
                SELECT 
                    time_bucket('{min_tf} minutes', ts) AS ts,
                    first(open, ts) AS open,
                    max(high) AS high,
                    min(low) AS low,
                    last(close, ts) AS close,
                    sum(volume) AS volume
                FROM ohlcv_1m 
                WHERE pair = %s AND ts >= %s AND ts <= %s
                GROUP BY ts 
                ORDER BY ts ASC
            """
        else:
            query = "SELECT ts, open, high, low, close, volume FROM ohlcv_1m WHERE pair = %s AND ts >= %s AND ts <= %s ORDER BY ts ASC"
            
        cur.execute(query, (pair_name, start_time, end_time))
        rows = cur.fetchall()
        if not rows: return []
        
        # Fast DB tuple to dict mapping without pandas overhead
        candles = [{'ts': r['ts'], 'open': r['open'], 'high': r['high'], 'low': r['low'], 'close': r['close'], 'volume': r['volume']} for r in rows]
        
        pair_trades = []
        pair_lc = str(pair_name).lower()
        for p in profiles_list:
            p_local = dict(p)
            allowed_pairs = set(str(x).lower() for x in (p_local.get("allowed_pairs") or []))
            if allowed_pairs and pair_lc not in allowed_pairs:
                continue
            pair_setup_map = p_local.get("pair_setup_map") or {}
            p_local["pair_setup"] = pair_setup_map.get(str(pair_name).lower(), {})
            res = _run_backtest_pair(p_local, pair_name, candles, start_time, end_time)
            if res["trades"]: pair_trades.extend(res["trades"])
        return pair_trades
    finally:
        conn.close()

def _simulate_portfolio(all_trades: list[dict], profile: dict) -> list[dict]:
    if not all_trades: return []
    sorted_trades = sorted(all_trades, key=lambda x: x["entry_time"])
    capital = float(profile.get("initial_capital_idr", 1000000.0))
    max_pos = int(profile.get("max_open_positions", 3))
    available_cash = capital
    active_trades = []
    final_trades = []

    def _release_closed(until_ts):
        nonlocal active_trades, available_cash
        still_open = []
        for a in active_trades:
            if a["exit_time"] <= until_ts:
                available_cash += float(a.get("amount_idr", 0.0)) + float(a.get("pnl_idr", 0.0))
            else:
                still_open.append(a)
        active_trades = still_open

    for t in sorted_trades:
        _release_closed(t["entry_time"])

        amount_idr = float(t.get("amount_idr", 0.0))
        if len(active_trades) < max_pos and amount_idr <= available_cash:
            active_trades.append(t)
            final_trades.append(t)
            available_cash -= amount_idr

    # Realize remaining open trades at the end for accounting completeness.
    _release_closed(datetime.max.replace(tzinfo=timezone.utc))

    return final_trades

def _save_result(pid, pair, stats, trades):
    with transaction() as cur:
        # Deep cast numpy types to avoid "schema np does not exist"
        def _c(val):
            if hasattr(val, 'item'): return val.item() # for numpy types
            return val

        cur.execute("""
            INSERT INTO backtest_results (
                profile_id, window_start, window_end, pair, total_trades, winning_trades, losing_trades, win_rate, 
                total_pnl_idr, total_pnl_pct, max_drawdown_idr, max_drawdown_pct,
                profit_factor, sharpe_ratio, initial_capital_idr, final_capital_idr, 
                avg_trade_duration_sec, avg_pnl_per_trade_idr, avg_pnl_per_trade_pct
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (
            int(pid), stats['window_start'], stats['window_end'], pair, 
            int(_c(stats['total_trades'])), int(_c(stats['winning_trades'])), int(_c(stats['losing_trades'])), float(_c(stats['win_rate'])),
            float(_c(stats['total_pnl_idr'])), float(_c(stats['total_pnl_pct'])), 
            float(_c(stats['max_drawdown_idr'])), float(_c(stats['max_drawdown_pct'])),
            float(_c(stats['profit_factor'])), float(_c(stats['sharpe_ratio'])), float(_c(stats['initial_capital_idr'])), 
            float(_c(stats['final_capital_idr'])), int(_c(stats['avg_trade_duration_sec'])),
            float(_c(stats['avg_pnl_per_trade_idr'])), float(_c(stats['avg_pnl_per_trade_pct']))
        ))
        rid = cur.fetchone()['id']
        for t in trades:
            cur.execute("""
                INSERT INTO backtest_trades (
                    result_id, pair, entry_time, exit_time, entry_price, exit_price, 
                    amount_idr, amount_crypto, pnl_idr, pnl_pct, exit_reason, 
                    buy_score, sell_score_at_exit, duration_seconds
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                rid, t['pair'], t['entry_time'], t['exit_time'], 
                float(_c(t['entry_price'])), float(_c(t['exit_price'])),
                float(_c(t['amount_idr'])), float(_c(t['amount_crypto'])),
                float(_c(t['pnl_idr'])), float(_c(t['pnl_pct'])), t['exit_reason'],
                float(_c(t['buy_score'])), 
                float(_c(t['sell_score_at_exit'])) if t['sell_score_at_exit'] is not None else None,
                int(_c(t['duration_seconds']))
            ))
        return rid


def _save_result_pair_setups(result_id: int, profile_id: int, trades: list[dict], pair_setup_map: dict[str, dict] | None) -> None:
    if not result_id or not trades or not pair_setup_map:
        return

    used_pairs = {str(t.get("pair", "")).lower() for t in trades if t.get("pair")}
    if not used_pairs:
        return

    with transaction() as cur:
        for pair in sorted(used_pairs):
            raw_cfg = pair_setup_map.get(pair)
            if not raw_cfg:
                continue

            cfg = dict(raw_cfg)
            source_ts = cfg.pop("analysis_ts", None)
            setup_json = json.dumps(cfg, ensure_ascii=True, sort_keys=True)
            cur.execute(
                """
                INSERT INTO backtest_result_pair_setups (
                    result_id, profile_id, pair, setup_json, source_analysis_ts
                ) VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (result_id, pair)
                DO UPDATE SET
                    setup_json = EXCLUDED.setup_json,
                    source_analysis_ts = EXCLUDED.source_analysis_ts,
                    created_at = NOW()
                """,
                (int(result_id), int(profile_id), pair, setup_json, source_ts),
            )


def _load_latest_coin_setup(pairs: list[str]) -> dict[str, dict]:
    pair_keys = [str(px).lower() for px in pairs if px]
    if not pair_keys:
        return {}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT ON (pair)
                lower(pair) AS pair,
                rec_buy_score_threshold,
                rec_sell_score_threshold,
                rec_opposing_max,
                rec_sell_block_threshold,
                rec_target_tp_pct,
                rec_target_sl_pct,
                rec_cooldown_seconds,
                rec_max_hold_minutes,
                rec_ema_trend_filter_enabled,
                rec_atr_max_pct,
                rec_max_position_idr,
                rec_dca_enabled,
                rec_dca_max_tiers,
                rec_rsi_oversold,
                rec_rsi_overbought,
                rec_bb_extreme_low,
                rec_bb_low,
                rec_bb_high,
                rec_bb_extreme_high,
                rec_stoch_k_oversold,
                rec_stoch_d_oversold,
                rec_stoch_k_overbought,
                rec_stoch_d_overbought,
                rec_williams_deep_oversold,
                rec_williams_oversold,
                rec_williams_overbought,
                rec_williams_deep_overbought,
                rec_slope_strong_up,
                rec_slope_up,
                rec_slope_down,
                rec_slope_strong_down,
                rec_roc_oversold,
                rec_roc_mild_down,
                rec_roc_mild_up,
                rec_roc_overbought,
                rec_ob_strong_bid,
                rec_ob_bid_dom,
                rec_ob_ask_dom,
                rec_ob_strong_ask,
                rec_volume_spike,
                rec_volume_high,
                rec_volume_low,
                rec_volume_dry,
                rec_obv_accumulate,
                rec_obv_rising,
                rec_obv_falling,
                rec_obv_distribute,
                analysis_ts
            FROM backtest_coin_analysis
            WHERE lower(pair) = ANY(%s)
            ORDER BY pair, analysis_ts DESC
            """,
            (pair_keys,),
        )
        rows = cur.fetchall()

        setup = {}
        for r in rows:
            setup[r["pair"]] = {
                "buy_score_threshold": float(r["rec_buy_score_threshold"] or 6.0),
                "sell_score_threshold": float(r["rec_sell_score_threshold"] or 6.0),
                "opposing_max": float(r["rec_opposing_max"] or 4.0),
                "sell_block_threshold": float(r["rec_sell_block_threshold"] or 5.0),
                "target_tp_pct": float(r["rec_target_tp_pct"] or 0.02),
                "target_sl_pct": float(r["rec_target_sl_pct"] or -0.02),
                "cooldown_seconds": int(r["rec_cooldown_seconds"] or 300),
                "max_hold_minutes": int(r["rec_max_hold_minutes"] or 1440),
                "ema_trend_filter_enabled": bool(r["rec_ema_trend_filter_enabled"]),
                "atr_max_pct": float(r["rec_atr_max_pct"] or 3.0),
                "max_position_idr": float(r["rec_max_position_idr"] or 100000.0),
                "dca_enabled": bool(r["rec_dca_enabled"]),
                "dca_max_tiers": int(r["rec_dca_max_tiers"] or 1),
                "rsi_oversold": float(r["rec_rsi_oversold"] or 35.0),
                "rsi_overbought": float(r["rec_rsi_overbought"] or 70.0),
                "bb_extreme_low": float(r["rec_bb_extreme_low"] or 0.10),
                "bb_low": float(r["rec_bb_low"] or 0.25),
                "bb_high": float(r["rec_bb_high"] or 0.75),
                "bb_extreme_high": float(r["rec_bb_extreme_high"] or 0.90),
                "stoch_k_oversold": float(r["rec_stoch_k_oversold"] or 20.0),
                "stoch_d_oversold": float(r["rec_stoch_d_oversold"] or 30.0),
                "stoch_k_overbought": float(r["rec_stoch_k_overbought"] or 80.0),
                "stoch_d_overbought": float(r["rec_stoch_d_overbought"] or 70.0),
                "williams_deep_oversold": float(r["rec_williams_deep_oversold"] or -85.0),
                "williams_oversold": float(r["rec_williams_oversold"] or -70.0),
                "williams_overbought": float(r["rec_williams_overbought"] or -30.0),
                "williams_deep_overbought": float(r["rec_williams_deep_overbought"] or -15.0),
                "slope_strong_up": float(r["rec_slope_strong_up"] or 0.005),
                "slope_up": float(r["rec_slope_up"] or 0.001),
                "slope_down": float(r["rec_slope_down"] or -0.001),
                "slope_strong_down": float(r["rec_slope_strong_down"] or -0.005),
                "roc_oversold": float(r["rec_roc_oversold"] or -3.0),
                "roc_mild_down": float(r["rec_roc_mild_down"] or -1.0),
                "roc_mild_up": float(r["rec_roc_mild_up"] or 1.0),
                "roc_overbought": float(r["rec_roc_overbought"] or 3.0),
                "ob_strong_bid": float(r["rec_ob_strong_bid"] or 65.0),
                "ob_bid_dom": float(r["rec_ob_bid_dom"] or 55.0),
                "ob_ask_dom": float(r["rec_ob_ask_dom"] or 45.0),
                "ob_strong_ask": float(r["rec_ob_strong_ask"] or 35.0),
                "volume_spike": float(r["rec_volume_spike"] or 2.0),
                "volume_high": float(r["rec_volume_high"] or 1.5),
                "volume_low": float(r["rec_volume_low"] or 0.6),
                "volume_dry": float(r["rec_volume_dry"] or 0.4),
                "obv_accumulate": float(r["rec_obv_accumulate"] or 5.0),
                "obv_rising": float(r["rec_obv_rising"] or 2.0),
                "obv_falling": float(r["rec_obv_falling"] or -2.0),
                "obv_distribute": float(r["rec_obv_distribute"] or -5.0),
                "analysis_ts": r["analysis_ts"],
            }
        return setup
    finally:
        conn.close()


def _load_profile_pair_scopes(profile_ids: list[int]) -> dict[int, set[str]]:
    ids = [int(x) for x in profile_ids if x is not None]
    if not ids:
        return {}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT profile_id, lower(pair) AS pair
            FROM backtest_profile_pair_scope
            WHERE profile_id = ANY(%s)
            """,
            (ids,),
        )
        rows = cur.fetchall()
        out: dict[int, set[str]] = {}
        for r in rows:
            pid = int(r["profile_id"])
            out.setdefault(pid, set()).add(str(r["pair"]))
        return out
    finally:
        conn.close()


def _backfill_profile_pair_scope_for_quant_profiles(profiles: list[dict]) -> None:
    if not profiles:
        return

    import re

    rows_to_add: list[tuple[int, str]] = []
    for p in profiles:
        name = str(p.get("profile_name") or "")
        m = re.match(r"^quant_([a-z0-9]+idr)(?:_|$)", name.lower())
        if not m:
            continue
        rows_to_add.append((int(p["id"]), m.group(1)))

    if not rows_to_add:
        return

    with transaction() as cur:
        for pid, pair in rows_to_add:
            cur.execute(
                """
                INSERT INTO backtest_profile_pair_scope (profile_id, pair)
                VALUES (%s, %s)
                ON CONFLICT (profile_id, pair) DO NOTHING
                """,
                (pid, pair),
            )

# ─────────────────────────────────────────────────────────────────────────────
# EVOLUTIONARY ENGINE: Self-Correction
# ─────────────────────────────────────────────────────────────────────────────

def tune_profile(profile_id: int, target_daily_pnl: float | None = None) -> str:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE id = %s", (profile_id,))
        p = cur.fetchone()
        if not p:
            return "missing_profile"
        cur.execute("SELECT * FROM backtest_results WHERE profile_id = %s ORDER BY created_at DESC LIMIT 1", (profile_id,))
        res = cur.fetchone()
        if not res:
            print(f"   ⚠️ [NO DATA] {p['profile_name']} has no backtest_result row yet; skip tuning.")
            return "no_data"

        # Early Exit check (Normalized to DAILY targets)
        total_pnl = res['total_pnl_idr']
        total_roi = res['total_pnl_pct']
        
        # Calculate actual days tested
        w_start = res['window_start']
        w_end = res['window_end']
        days_tested = (w_end - w_start).total_seconds() / 86400.0 if w_end and w_start and w_end > w_start else 90.0
        
        target_roi_daily = float(p.get('target_roi_pct', 0.0) or 0.0)
        target_pnl_daily = float(p.get('target_pnl_idr', 0.0) or 0.0)
        if target_daily_pnl is not None and target_daily_pnl > 0:
            target_pnl_daily = float(target_daily_pnl)
        elif target_pnl_daily <= 0:
            target_pnl_daily = DEFAULT_DAILY_TARGET_PNL_IDR

        avg_daily_pnl = total_pnl / days_tested
        avg_daily_roi = total_roi / days_tested
        
        reached_pnl = (target_pnl_daily > 0 and avg_daily_pnl >= target_pnl_daily)
        reached_roi = (target_roi_daily > 0 and avg_daily_roi >= target_roi_daily)

        if reached_pnl or reached_roi:
            status = f"Rp {avg_daily_pnl:,.0f}/hari" if reached_pnl else f"{avg_daily_roi:.2f}%/hari"
            print(f"   🏆 [DAILY TARGET REACHED] {p['profile_name']} ({status})")
            return "target_reached"

        logger.info("[tune] 🧠 Analyzing: Avg Rp {0:,.0f}/day (Target: {1:,.0f})".format(avg_daily_pnl, target_pnl_daily))

        # Mutation Logic
        new_p = dict(p)
        for k in ['id', 'created_at', 'updated_at']: new_p.pop(k, None)
        import re
        m = re.search(r'_v(\d+)$', p['profile_name'])
        v = int(m.group(1))+1 if m else 2
        new_p['profile_name'] = f"{p['profile_name'].split('_v')[0]}_v{v}"
        new_p['target_pnl_idr'] = target_pnl_daily
        
        changes = []
        param_diffs = []

        def _canonical_bool(v):
            if isinstance(v, str):
                s = v.strip().lower()
                if s in {"1", "true", "t", "yes", "y", "on"}:
                    return True
                if s in {"0", "false", "f", "no", "n", "off"}:
                    return False
            return v

        def _set_if_changed(key, value, reason):
            old_val = new_p.get(key)
            if _canonical_bool(old_val) != _canonical_bool(value):
                new_p[key] = value
                changes.append(reason)
                param_diffs.append((key, old_val, value, reason))
        
        # 1. Target-gap driven regime
        target_gap = target_pnl_daily - avg_daily_pnl
        win_rate = float(res.get('win_rate', 0.0) or 0.0)
        pf = float(res.get('profit_factor', 0.0) or 0.0)
        avg_pnl_pct = float(res.get('avg_pnl_per_trade_pct', 0.0) or 0.0)
        trades_per_day = float(res.get('total_trades', 0) or 0) / days_tested

        # Recovery mode: losing profile -> improve quality/risk first.
        if avg_daily_pnl <= 0 or win_rate < 0.35 or pf < 0.8:
            _set_if_changed('buy_threshold', round(min(8.5, float(p['buy_threshold']) + 0.7), 2),
                            'Recovery mode: stricter entry threshold')
            _set_if_changed('target_tp_pct', round(max(0.01, min(float(p['target_tp_pct']), 0.02)), 4),
                            'Recovery mode: lower TP to improve hit-rate')
            _set_if_changed('target_sl_pct', round(min(-0.01, float(p['target_sl_pct']) + 0.01), 4),
                            'Recovery mode: tighter SL to cap downside')
            _set_if_changed('max_hold_minutes', int(max(120, int(float(p.get('max_hold_minutes', 1440))) * 0.5)),
                            'Recovery mode: shorter holding window')
            _set_if_changed('ema_trend_filter_enabled', True,
                            'Recovery mode: enforce EMA trend filter')
            _set_if_changed('dca_enabled', False,
                            'Recovery mode: disable DCA while expectancy negative')
            _set_if_changed('cooldown_seconds', int(min(1800, max(300, int(float(p.get('cooldown_seconds', 300))) + 120))),
                            'Recovery mode: increase cooldown to reduce overtrading')

        # Growth mode: positive expectancy but still below target -> scale exposure gradually.
        elif target_gap > 0 and win_rate >= 0.5 and pf >= 1.2:
            current_cap = float(p.get('initial_capital_idr', 1000000.0) or 1000000.0)
            current_pos = float(p.get('max_position_idr', 100000.0) or 100000.0)
            cap_limit = current_cap * 0.35
            bumped_pos = round(min(cap_limit, max(current_pos + MIN_TRADE_IDR, current_pos * 1.2)), 2)
            _set_if_changed('max_position_idr', bumped_pos,
                            'Growth mode: increase position sizing toward daily target')
            _set_if_changed('buy_threshold', round(max(4.0, float(p['buy_threshold']) - 0.1), 2),
                            'Growth mode: slightly loosen threshold for more valid entries')
            _set_if_changed('target_tp_pct', round(min(0.06, float(p['target_tp_pct']) + 0.003), 4),
                            'Growth mode: modestly increase TP to lift payoff ratio')

        # 2. Overtrading guard
        if avg_pnl_pct < 0.2 and trades_per_day > 5.0:
            _set_if_changed('buy_threshold', round(min(8.5, float(new_p['buy_threshold']) + 0.3), 2),
                            'Overtrading guard: stricter entry quality gate')

        # 3. Undertrading guard (only when already profitable but missing target)
        if avg_daily_pnl > 0 and target_gap > 0:
            if trades_per_day < 1.0:
                _set_if_changed('buy_threshold', round(max(4.0, float(new_p['buy_threshold']) - 0.3), 2),
                                'Undertrading guard: increase signal frequency')
            elif trades_per_day < 2.0:
                _set_if_changed('buy_threshold', round(max(4.0, float(new_p['buy_threshold']) - 0.1), 2),
                                'Undertrading guard: mildly increase signal frequency')
            
        if not changes:
            print(f"   ℹ️ [NO CHANGE] {p['profile_name']} produced no parameter updates this generation.")
            return "no_change"

        print(f"   🧬 [MUTATION] {p['profile_name']} -> {new_p['profile_name']}")
        for key, old_val, new_val, reason in param_diffs:
            print(f"      - {key}: {old_val} -> {new_val} ({reason})")
        
        cols = ", ".join(new_p.keys())
        placeholders = ", ".join(["%s"] * len(new_p))
        cur.execute(f"INSERT INTO backtest_profiles ({cols}) VALUES ({placeholders})", list(new_p.values()))
        cur.execute("UPDATE backtest_profiles SET is_active = false WHERE id = %s", (profile_id,))
        conn.commit()
        return "mutated"
    finally:
        conn.close()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _build_coin_recommendation(
    *,
    q: float,
    p_hat: float,
    up_ratio: float,
    acf1: float,
    day_range: float,
    sigma_day: float,
    jump_1pct: float,
    edge_trade_raw: float,
    edge_trade_effective: float,
    trades_needed_full_cap: float,
    balance_idr: float,
    max_position_idr: float,
    max_open_positions: int,
    buy_threshold: float,
    tp_pct: float,
    sl_pct: float,
    cooldown_seconds: int,
    max_hold_minutes: int,
    atr_max_pct: float,
) -> dict:
    trend_bias = _clamp((2.0 * up_ratio) - 1.0, -1.0, 1.0)
    acf_norm = _clamp((acf1 + 0.35) / 0.70, 0.0, 1.0)
    noise_regime = _clamp(jump_1pct / 0.05, 0.0, 1.0)
    vol_regime = _clamp(day_range / 0.10, 0.0, 1.0)
    prob_conf = _clamp((p_hat - 0.5) / 0.22, -1.0, 1.0)
    edge_conf = _clamp(edge_trade_effective / 0.01, 0.0, 1.0)
    pressure = _clamp((trades_needed_full_cap - 3.0) / 12.0, 0.0, 1.0)

    # Gate thresholds are derived from probability confidence and operational pressure.
    buy_score_threshold = _clamp(
        6.1
        - (0.55 * edge_conf)
        + (0.35 * pressure)
        + (0.45 * (1.0 - q))
        + (0.20 * (1.0 - acf_norm)),
        5.0,
        7.3,
    )
    sell_score_threshold = _clamp(
        5.6
        + (0.40 * noise_regime)
        - (0.20 * prob_conf)
        + (0.25 * (1.0 - q)),
        5.0,
        7.1,
    )
    opposing_max = _clamp(
        3.8
        + (0.45 * noise_regime)
        + (0.25 * (1.0 - acf_norm))
        - (0.20 * edge_conf),
        3.3,
        4.9,
    )
    sell_block_threshold = _clamp(
        4.9
        + (0.45 * noise_regime)
        - (0.25 * trend_bias)
        + (0.20 * (1.0 - q)),
        4.4,
        6.3,
    )

    # Indicator thresholds adapt to trend persistence vs mean-reversion regime.
    mean_reversion = _clamp((0.55 * (1.0 - q)) + (0.45 * noise_regime), 0.0, 1.0)
    trend_strength = _clamp((0.45 * q) + (0.30 * acf_norm) + (0.25 * max(0.0, trend_bias)), 0.0, 1.0)

    rsi_oversold = _clamp(
        RSI_OVERSOLD_BASE + (6.0 * mean_reversion) - (2.0 * trend_strength),
        RSI_OVERSOLD_MIN,
        RSI_OVERSOLD_MAX,
    )
    rsi_overbought = _clamp(
        RSI_OVERBOUGHT_BASE + (2.0 * trend_strength) - (6.0 * mean_reversion),
        RSI_OVERBOUGHT_MIN,
        RSI_OVERBOUGHT_MAX,
    )

    bb_center_shift = _clamp(0.05 * trend_bias, -0.05, 0.05)
    bb_width = _clamp(0.30 + (0.06 * noise_regime) - (0.04 * trend_strength), 0.22, 0.38)
    bb_low = _clamp(0.50 - bb_width + bb_center_shift, 0.15, 0.40)
    bb_high = _clamp(0.50 + bb_width + bb_center_shift, 0.60, 0.85)
    bb_extreme_low = _clamp(bb_low - (0.12 + (0.03 * noise_regime)), 0.05, 0.22)
    bb_extreme_high = _clamp(bb_high + (0.12 + (0.03 * noise_regime)), 0.78, 0.95)

    stoch_k_oversold = _clamp(18.0 + (7.0 * mean_reversion), 15.0, 35.0)
    stoch_d_oversold = _clamp(stoch_k_oversold + 10.0, 22.0, 45.0)
    stoch_k_overbought = _clamp(82.0 - (7.0 * mean_reversion), 65.0, 85.0)
    stoch_d_overbought = _clamp(stoch_k_overbought - 10.0, 55.0, 78.0)

    williams_oversold = _clamp(-70.0 - (10.0 * mean_reversion), -88.0, -60.0)
    williams_deep_oversold = _clamp(williams_oversold - (10.0 + (3.0 * noise_regime)), -95.0, -72.0)
    williams_overbought = _clamp(-30.0 + (10.0 * mean_reversion), -40.0, -12.0)
    williams_deep_overbought = _clamp(williams_overbought + (10.0 + (3.0 * noise_regime)), -25.0, -5.0)

    slope_base = _clamp(0.0008 + (0.06 * sigma_day), 0.0008, 0.0050)
    slope_up = slope_base
    slope_strong_up = _clamp(slope_base * 3.0, 0.0025, 0.0120)
    slope_down = -slope_base
    slope_strong_down = -slope_strong_up

    roc_abs = _clamp((sigma_day * 100.0) * (0.55 + (0.35 * noise_regime)), 1.2, 5.0)
    roc_mild = _clamp(roc_abs * 0.35, 0.8, 2.0)

    bid_shift = _clamp(4.0 * trend_strength - 3.0 * noise_regime, -3.0, 4.0)
    ob_strong_bid = _clamp(65.0 + bid_shift, 60.0, 72.0)
    ob_bid_dom = _clamp(55.0 + (bid_shift * 0.8), 50.0, 62.0)
    ob_ask_dom = _clamp(45.0 - (bid_shift * 0.8), 38.0, 50.0)
    ob_strong_ask = _clamp(35.0 - bid_shift, 28.0, 40.0)

    volume_spike = _clamp(1.7 + (0.7 * noise_regime) + (0.2 * vol_regime), 1.5, 2.8)
    volume_high = _clamp(1.25 + (0.30 * vol_regime), 1.2, 1.8)
    volume_low = _clamp(0.75 - (0.15 * noise_regime), 0.45, 0.8)
    volume_dry = _clamp(volume_low - (0.20 + (0.05 * noise_regime)), 0.25, 0.65)

    obv_strong = _clamp(3.0 + (4.0 * trend_strength) + (1.5 * edge_conf), 3.0, 9.0)
    obv_mild = _clamp(obv_strong * 0.4, 1.5, 4.0)

    return {
        "initial_capital_idr": float(balance_idr),
        "min_position_idr": float(MIN_TRADE_IDR),
        "max_position_idr": round(max_position_idr, 2),
        "max_open_positions": int(max_open_positions),
        "buy_threshold": round(buy_threshold, 2),
        "sell_block_threshold": round(sell_block_threshold, 3),
        "buy_score_threshold": round(buy_score_threshold, 3),
        "sell_score_threshold": round(sell_score_threshold, 3),
        "opposing_max": round(opposing_max, 3),
        "target_tp_pct": round(tp_pct, 4),
        "target_sl_pct": round(sl_pct, 4),
        "cooldown_seconds": int(cooldown_seconds),
        "max_hold_minutes": int(max_hold_minutes),
        "ema_trend_filter_enabled": bool((prob_conf >= -0.2) or (trend_strength >= 0.45)),
        "atr_max_pct": round(atr_max_pct, 3),
        "dca_enabled": bool((edge_trade_raw > 0.0) and (p_hat >= 0.56) and (noise_regime < 0.35)),
        "dca_max_tiers": int(_clamp(1.0 + (1.5 * edge_conf) - (1.0 * noise_regime), 1.0, 3.0)),
        "rsi_oversold": round(rsi_oversold, 3),
        "rsi_overbought": round(rsi_overbought, 3),
        "bb_extreme_low": round(bb_extreme_low, 3),
        "bb_low": round(bb_low, 3),
        "bb_high": round(bb_high, 3),
        "bb_extreme_high": round(bb_extreme_high, 3),
        "stoch_k_oversold": round(stoch_k_oversold, 3),
        "stoch_d_oversold": round(stoch_d_oversold, 3),
        "stoch_k_overbought": round(stoch_k_overbought, 3),
        "stoch_d_overbought": round(stoch_d_overbought, 3),
        "williams_deep_oversold": round(williams_deep_oversold, 3),
        "williams_oversold": round(williams_oversold, 3),
        "williams_overbought": round(williams_overbought, 3),
        "williams_deep_overbought": round(williams_deep_overbought, 3),
        "slope_strong_up": round(slope_strong_up, 6),
        "slope_up": round(slope_up, 6),
        "slope_down": round(slope_down, 6),
        "slope_strong_down": round(slope_strong_down, 6),
        "roc_oversold": round(-roc_abs, 3),
        "roc_mild_down": round(-roc_mild, 3),
        "roc_mild_up": round(roc_mild, 3),
        "roc_overbought": round(roc_abs, 3),
        "ob_strong_bid": round(ob_strong_bid, 3),
        "ob_bid_dom": round(ob_bid_dom, 3),
        "ob_ask_dom": round(ob_ask_dom, 3),
        "ob_strong_ask": round(ob_strong_ask, 3),
        "volume_spike": round(volume_spike, 3),
        "volume_high": round(volume_high, 3),
        "volume_low": round(volume_low, 3),
        "volume_dry": round(volume_dry, 3),
        "obv_accumulate": round(obv_strong, 3),
        "obv_rising": round(obv_mild, 3),
        "obv_falling": round(-obv_mild, 3),
        "obv_distribute": round(-obv_strong, 3),
    }


def _apply_backtest_optimized_overrides(recommendation: dict, balance_idr: float, target_daily_pnl: float) -> dict:
    """
    Mode khusus backtesting (bukan parity bot live).
    Fokus: meningkatkan frekuensi sinyal terkontrol untuk target harian kecil (mis. 1k/100k).
    """
    rec = dict(recommendation)

    rec["buy_threshold"] = round(_clamp(float(rec.get("buy_threshold", 6.0)) - 0.8, 4.0, 7.0), 2)
    rec["buy_score_threshold"] = round(_clamp(float(rec.get("buy_score_threshold", 6.0)) - 0.5, 5.0, 7.0), 3)
    rec["sell_score_threshold"] = round(_clamp(float(rec.get("sell_score_threshold", 6.0)) - 0.2, 4.8, 6.8), 3)
    rec["sell_block_threshold"] = round(_clamp(float(rec.get("sell_block_threshold", 5.0)) - 0.4, 4.2, 6.0), 3)
    rec["opposing_max"] = round(_clamp(float(rec.get("opposing_max", 4.0)) + 0.3, 3.5, 5.0), 3)

    # TP lebih realistis untuk hit-rate, SL dipersempit agar drawdown tetap terkendali.
    rec["target_tp_pct"] = round(_clamp(float(rec.get("target_tp_pct", 0.02)), 0.010, 0.020), 4)
    rec["target_sl_pct"] = round(-_clamp(abs(float(rec.get("target_sl_pct", -0.015))), 0.008, 0.015), 4)

    rec["cooldown_seconds"] = int(_clamp(float(rec.get("cooldown_seconds", 300)) * 0.5, 90, 600))
    rec["max_hold_minutes"] = int(_clamp(float(rec.get("max_hold_minutes", 240)), 45, 240))
    rec["ema_trend_filter_enabled"] = False
    rec["atr_max_pct"] = round(_clamp(float(rec.get("atr_max_pct", 3.0)) + 0.7, 2.5, 5.0), 3)

    # Position sizing khusus target 1k/day dari 100k balance.
    est_trades = max(2, min(12, int(round(target_daily_pnl / max(1.0, balance_idr * 0.0035)))))
    pos_target = target_daily_pnl / max(1e-9, est_trades * 0.0035)
    rec["max_position_idr"] = round(_clamp(pos_target, MIN_TRADE_IDR, balance_idr * 0.30), 2)
    rec["max_open_positions"] = 2 if rec["max_position_idr"] <= (0.2 * balance_idr) else 1

    rec["dca_enabled"] = False
    rec["dca_max_tiers"] = 1
    return rec


def _upsert_coin_analysis(
    window_start: datetime,
    window_end: datetime,
    target_daily_pnl: float,
    balance_idr: float,
    rows: list[dict],
) -> None:
    if not rows:
        return

    def _py(v):
        if hasattr(v, "item"):
            return v.item()
        return v

    with transaction() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO backtest_coin_analysis (
                    window_start, window_end, pair, n_candles, n_days,
                    mu_1m, sigma_1m, mad_1m, up_ratio, jump_1pct_ratio, acf1,
                    avg_day_range, sigma_day, mu_day, sharpe_day_like, momentum_quality,
                    target_daily_pnl, balance_idr,
                    fee_rt, p_hat, breakeven_p, win_net, loss_net, edge_trade, trades_needed_full_cap,
                    edge_trade_raw, edge_trade_effective,
                    rec_buy_threshold, rec_sell_block_threshold, rec_target_tp_pct, rec_target_sl_pct,
                    rec_buy_score_threshold, rec_sell_score_threshold, rec_opposing_max,
                    rec_cooldown_seconds, rec_max_hold_minutes, rec_ema_trend_filter_enabled, rec_atr_max_pct,
                    rec_max_position_idr, rec_max_open_positions, rec_dca_enabled, rec_dca_max_tiers,
                    rec_rsi_oversold, rec_rsi_overbought,
                    rec_bb_extreme_low, rec_bb_low, rec_bb_high, rec_bb_extreme_high,
                    rec_stoch_k_oversold, rec_stoch_d_oversold, rec_stoch_k_overbought, rec_stoch_d_overbought,
                    rec_williams_deep_oversold, rec_williams_oversold, rec_williams_overbought, rec_williams_deep_overbought,
                    rec_slope_strong_up, rec_slope_up, rec_slope_down, rec_slope_strong_down,
                    rec_roc_oversold, rec_roc_mild_down, rec_roc_mild_up, rec_roc_overbought,
                    rec_ob_strong_bid, rec_ob_bid_dom, rec_ob_ask_dom, rec_ob_strong_ask,
                    rec_volume_spike, rec_volume_high, rec_volume_low, rec_volume_dry,
                    rec_obv_accumulate, rec_obv_rising, rec_obv_falling, rec_obv_distribute,
                    analysis_ts
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    NOW()
                )
                ON CONFLICT (pair, window_start, window_end, target_daily_pnl, balance_idr)
                DO UPDATE SET
                    n_candles = EXCLUDED.n_candles,
                    n_days = EXCLUDED.n_days,
                    mu_1m = EXCLUDED.mu_1m,
                    sigma_1m = EXCLUDED.sigma_1m,
                    mad_1m = EXCLUDED.mad_1m,
                    up_ratio = EXCLUDED.up_ratio,
                    jump_1pct_ratio = EXCLUDED.jump_1pct_ratio,
                    acf1 = EXCLUDED.acf1,
                    avg_day_range = EXCLUDED.avg_day_range,
                    sigma_day = EXCLUDED.sigma_day,
                    mu_day = EXCLUDED.mu_day,
                    sharpe_day_like = EXCLUDED.sharpe_day_like,
                    momentum_quality = EXCLUDED.momentum_quality,
                    fee_rt = EXCLUDED.fee_rt,
                    p_hat = EXCLUDED.p_hat,
                    breakeven_p = EXCLUDED.breakeven_p,
                    win_net = EXCLUDED.win_net,
                    loss_net = EXCLUDED.loss_net,
                    edge_trade_raw = EXCLUDED.edge_trade_raw,
                    edge_trade = EXCLUDED.edge_trade,
                    edge_trade_effective = EXCLUDED.edge_trade_effective,
                    trades_needed_full_cap = EXCLUDED.trades_needed_full_cap,
                    rec_buy_threshold = EXCLUDED.rec_buy_threshold,
                    rec_sell_block_threshold = EXCLUDED.rec_sell_block_threshold,
                    rec_target_tp_pct = EXCLUDED.rec_target_tp_pct,
                    rec_target_sl_pct = EXCLUDED.rec_target_sl_pct,
                    rec_buy_score_threshold = EXCLUDED.rec_buy_score_threshold,
                    rec_sell_score_threshold = EXCLUDED.rec_sell_score_threshold,
                    rec_opposing_max = EXCLUDED.rec_opposing_max,
                    rec_cooldown_seconds = EXCLUDED.rec_cooldown_seconds,
                    rec_max_hold_minutes = EXCLUDED.rec_max_hold_minutes,
                    rec_ema_trend_filter_enabled = EXCLUDED.rec_ema_trend_filter_enabled,
                    rec_atr_max_pct = EXCLUDED.rec_atr_max_pct,
                    rec_max_position_idr = EXCLUDED.rec_max_position_idr,
                    rec_max_open_positions = EXCLUDED.rec_max_open_positions,
                    rec_dca_enabled = EXCLUDED.rec_dca_enabled,
                    rec_dca_max_tiers = EXCLUDED.rec_dca_max_tiers,
                    rec_rsi_oversold = EXCLUDED.rec_rsi_oversold,
                    rec_rsi_overbought = EXCLUDED.rec_rsi_overbought,
                    rec_bb_extreme_low = EXCLUDED.rec_bb_extreme_low,
                    rec_bb_low = EXCLUDED.rec_bb_low,
                    rec_bb_high = EXCLUDED.rec_bb_high,
                    rec_bb_extreme_high = EXCLUDED.rec_bb_extreme_high,
                    rec_stoch_k_oversold = EXCLUDED.rec_stoch_k_oversold,
                    rec_stoch_d_oversold = EXCLUDED.rec_stoch_d_oversold,
                    rec_stoch_k_overbought = EXCLUDED.rec_stoch_k_overbought,
                    rec_stoch_d_overbought = EXCLUDED.rec_stoch_d_overbought,
                    rec_williams_deep_oversold = EXCLUDED.rec_williams_deep_oversold,
                    rec_williams_oversold = EXCLUDED.rec_williams_oversold,
                    rec_williams_overbought = EXCLUDED.rec_williams_overbought,
                    rec_williams_deep_overbought = EXCLUDED.rec_williams_deep_overbought,
                    rec_slope_strong_up = EXCLUDED.rec_slope_strong_up,
                    rec_slope_up = EXCLUDED.rec_slope_up,
                    rec_slope_down = EXCLUDED.rec_slope_down,
                    rec_slope_strong_down = EXCLUDED.rec_slope_strong_down,
                    rec_roc_oversold = EXCLUDED.rec_roc_oversold,
                    rec_roc_mild_down = EXCLUDED.rec_roc_mild_down,
                    rec_roc_mild_up = EXCLUDED.rec_roc_mild_up,
                    rec_roc_overbought = EXCLUDED.rec_roc_overbought,
                    rec_ob_strong_bid = EXCLUDED.rec_ob_strong_bid,
                    rec_ob_bid_dom = EXCLUDED.rec_ob_bid_dom,
                    rec_ob_ask_dom = EXCLUDED.rec_ob_ask_dom,
                    rec_ob_strong_ask = EXCLUDED.rec_ob_strong_ask,
                    rec_volume_spike = EXCLUDED.rec_volume_spike,
                    rec_volume_high = EXCLUDED.rec_volume_high,
                    rec_volume_low = EXCLUDED.rec_volume_low,
                    rec_volume_dry = EXCLUDED.rec_volume_dry,
                    rec_obv_accumulate = EXCLUDED.rec_obv_accumulate,
                    rec_obv_rising = EXCLUDED.rec_obv_rising,
                    rec_obv_falling = EXCLUDED.rec_obv_falling,
                    rec_obv_distribute = EXCLUDED.rec_obv_distribute,
                    analysis_ts = NOW()
                """,
                (
                    window_start,
                    window_end,
                    r["pair"],
                    r["n"],
                    r["n_days"],
                    _py(r["mu_1m"]),
                    _py(r["sigma_1m"]),
                    _py(r["mad_1m"]),
                    _py(r["up_ratio"]),
                    _py(r["jump_1pct"]),
                    _py(r["acf1"]),
                    _py(r["avg_day_range"]),
                    _py(r["sigma_day"]),
                    _py(r["mu_day"]),
                    _py(r["sharpe_day_like"]),
                    _py(r["momentum_quality"]),
                    _py(target_daily_pnl),
                    _py(balance_idr),
                    _py(r["math"]["fee_rt"]),
                    _py(r["math"]["p_hat"]),
                    _py(r["math"]["breakeven_p"]),
                    _py(r["math"]["win_net"]),
                    _py(r["math"]["loss_net"]),
                    _py(r["math"]["edge_trade"]),
                    _py(r["math"]["trades_needed_full_cap"]),
                    _py(r["math"]["edge_trade_raw"]),
                    _py(r["math"]["edge_trade_effective"]),
                    _py(r["recommendation"]["buy_threshold"]),
                    _py(r["recommendation"]["sell_block_threshold"]),
                    _py(r["recommendation"]["target_tp_pct"]),
                    _py(r["recommendation"]["target_sl_pct"]),
                    _py(r["recommendation"]["buy_score_threshold"]),
                    _py(r["recommendation"]["sell_score_threshold"]),
                    _py(r["recommendation"]["opposing_max"]),
                    _py(r["recommendation"]["cooldown_seconds"]),
                    _py(r["recommendation"]["max_hold_minutes"]),
                    _py(r["recommendation"]["ema_trend_filter_enabled"]),
                    _py(r["recommendation"]["atr_max_pct"]),
                    _py(r["recommendation"]["max_position_idr"]),
                    _py(r["recommendation"]["max_open_positions"]),
                    _py(r["recommendation"]["dca_enabled"]),
                    _py(r["recommendation"]["dca_max_tiers"]),
                    _py(r["recommendation"]["rsi_oversold"]),
                    _py(r["recommendation"]["rsi_overbought"]),
                    _py(r["recommendation"]["bb_extreme_low"]),
                    _py(r["recommendation"]["bb_low"]),
                    _py(r["recommendation"]["bb_high"]),
                    _py(r["recommendation"]["bb_extreme_high"]),
                    _py(r["recommendation"]["stoch_k_oversold"]),
                    _py(r["recommendation"]["stoch_d_oversold"]),
                    _py(r["recommendation"]["stoch_k_overbought"]),
                    _py(r["recommendation"]["stoch_d_overbought"]),
                    _py(r["recommendation"]["williams_deep_oversold"]),
                    _py(r["recommendation"]["williams_oversold"]),
                    _py(r["recommendation"]["williams_overbought"]),
                    _py(r["recommendation"]["williams_deep_overbought"]),
                    _py(r["recommendation"]["slope_strong_up"]),
                    _py(r["recommendation"]["slope_up"]),
                    _py(r["recommendation"]["slope_down"]),
                    _py(r["recommendation"]["slope_strong_down"]),
                    _py(r["recommendation"]["roc_oversold"]),
                    _py(r["recommendation"]["roc_mild_down"]),
                    _py(r["recommendation"]["roc_mild_up"]),
                    _py(r["recommendation"]["roc_overbought"]),
                    _py(r["recommendation"]["ob_strong_bid"]),
                    _py(r["recommendation"]["ob_bid_dom"]),
                    _py(r["recommendation"]["ob_ask_dom"]),
                    _py(r["recommendation"]["ob_strong_ask"]),
                    _py(r["recommendation"]["volume_spike"]),
                    _py(r["recommendation"]["volume_high"]),
                    _py(r["recommendation"]["volume_low"]),
                    _py(r["recommendation"]["volume_dry"]),
                    _py(r["recommendation"]["obv_accumulate"]),
                    _py(r["recommendation"]["obv_rising"]),
                    _py(r["recommendation"]["obv_falling"]),
                    _py(r["recommendation"]["obv_distribute"]),
                ),
            )


def _create_profile_from_analysis(
    *,
    pair: str,
    recommendation: dict,
    target_daily_pnl: float,
    profile_name: str | None = None,
    timeframe_minutes: int = 1,
    strategy_mode: str = "scalping",
    backtest_optimized: bool = False,
    activate_new_profile: bool = False,
    activate_only_new: bool = False,
) -> dict:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    effective_name = profile_name or f"quant_{pair}_{ts}"

    payload = {
        "profile_name": effective_name,
        "initial_capital_idr": float(recommendation["initial_capital_idr"]),
        "min_position_idr": float(recommendation["min_position_idr"]),
        "max_position_idr": float(recommendation["max_position_idr"]),
        "max_open_positions": int(recommendation["max_open_positions"]),
        "timeframe_minutes": int(max(1, timeframe_minutes)),
        "strategy_mode": "backtest_optimized" if backtest_optimized else str(strategy_mode),
        "buy_threshold": float(recommendation["buy_threshold"]),
        "sell_block_threshold": float(recommendation["sell_block_threshold"]),
        "target_tp_pct": float(recommendation["target_tp_pct"]),
        "target_sl_pct": float(recommendation["target_sl_pct"]),
        "cooldown_seconds": int(recommendation["cooldown_seconds"]),
        "max_hold_minutes": int(recommendation["max_hold_minutes"]),
        "ema_trend_filter_enabled": bool(recommendation["ema_trend_filter_enabled"]),
        "atr_max_pct": float(recommendation["atr_max_pct"]),
        "dca_enabled": bool(recommendation["dca_enabled"]),
        "dca_max_tiers": int(recommendation["dca_max_tiers"]),
        "target_pnl_idr": float(target_daily_pnl),
        "target_roi_pct": 0.0,
        "is_active": bool(activate_new_profile),
    }

    with transaction() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'backtest_profiles'
            """
        )
        existing_cols = {r["column_name"] for r in cur.fetchall()}

        data = {k: v for k, v in payload.items() if k in existing_cols}
        cols = list(data.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        columns_sql = ", ".join(cols)

        if activate_new_profile and activate_only_new and "is_active" in existing_cols:
            cur.execute("UPDATE backtest_profiles SET is_active = false WHERE is_active = true")

        cur.execute(
            f"INSERT INTO backtest_profiles ({columns_sql}) VALUES ({placeholders}) RETURNING id",
            [data[c] for c in cols],
        )
        new_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO backtest_profile_pair_scope (profile_id, pair)
            VALUES (%s, %s)
            ON CONFLICT (profile_id, pair) DO NOTHING
            """,
            (int(new_id), str(pair).lower()),
        )
        cur.execute("SELECT * FROM backtest_profiles WHERE id = %s", (new_id,))
        return cur.fetchone()


def _sort_auto_selection_candidates(rows: list[dict]) -> list[dict]:
    """
    Urutkan kandidat pair secara pragmatis untuk auto-selection:
    1) edge_raw paling baik (least-negative / positive),
    2) momentum quality,
    3) likuiditas notional harian.
    """
    return sorted(
        rows,
        key=lambda x: (
            float(x.get("math", {}).get("edge_trade_raw", -1e9) or -1e9),
            float(x.get("momentum_quality", 0.0) or 0.0),
            float(x.get("avg_day_notional_window", 0.0) or 0.0),
            float(x.get("up_ratio", 0.0) or 0.0),
        ),
        reverse=True,
    )


def analyze_ohlcv_target_setup(
    target_daily_pnl: float = DEFAULT_DAILY_TARGET_PNL_IDR,
    balance_idr: float = 100000.0,
    days: int = OHLCV_DAYS,
    pairs_filter: str | None = None,
    min_liquidity_24h_idr: float = DEFAULT_MIN_LIQUIDITY_24H_IDR,
    min_price_idr: float = DEFAULT_MIN_PRICE_IDR,
) -> dict:
    """
    Analisis statistik murni dari ohlcv_1m untuk menyarankan setup parameter
    teknikal di backtest_profiles menuju target profit harian.
    """
    end = _get_latest_ohlcv_ts()
    if end is None:
        print("❌ Tidak ada data ohlcv_1m untuk dianalisis.")
        return {}
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    filter_set = {p.strip().lower() for p in pairs_filter.split(",") if p.strip()} if pairs_filter else None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            WITH px AS (
                SELECT
                    pair,
                    ts,
                    close::double precision AS close,
                    high::double precision AS high,
                    low::double precision AS low,
                    LAG(close::double precision) OVER (PARTITION BY pair ORDER BY ts) AS prev_close
                FROM ohlcv_1m
                WHERE ts >= %s
            ), ret AS (
                SELECT
                    pair,
                    ts,
                    CASE
                        WHEN prev_close IS NULL OR prev_close = 0 THEN NULL
                        ELSE (close / prev_close) - 1
                    END AS r,
                    high,
                    low,
                    close
                FROM px
            ), ret_lag AS (
                SELECT
                    pair,
                    ts,
                    r,
                    LAG(r) OVER (PARTITION BY pair ORDER BY ts) AS r_l1
                FROM ret
            ), stats AS (
                SELECT
                    pair,
                    COUNT(r) AS n,
                    AVG(r) AS mu_1m,
                    STDDEV_POP(r) AS sigma_1m,
                    AVG(ABS(r)) AS mad_1m,
                    AVG(CASE WHEN r > 0 THEN 1.0 ELSE 0.0 END) AS up_ratio,
                    AVG(CASE WHEN ABS(r) > 0.01 THEN 1.0 ELSE 0.0 END) AS jump_1pct_ratio,
                    CORR(r, r_l1) AS acf1
                FROM ret_lag
                WHERE r IS NOT NULL
                GROUP BY pair
            ), day_bar AS (
                SELECT
                    pair,
                    date_trunc('day', ts) AS d,
                    MAX(high::double precision) AS hi,
                    MIN(low::double precision) AS lo,
                    AVG(close::double precision) AS c
                FROM ohlcv_1m
                WHERE ts >= %s
                GROUP BY pair, date_trunc('day', ts)
            ), day_stats AS (
                SELECT
                    pair,
                    COUNT(*) AS n_days,
                    AVG(CASE WHEN c > 0 THEN (hi - lo) / c ELSE NULL END) AS avg_day_range
                FROM day_bar
                GROUP BY pair
            ), liq AS (
                SELECT
                    pair,
                    AVG(day_notional) AS avg_day_notional_window
                FROM (
                    SELECT
                        pair,
                        date_trunc('day', ts) AS d,
                        SUM(close::double precision * volume::double precision) AS day_notional
                    FROM ohlcv_1m
                    WHERE ts >= %s AND ts <= %s
                    GROUP BY pair, date_trunc('day', ts)
                ) q
                GROUP BY pair
            ), px_guard AS (
                SELECT
                    pair,
                    AVG(close::double precision) AS avg_close_window,
                    MAX(ts) AS last_ts
                FROM ohlcv_1m
                WHERE ts >= %s AND ts <= %s
                GROUP BY pair
            )
            SELECT
                s.pair,
                s.n,
                s.mu_1m,
                s.sigma_1m,
                s.mad_1m,
                s.up_ratio,
                s.jump_1pct_ratio,
                s.acf1,
                d.n_days,
                d.avg_day_range,
                                l.avg_day_notional_window,
                                g.avg_close_window
            FROM stats s
            JOIN day_stats d USING (pair)
            JOIN liq l USING (pair)
                        JOIN px_guard g USING (pair)
                        WHERE s.n > 10000
                            AND d.n_days >= 10
                            AND COALESCE(l.avg_day_notional_window, 0) >= %s
                            AND (%s <= 0 OR COALESCE(g.avg_close_window, 0) >= %s)
            ORDER BY s.pair ASC
            """,
                        (
                                start,
                                start,
                                start,
                                end,
                                start,
                                end,
                                float(min_liquidity_24h_idr),
                                float(min_price_idr),
                                float(min_price_idr),
                        ),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("❌ Tidak ada data ohlcv_1m pada window analisis.")
        return {}

    universe = []
    for r in rows:
        pair = str(r["pair"])
        if filter_set and pair.lower() not in filter_set:
            continue

        sigma_1m = float(r["sigma_1m"] or 0.0)
        mad_1m = float(r["mad_1m"] or 0.0)
        mu_1m = float(r["mu_1m"] or 0.0)
        up_ratio = float(r["up_ratio"] or 0.5)
        acf1 = float(r["acf1"] or 0.0)
        day_range = float(r["avg_day_range"] or 0.0)
        jump_1pct = float(r["jump_1pct_ratio"] or 0.0)

        sigma_day = sigma_1m * _np.sqrt(1440.0)
        mu_day = mu_1m * 1440.0
        sharpe_day_like = (mu_day / sigma_day) if sigma_day > 0 else 0.0

        # Momentum quality: persistence + directional bias + healthy range - jump noise.
        drift_bias = (2.0 * up_ratio) - 1.0
        q_persist = _clamp((acf1 + 0.05) / 0.15, 0.0, 1.0)
        q_drift = _clamp((drift_bias + 0.10) / 0.25, 0.0, 1.0)
        q_range = _clamp(day_range / 0.08, 0.0, 1.0)
        q_noise = _clamp(jump_1pct / 0.04, 0.0, 1.0)
        momentum_quality = (0.40 * q_persist) + (0.25 * q_drift) + (0.25 * q_range) - (0.10 * q_noise)
        momentum_quality = _clamp(momentum_quality, 0.0, 1.0)

        universe.append(
            {
                "pair": pair,
                "n": int(r["n"]),
                "n_days": int(r["n_days"]),
                "avg_day_notional_window": float(r["avg_day_notional_window"] or 0.0),
                "avg_close_window": float(r["avg_close_window"] or 0.0),
                "mu_1m": mu_1m,
                "sigma_1m": sigma_1m,
                "mad_1m": mad_1m,
                "up_ratio": up_ratio,
                "acf1": acf1,
                "jump_1pct": jump_1pct,
                "avg_day_range": day_range,
                "sigma_day": sigma_day,
                "mu_day": mu_day,
                "sharpe_day_like": sharpe_day_like,
                "momentum_quality": momentum_quality,
            }
        )

    if not universe:
        print("❌ Pair hasil filter kosong. Cek --pairs terhadap symbol di DB.")
        return {}

    ranked = sorted(
        universe,
        key=lambda x: (
            x["momentum_quality"],
            x["sharpe_day_like"],
            x["up_ratio"],
        ),
        reverse=True,
    )
    top_k = ranked[: min(5, len(ranked))]

    fee_buy = 0.002111
    fee_sell = 0.004211
    slippage_in = 0.001
    slippage_out = 0.001
    fee_rt = fee_buy + fee_sell + slippage_in + slippage_out
    per_coin = []
    for c in ranked:
        q = float(c["momentum_quality"])
        day_range = float(c["avg_day_range"])
        sigma_day = float(c["sigma_day"])
        up_ratio = float(c["up_ratio"])
        acf1 = float(c["acf1"])

        tp_pct = _clamp(max(3.0 * fee_rt, 0.35 * day_range), 0.018, 0.06)
        sl_abs = _clamp(max(2.0 * fee_rt, 0.45 * tp_pct), 0.010, 0.030)
        sl_pct = -sl_abs

        p_hat = 0.5 + (0.30 * acf1) + (0.20 * ((2.0 * up_ratio) - 1.0)) + (0.10 * (q - 0.5))
        p_hat = _clamp(p_hat, 0.35, 0.72)

        win_net = tp_pct - fee_rt
        loss_net = sl_abs + fee_rt
        breakeven_p = loss_net / (win_net + loss_net) if (win_net + loss_net) > 0 else 1.0
        edge_trade_raw = (p_hat * win_net) - ((1.0 - p_hat) * loss_net)
        edge_trade = edge_trade_raw
        if edge_trade <= 0:
            edge_trade = 0.0035
        edge_trade_effective = edge_trade

        trades_needed_full_cap = max(0.0, target_daily_pnl / max(1.0, balance_idr * edge_trade))
        buy_threshold = _clamp(7.0 - (3.2 * q), 3.8, 7.5)
        cooldown_seconds = int(_clamp(900 - (600 * q), 240, 900))
        atr_max_pct = _clamp(day_range * 100.0 * 0.45, 2.0, 4.5)
        max_hold_minutes = int(_clamp((tp_pct / max(sigma_day, 1e-6)) * 1440.0 * 0.6, 120, 1440))

        target_trades_day = int(_clamp(_np.ceil(trades_needed_full_cap), 1, 12))
        needed_position = target_daily_pnl / max(1e-9, target_trades_day * edge_trade)
        max_position_idr = float(_clamp(needed_position, MIN_TRADE_IDR, balance_idr))
        max_open_positions = int(1 if max_position_idr >= (0.6 * balance_idr) else 2)

        recommendation = _build_coin_recommendation(
            q=q,
            p_hat=p_hat,
            up_ratio=up_ratio,
            acf1=acf1,
            day_range=day_range,
            sigma_day=sigma_day,
            jump_1pct=jump_1pct,
            edge_trade_raw=edge_trade_raw,
            edge_trade_effective=edge_trade_effective,
            trades_needed_full_cap=trades_needed_full_cap,
            balance_idr=balance_idr,
            max_position_idr=max_position_idr,
            max_open_positions=max_open_positions,
            buy_threshold=buy_threshold,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            cooldown_seconds=cooldown_seconds,
            max_hold_minutes=max_hold_minutes,
            atr_max_pct=atr_max_pct,
        )

        c["recommendation"] = recommendation
        c["math"] = {
            "fee_rt": fee_rt,
            "p_hat": p_hat,
            "breakeven_p": breakeven_p,
            "win_net": win_net,
            "loss_net": loss_net,
            "edge_trade_raw": edge_trade_raw,
            "edge_trade": edge_trade,
            "edge_trade_effective": edge_trade_effective,
            "trades_needed_full_cap": trades_needed_full_cap,
        }
        per_coin.append(c)

    top_k = per_coin[: min(5, len(per_coin))]
    best = top_k[0]
    recommendation = best["recommendation"]

    print("\n📊 OHLCV_1M QUANT ANALYSIS (DATA-DRIVEN)")
    print(
        f"Window: {days} hari | Pair dianalisis: {len(universe)} | "
        f"Target: Rp {target_daily_pnl:,.0f}/hari | Balance: Rp {balance_idr:,.0f} | "
        f"Min Avg Daily Notional: Rp {float(min_liquidity_24h_idr):,.0f}"
    )
    if float(min_price_idr) > 0:
        print(f"Min Price filter aktif: Rp {float(min_price_idr):,.0f}")
    print("\nTop momentum-quality pairs:")
    for i, p in enumerate(top_k, start=1):
        print(
            f" {i}. {p['pair']:<12} MQ={p['momentum_quality']:.3f} "
            f"acf1={p['acf1']:+.4f} up={p['up_ratio']*100:5.2f}% "
            f"sigma_day={p['sigma_day']*100:5.2f}% day_range={p['avg_day_range']*100:5.2f}%"
        )

    print("\nMath summary (best pair):")
    print(
        f" pair={best['pair']} | fee_rt={best['math']['fee_rt']*100:.3f}% | "
        f"p_hat={best['math']['p_hat']*100:.2f}% | breakeven_p={best['math']['breakeven_p']*100:.2f}%"
    )
    print(
        f" win_net={best['math']['win_net']*100:.3f}% | "
        f"loss_net={best['math']['loss_net']*100:.3f}% | "
        f"edge_trade={best['math']['edge_trade']*100:.3f}%"
    )
    print(
        f" edge_raw={best['math']['edge_trade_raw']*100:.3f}% | "
        f"edge_effective={best['math']['edge_trade_effective']*100:.3f}%"
    )
    print(f" trades_needed_full_cap≈{best['math']['trades_needed_full_cap']:.2f}/hari")

    if best['math']['edge_trade_raw'] <= 0:
        print(" ℹ️ edge_effective memakai fallback floor 0.35% karena edge_raw <= 0.")

    print("\nRecommended backtest_profiles setup:")
    for k, v in recommendation.items():
        print(f" - {k}: {v}")

    if best['math']['trades_needed_full_cap'] > 6:
        print("\n⚠️ Feasibility warning: target 5000/hari kemungkinan sangat agresif untuk balance ini pada edge historis saat ini.")

    _upsert_coin_analysis(
        window_start=start,
        window_end=end,
        target_daily_pnl=float(target_daily_pnl),
        balance_idr=float(balance_idr),
        rows=per_coin,
    )
    print(f"\n💾 Saved {len(per_coin)} row(s) to backtest_coin_analysis.")

    return {
        "top_pairs": top_k,
        "recommendation": recommendation,
        "math": best["math"],
        "per_coin": per_coin,
    }

# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    days=OHLCV_DAYS,
    pairs_filter=None,
    profiles_override: list[dict] | None = None,
    strict_per_coin: bool = True,
    min_liquidity_24h_idr: float = DEFAULT_MIN_LIQUIDITY_24H_IDR,
    min_price_idr: float = DEFAULT_MIN_PRICE_IDR,
):
    profiles = profiles_override if profiles_override is not None else _load_active_profiles()
    if not profiles:
        logger.warning("[run] No profile available to run.")
        return

    conn = get_connection()
    cur = conn.cursor()
    data_end = _get_latest_ohlcv_ts()
    if data_end is None:
        logger.warning("[run] No ohlcv_1m data available.")
        conn.close()
        return
    if data_end.tzinfo is None:
        data_end = data_end.replace(tzinfo=timezone.utc)
    data_start = data_end - timedelta(days=days)

    cur.execute(
        """
        SELECT DISTINCT pair
        FROM ohlcv_1m
        WHERE ts >= %s AND ts <= %s
        ORDER BY pair ASC
        """,
        (
            data_start,
            data_end,
        ),
    )
    pairs = [r['pair'] for r in cur.fetchall()]
    if pairs_filter:
        filter_set = {px.strip().lower() for px in pairs_filter.split(",") if px.strip()}
        pairs = [px for px in pairs if str(px).lower() in filter_set]

    if not pairs:
        logger.warning("[run] No pairs selected in requested time window.")
        conn.close()
        return

    if float(min_liquidity_24h_idr) > 0 or float(min_price_idr) > 0:
        print("ℹ️ Catatan: filter likuiditas/harga diabaikan pada mode strict per-koin ini.")

    pair_setup_map = _load_latest_coin_setup(pairs)

    logger.info(f"[run] Loaded per-coin setup for {len(pair_setup_map)} pair(s) from backtest_coin_analysis.")
    print(f"ℹ️ Loaded per-coin technical setup for {len(pair_setup_map)} pair(s) from backtest_coin_analysis.")

    missing_pairs = sorted([px for px in pairs if str(px).lower() not in pair_setup_map])

    if missing_pairs:
        conn.close()
        preview = ", ".join(missing_pairs[:10])
        if len(missing_pairs) > 10:
            preview += f", ... (+{len(missing_pairs)-10} lainnya)"
        print("❌ Strict per-coin aktif: setup teknikal per-koin belum lengkap.")
        print(f"   Missing pair setup: {preview}")
        print("   Jalankan `analyze` dulu agar backtest_coin_analysis terisi untuk semua pair tersebut.")
        return

    enriched_profiles = []
    for p in profiles:
        p2 = dict(p)
        p2["pair_setup_map"] = pair_setup_map
        p2["allowed_pairs"] = []
        enriched_profiles.append(p2)
    
    n_pairs = len(pairs)
    now = data_end
    start = data_start
    
    # 1. Multi-process sim
    all_results = []
    import multiprocessing
    max_w = min(4, multiprocessing.cpu_count())
    with ProcessPoolExecutor(max_workers=max_w) as pool:
        future_to_pair = {pool.submit(_process_pair_worker, px, start, now, enriched_profiles): px for px in pairs}
        for i, f in enumerate(as_completed(future_to_pair)):
            pair = future_to_pair[f]
            try:
                res = f.result()
                if res:
                    all_results.extend(res)
            except Exception as e:
                logger.error(f"[run] Worker failed for {pair}: {e}")
            _progress(i+1, n_pairs)
    
    # 2. Checkpoint (Save to temporary cache)
    cache_file = "/app/data/backtest_cache.pkl"
    try:
        import pickle
        # Ensure directory exists
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "wb") as f:
            pickle.dump({"results": all_results, "days": days, "start": start, "now": now}, f)
        logger.info(f"[run] 💾 Results cached to {cache_file}")
    except Exception as e:
        logger.warning(f"[run] ⚠️  Failed to cache results: {e}")

    # 3. Portfolio & Stats (Database Save)
    _persist_results(all_results, enriched_profiles, start, now)
    conn.close()

def _persist_results(all_results, profiles, start, now):
    logger.info("[db] 📥 Persisting results to database...")
    for p in profiles:
        p_trades = [t for t in all_results if t['profile_id'] == p['id']]
        if p_trades:
            portfolio = _simulate_portfolio(p_trades, p)
            stats = _compute_stats(portfolio, p['initial_capital_idr'], start, now)
            if stats:
                rid = _save_result(p['id'], None, stats, portfolio)
                _save_result_pair_setups(
                    result_id=rid,
                    profile_id=int(p['id']),
                    trades=portfolio,
                    pair_setup_map=p.get('pair_setup_map'),
                )
                print(f"   ✅ Saved: {p['profile_name']} ({len(portfolio)} trades)")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["fetch", "run", "report", "tune", "optimize", "restore", "analyze", "sync-coin-analysis"])
    parser.add_argument("--days", type=int, default=OHLCV_DAYS)
    parser.add_argument("--pairs", type=str)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--target-daily-pnl", type=float, default=None)
    parser.add_argument("--balance", type=float, default=100000.0)
    parser.add_argument("--create-profile-from-analysis", action="store_true")
    parser.add_argument("--analysis-pair", type=str, default=None)
    parser.add_argument("--new-profile-name", type=str, default=None)
    parser.add_argument("--run-after-create", action="store_true")
    parser.add_argument("--activate-new-profile", action="store_true")
    parser.add_argument("--activate-only-new", action="store_true")
    parser.add_argument("--profile-timeframe-minutes", type=int, default=1)
    parser.add_argument("--profile-strategy-mode", type=str, default="scalping")
    parser.add_argument("--strict-per-coin", action="store_true")
    parser.add_argument("--allow-global-fallback", action="store_true")
    parser.add_argument("--allow-negative-edge-profile", action="store_true")
    parser.add_argument("--backtest-optimized", action="store_true")
    parser.add_argument("--min-liquidity-24h-idr", type=float, default=DEFAULT_MIN_LIQUIDITY_24H_IDR)
    parser.add_argument("--min-price-idr", type=float, default=DEFAULT_MIN_PRICE_IDR)
    parser.add_argument("--auto-select-top", type=int, default=0)
    args = parser.parse_args()

    cmd_parts = [args.command]
    if args.days != OHLCV_DAYS:
        cmd_parts.extend(["--days", str(args.days)])
    if args.pairs:
        cmd_parts.extend(["--pairs", args.pairs])
    if args.generations != 3:
        cmd_parts.extend(["--generations", str(args.generations)])
    if args.target_daily_pnl is not None:
        cmd_parts.extend(["--target-daily-pnl", str(args.target_daily_pnl)])
    if args.balance != 100000.0:
        cmd_parts.extend(["--balance", str(args.balance)])
    if args.create_profile_from_analysis:
        cmd_parts.append("--create-profile-from-analysis")
    if args.analysis_pair:
        cmd_parts.extend(["--analysis-pair", args.analysis_pair])
    if args.new_profile_name:
        cmd_parts.extend(["--new-profile-name", args.new_profile_name])
    if args.run_after_create:
        cmd_parts.append("--run-after-create")
    if args.activate_new_profile:
        cmd_parts.append("--activate-new-profile")
    if args.activate_only_new:
        cmd_parts.append("--activate-only-new")
    if args.profile_timeframe_minutes != 1:
        cmd_parts.extend(["--profile-timeframe-minutes", str(args.profile_timeframe_minutes)])
    if args.profile_strategy_mode != "scalping":
        cmd_parts.extend(["--profile-strategy-mode", args.profile_strategy_mode])
    if args.strict_per_coin:
        cmd_parts.append("--strict-per-coin")
    if args.allow_global_fallback:
        cmd_parts.append("--allow-global-fallback")
    if args.allow_negative_edge_profile:
        cmd_parts.append("--allow-negative-edge-profile")
    if args.backtest_optimized:
        cmd_parts.append("--backtest-optimized")
    if float(args.min_liquidity_24h_idr) != DEFAULT_MIN_LIQUIDITY_24H_IDR:
        cmd_parts.extend(["--min-liquidity-24h-idr", str(args.min_liquidity_24h_idr)])
    if float(args.min_price_idr) != DEFAULT_MIN_PRICE_IDR:
        cmd_parts.extend(["--min-price-idr", str(args.min_price_idr)])
    if int(args.auto_select_top) > 0:
        cmd_parts.extend(["--auto-select-top", str(int(args.auto_select_top))])
    _ensure_container_runtime(" ".join(cmd_parts))
    _ensure_schema_upgraded()

    if args.command == "fetch":
        print("ℹ️ No standalone fetch step is required. Backtest reads from table ohlcv_1m.")
    elif args.command == "run":
        run_backtest(
            args.days,
            args.pairs,
            strict_per_coin=(not args.allow_global_fallback),
            min_liquidity_24h_idr=float(args.min_liquidity_24h_idr),
            min_price_idr=float(args.min_price_idr),
        )
    elif args.command == "report":
        profiles = _load_active_profiles()
        for p in profiles: _print_run_report(p['id'], p['profile_name'])
        _print_comparison_table()
    elif args.command == "tune":
        for p in _load_active_profiles():
            tune_profile(p['id'], args.target_daily_pnl)
    elif args.command == "restore":
        cache_file = "/app/data/backtest_cache.pkl"
        import pickle
        if not os.path.exists(cache_file):
            print(f"❌ Error: No cache file found at {cache_file}")
            return
        
        print(f"📥 Restoring results from {cache_file}...")
        with open(cache_file, "rb") as f:
            data = pickle.load(f)
        
        profiles = _load_active_profiles()
        _persist_results(data['results'], profiles, data['start'], data['now'])
        print("🏁 Restore complete.")

    elif args.command == "sync-coin-analysis":
        print("🔄 Menyinkronkan setup teknikal per-koin dari backtest_coin_analysis ke coin_analysis trading...")
        from app.infrastructure.database import db as trading_db
        sync_result = trading_db.sync_backtest_coin_analysis_to_trading(
            pairs=args.pairs.split(",") if args.pairs else None,
        )
        if sync_result.get("ok"):
            print(
                "✅ Sync selesai: "
                f"fetched={sync_result.get('fetched', 0)} | "
                f"live={sync_result.get('live_written', 0)} | "
                f"dryrun={sync_result.get('dryrun_written', 0)}"
            )
        else:
            print(f"❌ Sync gagal: {sync_result.get('error', 'unknown error')}")

    elif args.command == "analyze":
        analysis_result = analyze_ohlcv_target_setup(
            target_daily_pnl=float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_IDR),
            balance_idr=float(args.balance),
            days=int(args.days),
            pairs_filter=args.pairs,
            min_liquidity_24h_idr=float(args.min_liquidity_24h_idr),
            min_price_idr=float(args.min_price_idr),
        )

        if args.create_profile_from_analysis:
            if not analysis_result or not analysis_result.get("per_coin"):
                print("❌ Tidak bisa membuat profile: hasil analisis kosong.")
                return

            if int(args.auto_select_top) > 0:
                ranked_candidates = _sort_auto_selection_candidates(list(analysis_result["per_coin"]))
                selected_rows = ranked_candidates[: int(args.auto_select_top)]
                if not selected_rows:
                    print("❌ Auto-selection kosong setelah ranking kandidat.")
                    return
                auto_batch_tag = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

                if args.activate_new_profile and args.activate_only_new:
                    with transaction() as cur:
                        cur.execute("UPDATE backtest_profiles SET is_active = false WHERE is_active = true")

                created_profiles = []
                skipped_negative = []
                print(
                    f"\n🤖 Auto-select aktif: membuat {len(selected_rows)} profile dari universe "
                    f"{len(analysis_result['per_coin'])} pair hasil filter."
                )

                for idx, selected in enumerate(selected_rows, start=1):
                    edge_raw_selected = float(selected.get("math", {}).get("edge_trade_raw", 0.0) or 0.0)
                    if edge_raw_selected <= 0 and not args.allow_negative_edge_profile:
                        skipped_negative.append(str(selected["pair"]))
                        continue

                    recommendation_for_profile = dict(selected["recommendation"])
                    if args.backtest_optimized:
                        recommendation_for_profile = _apply_backtest_optimized_overrides(
                            recommendation_for_profile,
                            balance_idr=float(args.balance),
                            target_daily_pnl=float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_IDR),
                        )

                    auto_name = (
                        args.new_profile_name
                        if args.new_profile_name
                        else f"bt_auto_{int(args.days)}d_{auto_batch_tag}_{idx:02d}_{str(selected['pair']).lower()}"
                    )
                    new_profile = _create_profile_from_analysis(
                        pair=str(selected["pair"]),
                        recommendation=recommendation_for_profile,
                        target_daily_pnl=float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_IDR),
                        profile_name=auto_name,
                        timeframe_minutes=int(args.profile_timeframe_minutes),
                        strategy_mode=str(args.profile_strategy_mode),
                        backtest_optimized=bool(args.backtest_optimized),
                        activate_new_profile=bool(args.activate_new_profile),
                        activate_only_new=False,
                    )
                    created_profiles.append(new_profile)
                    print(
                        f"✅ [{len(created_profiles)}/{len(selected_rows)}] id={new_profile['id']} "
                        f"name={new_profile['profile_name']} pair_ref={selected['pair']} "
                        f"edge_raw={edge_raw_selected*100:.3f}%"
                    )

                if skipped_negative:
                    preview = ", ".join(skipped_negative[:10])
                    if len(skipped_negative) > 10:
                        preview += f", ... (+{len(skipped_negative)-10} lainnya)"
                    print(
                        "ℹ️ Profile dilewati karena edge_raw <= 0 tanpa flag "
                        f"--allow-negative-edge-profile: {preview}"
                    )

                if not created_profiles:
                    print("❌ Tidak ada profile berhasil dibuat dari auto-selection.")
                    return

                if args.run_after_create:
                    pairs_for_run = ",".join(sorted({str(p["pair"]).lower() for p in created_profiles if p.get("pair")}))
                    print(
                        f"\n🚀 Menjalankan backtest {len(created_profiles)} profile auto-select "
                        f"({args.days} hari)..."
                    )
                    run_backtest(
                        days=int(args.days),
                        pairs_filter=pairs_for_run if pairs_for_run else None,
                        profiles_override=created_profiles,
                        strict_per_coin=(not args.allow_global_fallback),
                        min_liquidity_24h_idr=float(args.min_liquidity_24h_idr),
                        min_price_idr=float(args.min_price_idr),
                    )
                    for profile in created_profiles:
                        _print_run_report(profile["id"], profile["profile_name"])
                    _print_comparison_table()
            else:
                selected_pair = args.analysis_pair or analysis_result["top_pairs"][0]["pair"]
                selected = None
                for row in analysis_result["per_coin"]:
                    if str(row["pair"]).lower() == str(selected_pair).lower():
                        selected = row
                        break

                if not selected:
                    print(f"❌ Pair {selected_pair} tidak ditemukan di hasil analisis.")
                    return

                edge_raw_selected = float(selected.get("math", {}).get("edge_trade_raw", 0.0) or 0.0)
                if edge_raw_selected <= 0 and not args.allow_negative_edge_profile:
                    print("❌ Profile creation dibatalkan: edge_trade_raw <= 0 (negative expectancy).")
                    print(
                        "   Gunakan pair lain / ubah horizon analisis / ubah asumsi fee-slippage, "
                        "atau pakai --allow-negative-edge-profile jika tetap ingin memaksa."
                    )
                    return

                recommendation_for_profile = dict(selected["recommendation"])
                if args.backtest_optimized:
                    recommendation_for_profile = _apply_backtest_optimized_overrides(
                        recommendation_for_profile,
                        balance_idr=float(args.balance),
                        target_daily_pnl=float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_IDR),
                    )

                new_profile = _create_profile_from_analysis(
                    pair=str(selected["pair"]),
                    recommendation=recommendation_for_profile,
                    target_daily_pnl=float(args.target_daily_pnl or DEFAULT_DAILY_TARGET_PNL_IDR),
                    profile_name=args.new_profile_name,
                    timeframe_minutes=int(args.profile_timeframe_minutes),
                    strategy_mode=str(args.profile_strategy_mode),
                    backtest_optimized=bool(args.backtest_optimized),
                    activate_new_profile=bool(args.activate_new_profile),
                    activate_only_new=bool(args.activate_only_new),
                )
                print(
                    f"\n✅ Created profile baru: id={new_profile['id']} "
                    f"name={new_profile['profile_name']} pair_ref={selected['pair']}"
                )

                if args.run_after_create:
                    print(
                        f"\n🚀 Menjalankan backtest profile baru untuk pair {selected['pair']} "
                        f"({args.days} hari)..."
                    )
                    run_backtest(
                        days=int(args.days),
                        pairs_filter=str(selected["pair"]),
                        profiles_override=[new_profile],
                        strict_per_coin=(not args.allow_global_fallback),
                        min_liquidity_24h_idr=float(args.min_liquidity_24h_idr),
                        min_price_idr=float(args.min_price_idr),
                    )
                    _print_run_report(new_profile["id"], new_profile["profile_name"])

    elif args.command == "optimize":
        for g in range(1, args.generations + 1):
            print(f"\n--- GENERATION {g} ---")
            run_backtest(
                args.days,
                args.pairs,
                strict_per_coin=(not args.allow_global_fallback),
                min_liquidity_24h_idr=float(args.min_liquidity_24h_idr),
                min_price_idr=float(args.min_price_idr),
            )
            active = _load_active_profiles()
            for p in active: _print_run_report(p['id'], p['profile_name'])
            _print_comparison_table()
            
            statuses = []
            for p in active:
                statuses.append(tune_profile(p['id'], args.target_daily_pnl))

            if any(s == "mutated" for s in statuses):
                continue

            if any(s == "target_reached" for s in statuses):
                print("🏁 Stopping early: Daily target reached.")
                break

            if any(s == "no_data" for s in statuses):
                print("⚠️ Stopping early: No backtest result rows were generated; check pair filter/window/data availability.")
                break

            if any(s == "no_change" for s in statuses):
                print("🏁 Stopping early: No further parameter changes proposed (local optimum).")
                break

if __name__ == "__main__":
    main()
