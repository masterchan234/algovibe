import json
import re
from datetime import datetime, timezone

import numpy as _np
import pandas as pd

from app.domain.technicals import derive_trade_signal
from services.backtester.db_backtest import get_connection, transaction
from services.backtester.runtime import MIN_TRADE_USDT


def _run_backtest_pair(p: dict, pair: str, df_or_candles: list[dict] | pd.DataFrame, start_time: datetime, end_time: datetime) -> dict:
    trades = []
    pair_cfg = p.get("pair_setup") or {}

    strat_mode = p.get("strategy_mode", "scalping")
    tf_min = int(p.get("timeframe_minutes", 1))
    pos_quote = float(pair_cfg.get("max_position_quote", p.get("max_position_quote", 120.0)))
    min_candles = 50

    fee_buy = float(p.get("fee_buy_taker", 0.001))   # Binance Spot taker 0.1%
    fee_sell = float(p.get("fee_sell_taker", 0.001))  # Binance Spot taker 0.1%
    slippage = float(p.get("fee_slippage_buffer", 0.0005))  # 0.05% market impact

    buy_threshold = float(pair_cfg.get("buy_score_threshold", p.get("buy_threshold", 6.5)))
    sell_block = float(pair_cfg.get("sell_block_threshold", p.get("sell_block_threshold", 5.0)))
    opp_max = float(pair_cfg.get("opposing_max", 5.0))  # Entry block: sell_score >= 5.0

    tp_pct = float(pair_cfg.get("target_tp_pct", p.get("target_tp_pct", 0.035)))
    sl_pct = float(pair_cfg.get("target_sl_pct", p.get("target_sl_pct", -0.035)))

    trail_enabled = p.get("trailing_sl_enabled", False)
    trail_trigger = float(p.get("trailing_sl_activation_pct", 0.02))
    trail_dist = float(p.get("trailing_sl_distance_pct", 0.015))

    ema_trend_f = pair_cfg.get("ema_trend_filter_enabled", p.get("ema_trend_filter_enabled", True))
    max_hold_m = int(pair_cfg.get("max_hold_minutes", p.get("max_hold_minutes", 1440)))
    cooldown_sec = int(pair_cfg.get("cooldown_seconds", p.get("cooldown_seconds", 300)))
    min_position_quote = float(pair_cfg.get("min_position_quote", p.get("min_position_quote", MIN_TRADE_USDT)))

    if isinstance(df_or_candles, list):
        df = pd.DataFrame(df_or_candles)
        candles = df_or_candles
    else:
        df = df_or_candles
        candles = df_or_candles.to_dict("records")
        
    if len(df) < min_candles:
        return {"trades": [], "stats": {}}

    if tf_min > 1:
        df_ts = df.copy()
        df_ts["ts"] = pd.to_datetime(df_ts["ts"])
        df_res = df_ts.set_index("ts").resample(f"{tf_min}min").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        ).dropna()

        resampled_candles = df_res.reset_index().to_dict("records")
        res_history = _get_signal_history(pair, resampled_candles, p, pair_cfg)
        if not res_history:
            return {"trades": [], "stats": {}}

        sig_map = pd.Series(res_history, index=df_res.index).shift(1)
        df_sig = df_ts[["ts"]].set_index("ts")
        df_sig["sig"] = sig_map
        df_sig["sig"] = df_sig["sig"].ffill()

        empty_sig = {"verdict": "HOLD", "buy_score": 0.0, "sell_score": 0.0}
        history = [s if isinstance(s, dict) else empty_sig for s in df_sig["sig"].tolist()]
    else:
        history = _get_signal_history(pair, df, p, pair_cfg)

    if not history or len(history) != len(candles):
        return {"trades": [], "stats": {}}

    current_cap = float(p.get("initial_capital_quote", 1000.0))
    position = None
    cooldown_until = 0
    pair_consecutive_losses = 0  # Anti-Bleed: track consecutive losses for THIS pair
    anti_bleed_cooldown_sec = 3 * 60 * 60  # 3 hours in seconds

    close_arr = df["close"].values
    high_arr = df["high"].values
    low_arr = df["low"].values
    open_arr = df["open"].values
    ts_epochs = _np.array([c["ts"].timestamp() for c in candles])
    ema50_arr = df["close"].ewm(span=50, adjust=False).mean().values
    pending_entry = None  # next-candle entry queue

    for i in range(min_candles, len(candles)):
        ts_epoch = ts_epochs[i]
        ts = candles[i]["ts"]
        close = close_arr[i]
        high = high_arr[i]
        low = low_arr[i]
        sig = history[i]

        # Fill pending entry at this candle's open (next-candle entry simulation).
        # Signal fires at previous candle close; execution at next candle open
        # avoids buying the local top of the momentum spike.
        if pending_entry is not None and position is None:
            open_fill_px = open_arr[i] * (1.0 + slippage)
            p_trade_quote = pending_entry["trade_quote"]
            p_buy_fee = p_trade_quote * fee_buy
            p_crypto_amt = (p_trade_quote - p_buy_fee) / open_fill_px
            position = {
                "entry_price": open_fill_px,
                "amount_quote": p_trade_quote,
                "amount_crypto": p_crypto_amt,
                "buy_fee": p_buy_fee,
                "entry_ts": ts_epoch,
                "highest_price": open_arr[i],
                "trailing_sl_price": 0.0,
                "dynamic_sl": 0.0,
                "buy_score": pending_entry["buy_score"],
                "dca_tier": 0,
            }
            pending_entry = None

        if position:
            entry_px = position["entry_price"]
            entry_quote = position["amount_quote"]
            crypto = position["amount_crypto"]

            current_highest = position["highest_price"]
            duration_m = (ts_epoch - position["entry_ts"]) / 60.0

            # Profile trailing SL (hanya jika trail_enabled di profil)
            if trail_enabled:
                if (current_highest - entry_px) / entry_px >= trail_trigger:
                    new_sl = current_highest * (1.0 - trail_dist)
                    if new_sl > position["trailing_sl_price"]:
                        position["trailing_sl_price"] = new_sl

            # Dynamic Breakeven SL — mirrors exit_manager.py, SELALU aktif
            # +0.35% gross peak → SL ke entry + 0.02% (fee cushion)
            # +0.55% gross peak, umur >=2m → trailing SL 0.2% dari peak
            gross_peak_pct = (current_highest - entry_px) / entry_px if entry_px > 0 else 0.0
            if gross_peak_pct >= 0.0035:
                be_sl = entry_px * 1.0002  # breakeven + 0.02% fee cushion
                if be_sl > position["dynamic_sl"]:
                    position["dynamic_sl"] = be_sl
            if gross_peak_pct >= 0.0055:
                trail_dist_dyn = 0.002 if duration_m >= 2.0 else 0.003
                dyn_trail = current_highest * (1.0 - trail_dist_dyn)
                if dyn_trail > position["dynamic_sl"]:
                    position["dynamic_sl"] = dyn_trail

            exit_reason = None
            exit_price = 0.0
            tp_price = entry_px * (1.0 + tp_pct)
            static_sl_px = entry_px * (1.0 + sl_pct)

            # Exit checks: Evaluate SL/TP against 'low' and 'high'
            if position["dynamic_sl"] > 0 and low <= position["dynamic_sl"]:
                exit_reason = "DYNAMIC_SL"
                exit_price = position["dynamic_sl"] * (1.0 - slippage)
            elif position["trailing_sl_price"] > 0 and low <= position["trailing_sl_price"]:
                exit_reason = "TRAILING_SL"
                exit_price = position["trailing_sl_price"] * (1.0 - slippage)
            elif low <= static_sl_px:
                exit_reason = "SL"
                exit_price = static_sl_px * (1.0 - slippage)
            elif high >= tp_price:
                # TTP Target Floor Lock: AGENTS.md rule 6
                if position["dynamic_sl"] < tp_price:
                    position["dynamic_sl"] = tp_price
                
                # Hit TP and dropped back down in the same candle
                if low <= tp_price:
                    exit_reason = "TP_FLOOR"
                    exit_price = tp_price  # Limit orders have no market-impact slippage
            elif (ts_epoch - position["entry_ts"]) > max_hold_m * 60:
                exit_reason = "MAX_HOLD"
                exit_price = close * (1.0 - slippage)
            elif sig["verdict"] != "BUY":
                # Momentum Fading Profit Lock — mirrors exit_manager.py
                cur_gross_pct = (close - entry_px) / entry_px if entry_px > 0 else 0.0
                if cur_gross_pct >= 0.003 and (
                    (duration_m >= 5.0) or sig["verdict"] == "SELL"
                ):
                    exit_reason = "MOMENTUM_FADE"
                    exit_price = close * (1.0 - slippage)
                elif sig["verdict"] == "SELL" and sig["sell_score"] >= sell_block:
                    exit_reason = "SIGNAL_REVERSE"
                    exit_price = close * (1.0 - slippage)

            # Track highest_price untuk NEXT candle
            if not exit_reason:
                if high > position["highest_price"]:
                    position["highest_price"] = high

            if exit_reason:
                gross_pnl = (exit_price - entry_px) * crypto
                sell_fee = exit_price * crypto * fee_sell
                net_pnl = gross_pnl - position["buy_fee"] - sell_fee
                pnl_pct = net_pnl / entry_quote * 100 if entry_quote > 0 else 0
                duration = int(ts_epoch - position["entry_ts"])
                trades.append(
                    {
                        "profile_id": p["id"],
                        "pair": pair,
                        "entry_time": datetime.fromtimestamp(position["entry_ts"], tz=timezone.utc),
                        "exit_time": ts,
                        "entry_price": entry_px,
                        "exit_price": exit_price,
                        "amount_quote": entry_quote,
                        "amount_crypto": crypto,
                        "pnl_quote": round(net_pnl, 2),
                        "pnl_pct": round(pnl_pct, 4),
                        "exit_reason": exit_reason,
                        "buy_score": position.get("buy_score", 0),
                        "sell_score_at_exit": sig["sell_score"] if exit_reason == "SIGNAL_REVERSE" else None,
                        "duration_seconds": duration,
                    }
                )
                current_cap += entry_quote + net_pnl
                position = None
                # Anti-Bleed: escalate cooldown on consecutive losses per pair
                if net_pnl > 0:
                    pair_consecutive_losses = 0
                    cooldown_until = ts_epoch + cooldown_sec
                else:
                    pair_consecutive_losses += 1
                    if pair_consecutive_losses >= 2:
                        cooldown_until = ts_epoch + anti_bleed_cooldown_sec  # 3h blacklist
                    else:
                        cooldown_until = ts_epoch + cooldown_sec
            continue

        if ts_epoch < cooldown_until:
            continue
        if current_cap < min_position_quote:
            break
        if sig["verdict"] != "BUY" or sig["buy_score"] < buy_threshold:
            continue
        if sig["sell_score"] >= opp_max:
            continue
        if ema_trend_f and close < ema50_arr[i]:
            continue
        if strat_mode == "trend_pullback":
            pass

        trade_quote = min(pos_quote, current_cap * 0.9)
        current_cap -= trade_quote  # Reserve capital; entry fills at next candle open
        pending_entry = {
            "trade_quote": trade_quote,
            "buy_score": sig["buy_score"],
        }

    if position:
        last_c = candles[-1]
        exit_px = last_c["close"] * (1.0 - slippage)
        gross_pnl = (exit_px - position["entry_price"]) * position["amount_crypto"]
        sell_fee = exit_px * position["amount_crypto"] * fee_sell
        net_pnl = gross_pnl - position["buy_fee"] - sell_fee
        current_cap += position["amount_quote"] + net_pnl
        trades.append(
            {
                "profile_id": p["id"],
                "pair": pair,
                "entry_time": datetime.fromtimestamp(position["entry_ts"], tz=timezone.utc),
                "exit_time": last_c["ts"],
                "entry_price": position["entry_price"],
                "exit_price": exit_px,
                "amount_quote": position["amount_quote"],
                "amount_crypto": position["amount_crypto"],
                "pnl_quote": round(net_pnl, 2),
                "pnl_pct": round(net_pnl / position["amount_quote"] * 100, 4),
                "exit_reason": "END_OF_DATA",
                "buy_score": position["buy_score"],
                "sell_score_at_exit": None,
                "duration_seconds": int(ts_epochs[-1] - position["entry_ts"]),
            }
        )

    return {"trades": trades}


