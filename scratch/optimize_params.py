#!/usr/bin/env python3
"""
Algovibe Parameter Optimizer
=============================
Baca ohlcv_1m, hitung sinyal + forward analysis vectorized, temukan
(threshold, TP%, SL%, max_hold) optimal untuk target ROI >= 10%.

Run inside backtester container:
  docker compose run --rm backtester python /app/scratch/optimize_params.py
"""

import sys
import os
sys.path.insert(0, '/app')

import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from services.backtester.db_backtest import get_connection

# ── CONFIG ────────────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 1000.0    # USDT — same as backtest profiles
MAX_POSITION    = 120.0     # USDT per trade
FEE             = 0.002     # 0.1% buy + 0.1% sell
SLIP            = 0.0005    # 0.05% slippage each way → combined 0.001
COST            = FEE + 2 * SLIP  # ~0.3% total round-trip cost
DAYS            = 30        # analysis window
MIN_CANDLES     = 2000      # skip pairs with insufficient data
MIN_SIGS        = 25        # skip if too few signals at any threshold
MAX_HOLD_MAX    = 30        # pre-compute fwd window up to 30 candles

# Grid search space
THRESHOLDS = [6.5, 7.0, 7.5, 8.0, 8.5, 9.0]
TPS        = [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040, 0.050]  # TP %
SLS        = [-0.005, -0.0075, -0.010, -0.015, -0.020, -0.025, -0.030] # SL % (negative)
HOLDS      = [5, 10, 15, 20, 30]  # max_hold minutes


ATR_MAX_PCT  = 3.0   # reject candles where ATR% >= this (same as backtester default)

# ── SIGNAL ENGINE (3-indicator v3.0, vectorized NumPy) ────────────────────────

def compute_signals(df: pd.DataFrame):
    """
    Compute buy_score[i] and entry-gate filters — identical to backtester:
      1. Velocity (4-period close pct change × 100)   → max 3.5
      2. Volume Ratio (vol / MA-20 vol)               → max 3.5
      3. OB Pressure (EMA-5 candle position, shift-1) → max 3.0
    Filters (must ALL pass for a valid BUY signal):
      - EMA-50 trend filter: close > ema50
      - ATR volatility filter: atr_pct < ATR_MAX_PCT
    Also returns adaptive_threshold per candle (6.5/7.0/7.5 based on volatility).
    """
    n  = len(df)
    c  = df['close'].values.astype(np.float64)
    h  = df['high'].values.astype(np.float64)
    lo = df['low'].values.astype(np.float64)
    v  = df['volume'].values.astype(np.float64)

    # 1. Velocity: (close[i] - close[i-4]) / close[i-4] * 100
    vel = np.zeros(n)
    prev4 = np.where(c[:-4] > 0, c[:-4], 1e-10)
    vel[4:] = (c[4:] / prev4 - 1.0) * 100.0
    vel_score = np.where(vel >= 0.4, 3.5, np.where(vel >= 0.15, 1.5, 0.0))

    # 2. Volume Ratio: vol / MA-20
    vm20 = pd.Series(v).rolling(20, min_periods=5).mean().bfill().values
    vm20 = np.where(vm20 > 0, vm20, 1e-10)
    vr   = v / vm20
    vr_score = np.where(vr >= 2.0, 3.5, np.where(vr >= 1.4, 1.5, 0.0))

    # 3. OB Pressure: EMA-5 of (close-low)/(high-low)*100, shifted by 1 candle
    hl_rng  = np.where((h - lo) > 1e-12, h - lo, np.nan)
    cpos    = np.where(np.isnan(hl_rng), 50.0, (c - lo) / hl_rng * 100.0)
    bp      = pd.Series(cpos).ewm(span=5, adjust=False).mean().shift(1).fillna(50.0).values
    ob_score = np.where(bp >= 65.0, 3.0, np.where(bp >= 55.0, 1.5, 0.0))

    scores = vel_score + vr_score + ob_score  # shape (n,), max 10.0

    # ── Filter 1: EMA-50 Trend (close > ema50) ────────────────────────────────
    # Same as backtester: ema_trend_filter_enabled=True by default
    ema50 = pd.Series(c).ewm(span=50, adjust=False).mean().values
    ema_ok = c > ema50                             # (n,) bool

    # ── Filter 2: ATR Volatility (atr_pct < ATR_MAX_PCT) ─────────────────────
    # True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    tr1 = h - lo
    tr2 = np.abs(h - np.roll(c, 1));  tr2[0] = tr1[0]
    tr3 = np.abs(lo - np.roll(c, 1)); tr3[0] = tr1[0]
    tr  = np.maximum(tr1, np.maximum(tr2, tr3))
    atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
    atr_pct  = np.where(c > 0, atr / c * 100.0, 100.0)
    atr_ok   = atr_pct < ATR_MAX_PCT               # (n,) bool

    # ── Filter 3: Adaptive threshold per candle ───────────────────────────────
    # rolling 20-candle std of returns; maps to 6.5/7.0/7.5 like live bot
    pct_ret    = pd.Series(c).pct_change().fillna(0.0)
    roll_vol   = pct_ret.rolling(20, min_periods=10).std().fillna(0.0).values * 100.0
    adapt_thr  = np.where(roll_vol > 2.5, 7.5, np.where(roll_vol > 1.5, 7.0, 6.5))

    return scores, ema_ok & atr_ok, adapt_thr


# ── FORWARD WINDOW BUILDER ────────────────────────────────────────────────────

def build_forward_windows(opens, highs, lows, closes, scores, valid_mask, adapt_thr):
    """
    For all signal candles that pass entry-gate filters, build forward price windows.
    Entry is at NEXT candle's open (same as live bot pending-entry queue).

    valid_mask : bool array (n,) — EMA50 & ATR filters combined
    adapt_thr  : float array (n,) — per-candle adaptive threshold (6.5/7.0/7.5)

    Returns dict or None if insufficient signals.
    """
    n = len(closes)
    # Candidate signals: score >= minimum grid threshold AND pass all filters
    raw_idxs  = np.where((scores >= min(THRESHOLDS)) & valid_mask)[0]

    # Need MAX_HOLD_MAX candles after entry, plus one for entry itself
    in_window = (raw_idxs + 1 + MAX_HOLD_MAX) < n
    sig_idxs  = raw_idxs[in_window]

    if len(sig_idxs) < MIN_SIGS:
        return None

    entry_idxs = sig_idxs + 1                              # entry at NEXT open
    entry_px   = opens[entry_idxs] * (1.0 + SLIP)         # slippage on entry

    k_range    = np.arange(MAX_HOLD_MAX, dtype=np.intp)
    k_idx      = entry_idxs[:, None] + k_range[None, :]   # (n_sig, MAX_HOLD_MAX)

    return {
        'sig_idxs'  : sig_idxs,
        'entry_px'  : entry_px,                       # (n_sig,)
        'fwd_h'     : highs[k_idx],                   # (n_sig, MAX_HOLD_MAX)
        'fwd_l'     : lows[k_idx],                    # (n_sig, MAX_HOLD_MAX)
        'fwd_c'     : closes[k_idx],                  # (n_sig, MAX_HOLD_MAX)
        'scores'    : scores[sig_idxs],               # (n_sig,) raw score
        'adapt_thr' : adapt_thr[sig_idxs],            # (n_sig,) per-signal threshold
    }


# ── GRID SEARCH ───────────────────────────────────────────────────────────────