def _compute_technical_indicators(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    if len(df) < 50:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    delta = close.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = -delta.where(delta < 0, 0).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, _np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50.0)

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    df["tr"] = _np.maximum(tr1, _np.maximum(tr2, tr3))
    df["atr"] = df["tr"].ewm(alpha=1 / 14, adjust=False).mean()
    df["atr_pct"] = (df["atr"] / close) * 100.0

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd_hist"] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_denom = (4 * std20).replace(0, _np.nan)
    df["bb_pct"] = ((close - (sma20 - 2 * std20)) / bb_denom).fillna(0.5)

    rsi_min = df["rsi"].rolling(14).min()
    rsi_max = df["rsi"].rolling(14).max()
    stoch_denom = (rsi_max - rsi_min).replace(0, _np.nan)
    stoch_raw = ((df["rsi"] - rsi_min) / stoch_denom) * 100.0
    df["stoch_k"] = stoch_raw.rolling(3, min_periods=1).mean().fillna(50.0)
    df["stoch_d"] = df["stoch_k"].rolling(3, min_periods=1).mean().fillna(50.0)

    hh14 = high.rolling(14).max()
    ll14 = low.rolling(14).min()
    wr_denom = (hh14 - ll14).replace(0, _np.nan)
    df["williams_r"] = (-100.0 * (hh14 - close) / wr_denom).fillna(-50.0)
    df["roc"] = (close.pct_change(10) * 100.0).fillna(0.0)
    df["slope"] = close.pct_change(5).fillna(0.0)

    vol_ma20 = volume.rolling(20).mean().replace(0, _np.nan)
    df["volume_ratio"] = (volume / vol_ma20).fillna(1.0)

    obv_delta = _np.sign(close.diff().fillna(0.0)) * volume.fillna(0.0)
    obv = obv_delta.cumsum()
    obv_prev = obv.shift(5).abs().replace(0, _np.nan)
    df["obv_slope"] = (((obv - obv.shift(5)) / obv_prev) * 100.0).fillna(0.0)

    df["velocity"] = (close.pct_change(4) * 100.0).fillna(0.0)

    hl_range = (high - low).replace(0, _np.nan)
    candle_pos = ((close - low) / hl_range * 100.0).fillna(50.0)
    df["buy_pressure"] = candle_pos.ewm(span=5, adjust=False).mean().shift(1).fillna(50.0)

    pct_returns = close.pct_change().fillna(0.0)
    rolling_vol = pct_returns.rolling(20, min_periods=10).std() * 100.0
    def _vol_to_buy_threshold(v):
        if v > 2.5: return 7.5
        elif v > 1.5: return 7.0
        return 6.5
    def _vol_to_sell_threshold(v):
        if v > 2.5: return 7.5
        return 7.0
    df["adaptive_buy_threshold"] = rolling_vol.apply(_vol_to_buy_threshold).fillna(6.5)
    df["adaptive_sell_threshold"] = rolling_vol.apply(_vol_to_sell_threshold).fillna(7.0)

    return df


def _get_signal_history(pair: str, df_or_candles: list[dict] | pd.DataFrame, p: dict, pair_cfg: dict | None = None) -> list[dict]:
    if isinstance(df_or_candles, list):
        df = _compute_technical_indicators(df_or_candles)
    else:
        df = df_or_candles

    if len(df) < 50:
        return []

    history = []
    # Optimization: Itertuples is 10x faster than iterrows/to_dict("records")
    for row in df.itertuples():
        cfg = pair_cfg or {}
        ema_enabled = cfg.get("ema_trend_filter_enabled", p.get("ema_trend_filter_enabled", True))
        is_uptrend = (not ema_enabled) or (row.close > row.ema50)

        atr_limit = float(cfg.get("atr_max_pct", p.get("atr_max_pct", 3.0)))
        is_stable = row.atr_pct < atr_limit

        tech = {
            "rsi": row.rsi,
            "macd_hist": row.macd_hist,
            "last_price": row.close,
            "bb_pct": row.bb_pct,
            "ema9": row.ema9,
            "ema21": row.ema21,
            "ema_cross": "BULLISH" if row.ema9 > row.ema21 else "BEARISH",
            "stoch_k": row.stoch_k,
            "stoch_d": row.stoch_d,
            "williams_r": row.williams_r,
            "roc": row.roc,
            "slope": row.slope,
            "volume_ratio": row.volume_ratio,
            "obv_slope": row.obv_slope,
            "velocity": row.velocity,
            "buy_pressure": row.buy_pressure,
        }

        adaptive_cfg = dict(cfg)
        adaptive_cfg["buy_score_threshold"] = row.adaptive_buy_threshold
        adaptive_cfg["sell_score_threshold"] = row.adaptive_sell_threshold

        sig = derive_trade_signal(tech, adaptive_thresholds=adaptive_cfg)
        if sig["verdict"] == "BUY":
            if not is_uptrend:
                sig["verdict"] = "HOLD"
                sig["reason"] = "REJECTED (EMA50 Trend Filter)"
            elif not is_stable:
                sig["verdict"] = "HOLD"
                sig["reason"] = "REJECTED (ATR Volatility Filter)"
        history.append(sig)
    return history