def grid_search(data: dict) -> dict:
    """
    For one pair, grid-search over (threshold, tp, sl, hold) and return
    a dict mapping each combo → {wr, trades, roi, pnl, ...}.
    """
    ep         = data['entry_px']    # (n_sig,)
    fwd_h      = data['fwd_h']       # (n_sig, MAX_HOLD_MAX)
    fwd_l      = data['fwd_l']
    fwd_c      = data['fwd_c']
    scores     = data['scores']
    adapt_thr  = data['adapt_thr']   # per-signal adaptive threshold
    results = {}

    for threshold in THRESHOLDS:
        # A signal is valid if raw_score >= threshold AND raw_score >= adapt_thr
        # (adaptive threshold enforces volatility-based minimum)
        tmask = (scores >= threshold) & (scores >= adapt_thr)
        if tmask.sum() < MIN_SIGS:
            continue

        t_ep   = ep[tmask]
        t_fh   = fwd_h[tmask]
        t_fl   = fwd_l[tmask]
        t_fc   = fwd_c[tmask]
        n_sig  = len(t_ep)

        for tp in TPS:
            tp_px   = t_ep * (1.0 + tp)              # (n_sig,)
            # tp_hit[i,k] = True if high at candle (entry+k) >= tp_price[i]
            tp_hit  = t_fh >= tp_px[:, None]         # (n_sig, MAX_HOLD_MAX) bool

            for sl in SLS:
                sl_px   = t_ep * (1.0 + sl)          # (n_sig,)  sl < 0
                sl_hit  = t_fl <= sl_px[:, None]     # (n_sig, MAX_HOLD_MAX) bool

                for hold in HOLDS:
                    th = tp_hit[:, :hold]             # (n_sig, hold)
                    sh = sl_hit[:, :hold]

                    tp_ever = th.any(axis=1)          # (n_sig,) bool
                    sl_ever = sh.any(axis=1)

                    # First hit candle index (argmax on bool → first True index)
                    # If never hit, set to `hold` (beyond window)
                    first_tp = np.where(tp_ever, np.argmax(th, axis=1), hold)
                    first_sl = np.where(sl_ever, np.argmax(sh, axis=1), hold)

                    # Outcome determination:
                    # WIN  : TP hit and (no SL, or TP hit before SL)
                    # LOSS : SL hit and (no TP, or SL hit before TP)
                    # HOLD : neither hit within window
                    wins   = (tp_ever & (~sl_ever | (first_tp <= first_sl)))
                    losses = (sl_ever & (~tp_ever | (first_sl <  first_tp)))
                    holds  = ~wins & ~losses

                    n_wins  = wins.sum()
                    n_loss  = losses.sum()
                    n_holds = holds.sum()
                    wr      = float(n_wins) / n_sig

                    # PnL per outcome type:
                    #   WIN  : exit at tp_px → pct = tp; net = tp - COST
                    #   LOSS : exit at sl_px → pct = sl; net = sl - COST  (sl < 0)
                    #   HOLD : exit at close[entry + hold - 1]
                    hold_exit_idx = hold - 1
                    hold_ret = t_fc[:, hold_exit_idx] / t_ep - 1.0  # (n_sig,)

                    win_pnl  = float(n_wins)              * MAX_POSITION * (tp  - COST)
                    loss_pnl = float(n_loss)              * MAX_POSITION * (sl  - COST)
                    hold_pnl = float((hold_ret[holds] - COST).sum()) * MAX_POSITION

                    total_pnl = win_pnl + loss_pnl + hold_pnl
                    roi       = total_pnl / INITIAL_CAPITAL

                    results[(threshold, tp, sl, hold)] = {
                        'wr'         : wr,
                        'trades'     : n_sig,
                        'wins'       : int(n_wins),
                        'losses'     : int(n_loss),
                        'hold_exits' : int(n_holds),
                        'pnl'        : total_pnl,
                        'roi'        : roi,
                    }
    return results


# ── DB HELPERS ────────────────────────────────────────────────────────────────

def load_all_pairs(conn) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT lower(pair) AS pair
        FROM ohlcv_1m
        WHERE ts >= %s
        GROUP BY pair
        HAVING count(*) >= %s
        ORDER BY pair
        """,
        (cutoff, MIN_CANDLES),
    )
    return [r['pair'] for r in cur.fetchall()]


def load_candles(conn, pair: str) -> pd.DataFrame:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv_1m
        WHERE lower(pair) = %s AND ts >= %s
        ORDER BY ts ASC
        """,
        (pair, cutoff),
    )
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def update_db_with_optimal(conn, best_global: tuple, pair_results: dict):
    """
    Update backtest_coin_analysis and backtest_profiles with optimal params.
    best_global: (threshold, tp, sl, hold)
    pair_results: { pair: (threshold, tp, sl, hold) }
    """
    cur = conn.cursor()

    # Update global profile params
    g_thresh, g_tp, g_sl, g_hold = best_global
    cur.execute(
        """
        UPDATE backtest_profiles
        SET buy_threshold    = %s,
            target_tp_pct    = %s,
            target_sl_pct    = %s,
            max_hold_minutes = %s,
            updated_at       = NOW()
        """,
        (g_thresh, g_tp, g_sl, g_hold),
    )

    # Update per-pair coin analysis
    updated = 0
    for pair, (p_thresh, p_tp, p_sl, p_hold) in pair_results.items():
        cur.execute(
            """
            UPDATE backtest_coin_analysis
            SET rec_buy_score_threshold = %s,
                rec_target_tp_pct       = %s,
                rec_target_sl_pct       = %s,
                rec_max_hold_minutes    = %s
            WHERE lower(pair) = lower(%s)
            """,
            (p_thresh, p_tp, p_sl, p_hold, pair),
        )
        updated += cur.rowcount

    conn.commit()
    return updated


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    target_roi = 0.10
    print("=" * 70)
    print("  ALGOVIBE PARAMETER OPTIMIZER")
    print(f"  Target ROI : {target_roi * 100:.0f}%  |  Capital : ${INITIAL_CAPITAL:.0f}")
    print(f"  Position   : ${MAX_POSITION:.0f}  |  Fee+Slip : {COST*100:.2f}% round-trip")
    print(f"  Window     : {DAYS} days  |  Pairs min candles: {MIN_CANDLES}")
    print(f"  Grid       : {len(THRESHOLDS)}×{len(TPS)}×{len(SLS)}×{len(HOLDS)} = "
          f"{len(THRESHOLDS)*len(TPS)*len(SLS)*len(HOLDS)} combos per pair")
    print("=" * 70)

    conn    = get_connection()
    pairs   = load_all_pairs(conn)
    n_pairs = len(pairs)
    print(f"\n[INFO] Analyzing {n_pairs} pairs ...\n")

    # portfolio_scores[combo] → {'total_pnl', 'pairs_counted', 'total_trades'}
    portfolio = defaultdict(lambda: {'total_pnl': 0.0, 'pairs': 0, 'total_trades': 0, 'total_wins': 0})
    per_pair_best = {}   # pair → (threshold, tp, sl, hold) with best ROI
    skipped = 0

    for idx, pair in enumerate(pairs):
        df = load_candles(conn, pair)
        if len(df) < MIN_CANDLES:
            skipped += 1
            continue

        scores, valid_mask, adapt_thr = compute_signals(df)
        data   = build_forward_windows(
            df['open'].values.astype(np.float64),
            df['high'].values.astype(np.float64),
            df['low'].values.astype(np.float64),
            df['close'].values.astype(np.float64),
            scores,
            valid_mask,
            adapt_thr,
        )
        if data is None:
            skipped += 1
            continue

        res = grid_search(data)
        if not res:
            skipped += 1
            continue

        # Accumulate into portfolio aggregator
        for combo, m in res.items():
            portfolio[combo]['total_pnl']    += m['pnl']
            portfolio[combo]['pairs']         += 1
            portfolio[combo]['total_trades']  += m['trades']
            portfolio[combo]['total_wins']    += m['wins']

        # Per-pair best
        best_combo = max(res, key=lambda k: res[k]['roi'])
        per_pair_best[pair] = best_combo

        if (idx + 1) % 50 == 0 or (idx + 1) == n_pairs:
            print(f"  [{idx+1:>3}/{n_pairs}] done, skipped {skipped} ...")

    conn.close()

    if not portfolio:
        print("[ERROR] No valid results. Exiting.")
        sys.exit(1)

    # ── PORTFOLIO RESULT ──────────────────────────────────────────────────────
    # Sort by total_pnl (= ROI × INITIAL_CAPITAL summed across all pairs)
    sorted_portfolio = sorted(portfolio.items(), key=lambda x: x[1]['total_pnl'], reverse=True)

    print("\n" + "=" * 70)
    print("  TOP 20 GLOBAL CONFIGURATIONS (by portfolio PnL across all pairs)")
    print("=" * 70)
    hdr = f"{'Thresh':>6} {'TP%':>5} {'SL%':>6} {'Hold':>5} | {'Pairs':>6} {'Trades':>7} {'WR%':>6} | {'PnL $':>9} {'ROI%':>8}"
    print(hdr)
    print("-" * 70)

    TARGET_ROI = 0.10  # local var for display
    best_combo = sorted_portfolio[0][0]

    for combo, m in sorted_portfolio[:20]:
        wr_global = m['total_wins'] / max(m['total_trades'], 1) * 100
        roi_global = m['total_pnl'] / INITIAL_CAPITAL * 100
        marker = " <<< TARGET" if m['total_pnl'] >= INITIAL_CAPITAL * 0.10 else ""
        print(
            f"{combo[0]:>6.1f} {combo[1]*100:>5.1f} {combo[2]*100:>6.2f} {combo[3]:>5} | "
            f"{m['pairs']:>6} {m['total_trades']:>7} {wr_global:>6.1f} | "
            f"{m['total_pnl']:>9.2f} {roi_global:>7.1f}%{marker}"
        )

    # ── OPTIMAL CONFIG ────────────────────────────────────────────────────────
    top_combo = sorted_portfolio[0]
    b_thresh, b_tp, b_sl, b_hold = top_combo[0]
    b_metrics = top_combo[1]
    b_wr = b_metrics['total_wins'] / max(b_metrics['total_trades'], 1) * 100
    b_roi = b_metrics['total_pnl'] / INITIAL_CAPITAL * 100

    print("\n" + "=" * 70)
    print("  OPTIMAL GLOBAL CONFIGURATION")
    print("=" * 70)
    print(f"  buy_score_threshold : {b_thresh}")
    print(f"  target_tp_pct       : {b_tp*100:.2f}%")
    print(f"  target_sl_pct       : {b_sl*100:.2f}%")
    print(f"  max_hold_minutes    : {b_hold}")
    print(f"  ---")
    print(f"  Portfolio WR        : {b_wr:.2f}%")
    print(f"  Portfolio PnL       : ${b_metrics['total_pnl']:,.2f}")
    print(f"  Portfolio ROI       : {b_roi:.2f}%")
    print(f"  Total Trades        : {b_metrics['total_trades']:,}")
    print(f"  Pairs covered       : {b_metrics['pairs']}")

    # ── BREAKEVEN ANALYSIS ────────────────────────────────────────────────────
    net_win  = b_tp  - COST
    net_loss = abs(b_sl) + COST
    breakeven_wr = net_loss / (net_win + net_loss) * 100
    print(f"\n  Breakeven WR (at TP={b_tp*100:.1f}%, SL={b_sl*100:.1f}%): {breakeven_wr:.1f}%")
    print(f"  Achieved WR: {b_wr:.2f}%  →  {'PROFITABLE' if b_wr > breakeven_wr else 'LOSS'}")

    # ── PER-PAIR TOP 10 ───────────────────────────────────────────────────────
    # Best ROI per pair
    all_pair_results = []
    for pair, combo in per_pair_best.items():
        m = portfolio[combo]
        all_pair_results.append((pair, combo, m))

    # Get per-pair metrics from grid results (need to re-run per pair)
    print("\n" + "=" * 70)
    print("  PER-PAIR BEST CONFIG (sample — top 15 by individual ROI)")
    print("=" * 70)

    # Compute per-pair ROI using best combo from portfolio aggregator
    # We don't have per-pair metrics for the portfolio best combo stored
    # So show the per_pair_best combo (best ROI for each individual pair)
    from collections import Counter
    combo_counter = Counter(per_pair_best.values())
    print(f"\n  Most common optimal combos across {len(per_pair_best)} pairs:")
    print(f"  {'Thresh':>6} {'TP%':>5} {'SL%':>6} {'Hold':>5} | {'# Pairs':>8}")
    print("  " + "-" * 40)
    for combo, count in combo_counter.most_common(15):
        print(f"  {combo[0]:>6.1f} {combo[1]*100:>5.1f} {combo[2]*100:>6.2f} {combo[3]:>5} | {count:>8}")

    # ── WRITE OPTIMAL CONFIG TO DB ────────────────────────────────────────────
    print(f"\n[INFO] Applying optimal config to DB:")
    print(f"  backtest_profiles    → threshold={b_thresh}, TP={b_tp*100:.1f}%, SL={b_sl*100:.1f}%, hold={b_hold}min")
    print(f"  backtest_coin_analysis → {len(per_pair_best)} pairs with per-pair best config")

    conn2 = get_connection()
    n_updated = update_db_with_optimal(conn2, (b_thresh, b_tp, b_sl, b_hold), per_pair_best)
    conn2.close()

    print(f"  Updated {n_updated} per-pair rows in backtest_coin_analysis.")
    print(f"\n[DONE] Run: docker compose run --rm backtester run && backtester report")


if __name__ == '__main__':
    TARGET_ROI = 0.10  # global for main()
    main()