def _compute_stats(trades: list[dict], capital: float, start: datetime, end: datetime) -> dict:
    if not trades:
        return {}

    pnls = [t["pnl_quote"] for t in trades]
    total_pnl = sum(pnls)
    win_rate = len([p for p in pnls if p > 0]) / len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else 9.99

    equity = capital
    peak = capital
    max_dd_quote = 0.0
    max_dd_pct = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_quote:
            max_dd_quote = dd
        dd_p = (dd / peak * 100) if peak > 0 else 0
        if dd_p > max_dd_pct:
            max_dd_pct = dd_p

    days = max(1.0, (end - start).total_seconds() / 86400)
    annualized_factor = 365.0 / days
    trades_per_year = len(pnls) * annualized_factor
    pnl_pcts = [t["pnl_pct"]/100 for t in trades]
    
    if len(pnl_pcts) > 1 and _np.std(pnl_pcts) > 0:
        sharpe = (_np.mean(pnl_pcts) / _np.std(pnl_pcts)) * _np.sqrt(max(1, trades_per_year))
    else:
        sharpe = 0.0

    if len(pnl_pcts) > 1:
        downside = [p for p in pnl_pcts if p < 0]
        if downside and _np.std(downside) > 0:
            sortino = (_np.mean(pnl_pcts) / _np.std(downside)) * _np.sqrt(max(1, trades_per_year))
        else:
            sortino = sharpe
    else:
        sortino = 0.0
        
    calmar = (_np.mean(pnl_pcts) * trades_per_year) / (max_dd_pct / 100) if max_dd_pct > 0 else 0.0
    expectancy = (win_rate * _np.mean(wins)) - ((1 - win_rate) * _np.mean(losses)) if losses and wins else 0.0

    return {
        "window_start": start,
        "window_end": end,
        "total_trades": int(len(trades)),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate": float(round(win_rate, 4)),
        "total_pnl_quote": float(round(total_pnl, 2)),
        "total_pnl_pct": float(round(total_pnl / capital * 100, 4)),
        "max_drawdown_pct": float(round(max_dd_pct, 4)),
        "max_drawdown_quote": float(round(max_dd_quote, 2)),
        "profit_factor": float(round(pf, 2)),
        "sharpe_ratio": float(round(sharpe, 4)),
        "avg_trade_duration_sec": int(_np.mean([t["duration_seconds"] for t in trades])),
        "avg_pnl_per_trade_quote": float(round(_np.mean(pnls), 2)),
        "avg_pnl_per_trade_pct": float(round(_np.mean([t["pnl_pct"] for t in trades]), 4)),
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "initial_capital_quote": float(capital),
        "final_capital_quote": float(capital + total_pnl),
        "sortino_ratio": float(round(sortino, 4)),
        "recovery_factor": float(round(total_pnl / max_dd_quote, 4)) if max_dd_quote > 0 else 0.0,
        "calmar_ratio": float(round(calmar, 4)),
        "expectancy_quote": float(round(expectancy, 2)),
    }


def _process_pair_worker(pair_name, start_time, end_time, profiles_list):
    min_tf = min([int(p.get("timeframe_minutes", 1)) for p in profiles_list] + [1])
    if min_tf < 1:
        min_tf = 1

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
        if not rows:
            return []

        candles = [{"ts": r["ts"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]} for r in rows]
        pair_trades = []
        pair_lc = str(pair_name).lower()
        
        # Calculate technical indicators exactly ONCE for this pair
        df_tech = _compute_technical_indicators(candles)
        
        for p in profiles_list:
            p_local = dict(p)
            allowed_pairs = set(str(x).lower() for x in (p_local.get("allowed_pairs") or []))
            if allowed_pairs and pair_lc not in allowed_pairs:
                continue
            pair_setup_map = p_local.get("pair_setup_map") or {}
            p_local["pair_setup"] = pair_setup_map.get(str(pair_name).lower(), {})
            
            # Pass df_tech instead of raw candles to avoid redundant computation
            res = _run_backtest_pair(p_local, pair_name, df_tech, start_time, end_time)
            if res["trades"]:
                pair_trades.extend(res["trades"])
        return pair_trades
    finally:
        conn.close()


def _simulate_portfolio(all_trades: list[dict], profile: dict) -> list[dict]:
    if not all_trades:
        return []
    sorted_trades = sorted(all_trades, key=lambda x: x["entry_time"])
    capital = float(profile.get("initial_capital_quote", 1000000.0))
    max_pos = int(profile.get("max_open_positions", 3))
    available_cash = capital
    active_trades = []
    final_trades = []

    def _release_closed(until_ts):
        nonlocal active_trades, available_cash
        still_open = []
        for a in active_trades:
            if a["exit_time"] <= until_ts:
                available_cash += float(a.get("amount_quote", 0.0)) + float(a.get("pnl_quote", 0.0))
            else:
                still_open.append(a)
        active_trades = still_open

    for t in sorted_trades:
        _release_closed(t["entry_time"])
        amount_quote = float(t.get("amount_quote", 0.0))
        if len(active_trades) < max_pos and amount_quote <= available_cash:
            active_trades.append(t)
            final_trades.append(t)
            available_cash -= amount_quote

    _release_closed(datetime.max.replace(tzinfo=timezone.utc))
    return final_trades


def _save_result(pid, pair, stats, trades):
    with transaction() as cur:
        def _c(val):
            if hasattr(val, "item"):
                return val.item()
            return val

        cur.execute(
            """
            INSERT INTO backtest_results (
                profile_id, window_start, window_end, pair, total_trades, winning_trades, losing_trades, win_rate,
                total_pnl_quote, total_pnl_pct, max_drawdown_quote, max_drawdown_pct,
                profit_factor, sharpe_ratio, initial_capital_quote, final_capital_quote,
                avg_trade_duration_sec, avg_pnl_per_trade_quote, avg_pnl_per_trade_pct
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                int(pid),
                stats["window_start"],
                stats["window_end"],
                pair,
                int(_c(stats["total_trades"])),
                int(_c(stats["winning_trades"])),
                int(_c(stats["losing_trades"])),
                float(_c(stats["win_rate"])),
                float(_c(stats["total_pnl_quote"])),
                float(_c(stats["total_pnl_pct"])),
                float(_c(stats["max_drawdown_quote"])),
                float(_c(stats["max_drawdown_pct"])),
                float(_c(stats["profit_factor"])),
                float(_c(stats["sharpe_ratio"])),
                float(_c(stats["initial_capital_quote"])),
                float(_c(stats["final_capital_quote"])),
                int(_c(stats["avg_trade_duration_sec"])),
                float(_c(stats["avg_pnl_per_trade_quote"])),
                float(_c(stats["avg_pnl_per_trade_pct"])),
            ),
        )
        rid = cur.fetchone()["id"]
        for t in trades:
            cur.execute(
                """
                INSERT INTO backtest_trades (
                    result_id, pair, entry_time, exit_time, entry_price, exit_price,
                    amount_quote, amount_crypto, pnl_quote, pnl_pct, exit_reason,
                    buy_score, sell_score_at_exit, duration_seconds
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    rid,
                    t["pair"],
                    t["entry_time"],
                    t["exit_time"],
                    float(_c(t["entry_price"])),
                    float(_c(t["exit_price"])),
                    float(_c(t["amount_quote"])),
                    float(_c(t["amount_crypto"])),
                    float(_c(t["pnl_quote"])),
                    float(_c(t["pnl_pct"])),
                    t["exit_reason"],
                    float(_c(t["buy_score"])),
                    float(_c(t["sell_score_at_exit"])) if t["sell_score_at_exit"] is not None else None,
                    int(_c(t["duration_seconds"])),
                ),
            )
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
                rec_max_position_quote,
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
                "max_position_quote": float(r["rec_max_position_quote"] or 100000.0),
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