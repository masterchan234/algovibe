#!/usr/bin/env python3
"""
Data-Driven Forward Returns Optimizer
======================================
Baca ohlcv_1m, simulasikan EXACT backtester exit logic (termasuk DYNAMIC_SL +
MOMENTUM_FADE + SIGNAL_REVERSE), cari per-coin optimal params secara empiris.

Perbedaan kritis vs optimize_params.py:
  1. Simulasi lengkap: DYNAMIC_SL (breakeven & trailing), MOMENTUM_FADE,
     SIGNAL_REVERSE — bukan hanya SL/TP/MAX_HOLD
  2. Per-coin EV analysis: hanya trading coins dengan positive expected value
  3. Coins dengan negatif EV di semua kombinasi param → threshold=11.0 (skip)
  4. Score stratification: analisis per score bucket (6.5-7.5, 7.5-8.5, 8.5+)

Run:
  docker compose run --rm backtester python /app/scratch/data_driven_optimizer.py
"""

import sys
import os
sys.path.insert(0, '/app')

import numpy as np
import pandas as pd
from datetime import datetime, timezone
import time

from services.backtester.db_backtest import get_connection

# ── CONFIG ────────────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 1000.0
MAX_POSITION    = 120.0
FEE_BUY         = 0.001          # 0.1%
FEE_SELL        = 0.001          # 0.1%
SLIP            = 0.0005         # 0.05% per side
COST_TOTAL      = FEE_BUY + FEE_SELL + 2 * SLIP   # ~0.3% round-trip
DAYS            = 30
MIN_CANDLES     = 2000
MIN_SIGNALS     = 10             # skip pair dengan signal < ini
MAX_FWD         = 30             # pre-compute forward window up to 30 candles
ATR_MAX_PCT     = 3.0
SELL_BLOCK      = 5.0            # sell_score >= ini → SIGNAL_REVERSE

# Grid search space
THRESHOLDS = [6.5, 7.0, 7.5, 8.0, 8.5]
TPS        = [0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.025, 0.030]  # TP %
SLS        = [-0.005, -0.010, -0.015, -0.020, -0.025, -0.030, -0.035]  # SL % (neg)
HOLDS      = [3, 5, 8, 10, 15, 20, 30]

TARGET_ROI = 0.10   # 10% net ROI on INITIAL_CAPITAL

# ── SIGNAL COMPUTATION ────────────────────────────────────────────────────────

def compute_all_signals(df: pd.DataFrame):
    """
    Hitung buy_score, sell_score, adaptive_thr, dan entry filters untuk semua candle.
    Returns: scores, sell_scores, valid_mask (EMA50+ATR), adapt_thr, buy_verdicts, sell_verdicts
    """
    n  = len(df)
    c  = df['close'].values.astype(np.float64)
    h  = df['high'].values.astype(np.float64)
    lo = df['low'].values.astype(np.float64)
    v  = df['volume'].values.astype(np.float64)

    # 1. Velocity
    vel = np.zeros(n)
    p4  = np.where(c[:-4] > 0, c[:-4], 1e-10)
    vel[4:] = (c[4:] / p4 - 1.0) * 100.0
    buy_vel  = np.where(vel >= 0.4, 3.5, np.where(vel >= 0.15, 1.5, 0.0))
    sell_vel = np.where(vel <= -0.4, 3.5, 0.0)

    # 2. Volume Ratio
    vm20 = pd.Series(v).rolling(20, min_periods=5).mean().bfill().values
    vm20 = np.where(vm20 > 0, vm20, 1e-10)
    vr   = v / vm20
    buy_vr  = np.where(vr >= 2.0, 3.5, np.where(vr >= 1.4, 1.5, 0.0))
    sell_vr = np.where(vr <= 0.4, 2.0, 0.0)

    # 3. OB Pressure (EMA-5 of candle position, shifted 1)
    hl_rng = np.where((h - lo) > 1e-12, h - lo, np.nan)
    cpos   = np.where(np.isnan(hl_rng), 50.0, (c - lo) / hl_rng * 100.0)
    bp     = pd.Series(cpos).ewm(span=5, adjust=False).mean().shift(1).fillna(50.0).values
    buy_bp  = np.where(bp >= 65.0, 3.0, np.where(bp >= 55.0, 1.5, 0.0))
    sell_bp = np.where(bp <= 35.0, 3.0, 0.0)

    scores      = buy_vel + buy_vr + buy_bp
    sell_scores = sell_vel + sell_vr + sell_bp

    # EMA-50 filter
    ema50   = pd.Series(c).ewm(span=50, adjust=False).mean().values
    ema_ok  = c > ema50

    # ATR filter
    tr1 = h - lo
    tr2 = np.abs(h - np.roll(c, 1)); tr2[0] = tr1[0]
    tr3 = np.abs(lo - np.roll(c, 1)); tr3[0] = tr1[0]
    tr  = np.maximum(tr1, np.maximum(tr2, tr3))
    atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
    atr_pct = np.where(c > 0, atr / c * 100.0, 100.0)
    atr_ok  = atr_pct < ATR_MAX_PCT

    valid_mask = ema_ok & atr_ok

    # Adaptive threshold
    pct_ret  = pd.Series(c).pct_change().fillna(0.0)
    roll_vol = pct_ret.rolling(20, min_periods=10).std().fillna(0.0).values * 100.0
    adapt_thr = np.where(roll_vol > 2.5, 7.5, np.where(roll_vol > 1.5, 7.0, 6.5))

    # Verdicts (for fwd lookups during MOMENTUM_FADE/SIGNAL_REVERSE checks)
    buy_verdicts  = (scores >= adapt_thr) & (scores >= 6.5)
    sell_verdicts = sell_scores >= 7.0

    return scores, sell_scores, valid_mask, adapt_thr, buy_verdicts, sell_verdicts


# ── FORWARD WINDOW BUILDER ────────────────────────────────────────────────────

def build_forward_windows(df, scores, sell_scores, valid_mask, adapt_thr,
                          buy_verdicts, sell_verdicts):
    """
    Untuk semua signal candle yang lolos filter, build forward price windows.
    Entry di NEXT candle's open (same as backtester).

    Returns dict dengan:
      n_sig       : jumlah signal
      entry_px    : (n_sig,) entry price
      fwd_opens   : (n_sig, MAX_FWD)  open prices candle j=1..MAX_FWD
      fwd_highs   : (n_sig, MAX_FWD)
      fwd_lows    : (n_sig, MAX_FWD)
      fwd_closes  : (n_sig, MAX_FWD)
      fwd_buy_v   : (n_sig, MAX_FWD) bool — buy verdict at candle j
      fwd_sell_v  : (n_sig, MAX_FWD) bool — sell verdict at candle j
      fwd_sell_sc : (n_sig, MAX_FWD) sell_score at candle j
      sig_scores  : (n_sig,) buy_score at signal candle
      adapt_thr_s : (n_sig,) adaptive threshold at signal candle
    """
    n      = len(df)
    opens  = df['open'].values.astype(np.float64)
    highs  = df['high'].values.astype(np.float64)
    lows   = df['low'].values.astype(np.float64)
    closes = df['close'].values.astype(np.float64)

    # Candidate signal candles: minimum threshold pass + all filters
    raw_idxs = np.where((scores >= min(THRESHOLDS)) & valid_mask)[0]
    # Need at least MAX_FWD candles ahead
    raw_idxs = raw_idxs[raw_idxs < n - MAX_FWD - 1]
    if len(raw_idxs) == 0:
        return None

    ns = len(raw_idxs)

    fwd_o  = np.zeros((ns, MAX_FWD))
    fwd_h  = np.zeros((ns, MAX_FWD))
    fwd_l  = np.zeros((ns, MAX_FWD))
    fwd_c  = np.zeros((ns, MAX_FWD))
    fwd_bv = np.zeros((ns, MAX_FWD), dtype=bool)
    fwd_sv = np.zeros((ns, MAX_FWD), dtype=bool)
    fwd_ss = np.zeros((ns, MAX_FWD))

    for j in range(MAX_FWD):
        jj = raw_idxs + j + 1   # j=0 → entry candle (idx+1)
        fwd_o[:, j]  = opens[jj]
        fwd_h[:, j]  = highs[jj]
        fwd_l[:, j]  = lows[jj]
        fwd_c[:, j]  = closes[jj]
        fwd_bv[:, j] = buy_verdicts[jj]
        fwd_sv[:, j] = sell_verdicts[jj]
        fwd_ss[:, j] = sell_scores[jj]

    return {
        'n_sig'      : ns,
        'entry_px'   : fwd_o[:, 0],      # open of candle idx+1 = entry price
        'fwd_highs'  : fwd_h,
        'fwd_lows'   : fwd_l,
        'fwd_closes' : fwd_c,
        'fwd_buy_v'  : fwd_bv,
        'fwd_sell_v' : fwd_sv,
        'fwd_sell_sc': fwd_ss,
        'sig_scores' : scores[raw_idxs],
        'adapt_thr_s': adapt_thr[raw_idxs],
    }


# ── COMPLETE SIMULATION (mirrors backtester core.py exit logic) ───────────────

def simulate_combo(fwd, threshold, tp_pct, sl_pct, max_hold):
    """
    Simulate trades untuk satu kombinasi parameter.
    Exit logic EXACT sama dengan backtester core.py:
      1. DYNAMIC_SL  : low <= dynamic_sl_px (breakeven / trailing 0.2%)
      2. SL          : low <= static_sl_px
      3. TP          : high >= tp_px
      4. MAX_HOLD    : after max_hold candles
      5. MOMENTUM_FADE: close >= entry*1.003 AND signal != BUY (duration >= 2m or SELL)
      6. SIGNAL_REVERSE: signal == SELL AND sell_score >= SELL_BLOCK

    Returns:
      pnl_arr    : (n_active,) net pnl_pct per trade
      reasons    : (n_active,) exit reasons
      n_trades   : int
    """
    # Filter by threshold (adaptive threshold also applies at signal candle)
    sig_mask = (fwd['sig_scores'] >= threshold) & (fwd['sig_scores'] >= fwd['adapt_thr_s'])
    if sig_mask.sum() == 0:
        return np.array([]), np.array([]), 0

    ep  = fwd['entry_px'][sig_mask]        # entry price (n_active,)
    fh  = fwd['fwd_highs'][sig_mask]       # (n_active, MAX_FWD)
    fl  = fwd['fwd_lows'][sig_mask]
    fc  = fwd['fwd_closes'][sig_mask]
    fbv = fwd['fwd_buy_v'][sig_mask]       # bool: is buy signal at fwd candle j?
    fss = fwd['fwd_sell_sc'][sig_mask]     # sell score at fwd candle j
    fsv = fwd['fwd_sell_v'][sig_mask]      # sell verdict at fwd candle j

    n = len(ep)

    # Precompute fixed price levels
    tp_px    = ep * (1.0 + tp_pct)
    sl_px    = ep * (1.0 + sl_pct)      # sl_pct is negative
    mf_px    = ep * 1.003               # momentum fade trigger (+0.3%)
    be_px    = ep * 1.0002              # breakeven + 0.02% fee cushion
    tp_exit  = tp_px * (1.0 - SLIP)
    sl_exit  = sl_px * (1.0 - SLIP)

    # State
    active      = np.ones(n, dtype=bool)
    highest_px  = ep.copy()
    dynamic_sl  = np.zeros(n)           # 0 = not set
    pnl_arr     = np.zeros(n)
    reasons     = np.full(n, 'MAX_HOLD', dtype='<U16')

    mh = min(max_hold, MAX_FWD)

    for j in range(mh):
        if not np.any(active):
            break

        ch = fh[:, j]       # current candle high
        cl = fl[:, j]       # current candle low
        cc = fc[:, j]       # current candle close
        duration_m = j + 1  # candles elapsed since entry (1m data → minutes)

        # ── Update highest price ────────────────────────────────────────────
        highest_px = np.where(active, np.maximum(highest_px, ch), highest_px)

        # ── Update DYNAMIC_SL (breakeven + trailing) ────────────────────────
        gross_peak = np.where(ep > 0, (highest_px - ep) / ep, 0.0)

        # Step 1: set breakeven when peak >= +0.3%
        set_be = active & (gross_peak >= 0.003) & (dynamic_sl < be_px)
        dynamic_sl = np.where(set_be, be_px, dynamic_sl)

        # Step 2: trailing stop when peak >= +0.5% AND duration >= 2m
        if duration_m >= 2:
            trail_dist = 0.002
            dyn_trail = highest_px * (1.0 - trail_dist)
            set_trail = active & (gross_peak >= 0.005) & (dyn_trail > dynamic_sl)
            dynamic_sl = np.where(set_trail, dyn_trail, dynamic_sl)

        # ── Exit checks (same priority as backtester) ───────────────────────

        # DYNAMIC_SL: low hits breakeven/trailing stop
        hit_dsl = active & (dynamic_sl > 0) & (cl <= dynamic_sl)
        dsl_exit_px = dynamic_sl * (1.0 - SLIP)

        # Static SL
        hit_sl = active & ~hit_dsl & (cl <= sl_px)

        # TP
        hit_tp = active & ~hit_dsl & ~hit_sl & (ch >= tp_px)

        # MAX_HOLD (last allowed candle)
        hit_mh = active & ~hit_dsl & ~hit_sl & ~hit_tp & (j == mh - 1)

        # MOMENTUM_FADE: close >= entry*1.003 AND signal != BUY
        # signal != BUY = ~buy_verdict at fwd candle j
        not_buy = ~fbv[:, j]
        mf_cond = (cc >= mf_px) & not_buy & ((duration_m >= 2) | fsv[:, j])
        hit_mf = active & ~hit_dsl & ~hit_sl & ~hit_tp & ~hit_mh & mf_cond
        mf_exit_px = cc * (1.0 - SLIP)

        # SIGNAL_REVERSE: explicit SELL signal AND sell_score >= SELL_BLOCK
        hit_sr = (active & ~hit_dsl & ~hit_sl & ~hit_tp & ~hit_mh & ~hit_mf
                  & fsv[:, j] & (fss[:, j] >= SELL_BLOCK))
        sr_exit_px = cc * (1.0 - SLIP)

        # ── Compute PnL for each exit type ──────────────────────────────────
        def _pnl(exit_px):
            # (exit_px/entry_px - 1) - fees
            return exit_px / ep - 1.0 - FEE_BUY - FEE_SELL * exit_px / ep

        # Apply exits
        mh_exit_px = cc * (1.0 - SLIP)

        pnl_arr  = np.where(hit_dsl, _pnl(dsl_exit_px), pnl_arr)
        pnl_arr  = np.where(hit_sl,  _pnl(sl_exit),     pnl_arr)
        pnl_arr  = np.where(hit_tp,  _pnl(tp_exit),     pnl_arr)
        pnl_arr  = np.where(hit_mh,  _pnl(mh_exit_px),  pnl_arr)
        pnl_arr  = np.where(hit_mf,  _pnl(mf_exit_px),  pnl_arr)
        pnl_arr  = np.where(hit_sr,  _pnl(sr_exit_px),  pnl_arr)

        reasons  = np.where(hit_dsl, 'DYNAMIC_SL',      reasons)
        reasons  = np.where(hit_sl,  'SL',               reasons)
        reasons  = np.where(hit_tp,  'TP',               reasons)
        reasons  = np.where(hit_mh,  'MAX_HOLD',         reasons)
        reasons  = np.where(hit_mf,  'MOMENTUM_FADE',    reasons)
        reasons  = np.where(hit_sr,  'SIGNAL_REVERSE',   reasons)

        closed = hit_dsl | hit_sl | hit_tp | hit_mh | hit_mf | hit_sr
        active = active & ~closed

    return pnl_arr, reasons, n


# ── PER-PAIR GRID SEARCH ──────────────────────────────────────────────────────

def optimize_pair(pair, fwd):
    """
    Grid search: cari (threshold, tp, sl, max_hold) yang memaksimalkan
    total net PnL untuk pair ini.

    Returns (best_params dict, stats dict) atau None jika semua negatif EV.
    """
    best_total_pnl = -np.inf
    best_params    = None
    best_stats     = None

    for threshold in THRESHOLDS:
        for tp in TPS:
            for sl in SLS:
                for mh in HOLDS:
                    pnl_arr, _, n_trades = simulate_combo(fwd, threshold, tp, sl, mh)
                    if n_trades < 5:
                        continue

                    mean_pnl   = pnl_arr.mean()          # avg pnl per trade (frac)
                    total_pnl  = pnl_arr.sum() * MAX_POSITION  # USDT
                    n_wins     = (pnl_arr > 0).sum()
                    wr         = n_wins / n_trades

                    if total_pnl > best_total_pnl:
                        best_total_pnl = total_pnl
                        best_params    = dict(threshold=threshold, tp=tp, sl=sl, max_hold=mh)
                        best_stats     = dict(
                            n_trades=n_trades,
                            wr=round(wr * 100, 1),
                            mean_pnl_pct=round(mean_pnl * 100, 4),
                            total_pnl_usdt=round(total_pnl, 2),
                        )

    return best_params, best_stats


# ── SCORE STRATIFICATION ──────────────────────────────────────────────────────

def score_analysis(fwd):
    """Analisis forward returns per score bucket menggunakan default params."""
    buckets = [
        ('6.5-7.5',  6.5,  7.5),
        ('7.5-8.5',  7.5,  8.5),
        ('8.5-9.5',  8.5,  9.5),
        ('9.5-10.0', 9.5, 10.1),
    ]
    results = {}
    default_tp = 0.010
    default_sl = -0.015
    default_mh = 10

    for label, lo_s, hi_s in buckets:
        # Temporary: filter by score bucket
        tmp = dict(fwd)
        bucket_mask = (fwd['sig_scores'] >= lo_s) & (fwd['sig_scores'] < hi_s) & \
                      (fwd['sig_scores'] >= fwd['adapt_thr_s'])
        if bucket_mask.sum() < 5:
            continue
        tmp['entry_px']    = fwd['entry_px'][bucket_mask]
        tmp['fwd_highs']   = fwd['fwd_highs'][bucket_mask]
        tmp['fwd_lows']    = fwd['fwd_lows'][bucket_mask]
        tmp['fwd_closes']  = fwd['fwd_closes'][bucket_mask]
        tmp['fwd_buy_v']   = fwd['fwd_buy_v'][bucket_mask]
        tmp['fwd_sell_v']  = fwd['fwd_sell_v'][bucket_mask]
        tmp['fwd_sell_sc'] = fwd['fwd_sell_sc'][bucket_mask]
        tmp['sig_scores']  = fwd['sig_scores'][bucket_mask]
        tmp['adapt_thr_s'] = fwd['adapt_thr_s'][bucket_mask]

        # Use threshold = lo_s (all signals in bucket pass)
        pnl_arr, reasons, n = simulate_combo(tmp, lo_s, default_tp, default_sl, default_mh)
        if n > 0:
            results[label] = {
                'n': n,
                'wr': round((pnl_arr > 0).mean() * 100, 1),
                'mean_pnl': round(pnl_arr.mean() * 100, 4),
            }
    return results


# ── DB HELPERS ────────────────────────────────────────────────────────────────

def load_pairs_with_data(conn):
    """Load only pairs that were actually traded in the most recent backtest run."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT bt.pair
        FROM backtest_trades bt
        JOIN backtest_results br ON bt.result_id = br.id
        WHERE br.pair IS NULL
          AND br.created_at >= NOW() - INTERVAL '30 days'
        ORDER BY bt.pair
    """)
    traded = [r['pair'] for r in cur.fetchall()]

    # Fallback: all pairs with sufficient candles if no recent backtest
    if not traded:
        cur.execute("""
            SELECT DISTINCT pair FROM backtest_coin_analysis
            WHERE pair IN (
                SELECT DISTINCT pair FROM ohlcv_1m
                GROUP BY pair HAVING COUNT(*) >= %s
            )
            ORDER BY pair
        """, (MIN_CANDLES,))
        traded = [r['pair'] for r in cur.fetchall()]

    return traded


def load_ohlcv_batch(conn, pairs):
    """
    Load OHLCV for all pairs in a SINGLE query — ~10x faster than one-by-one.
    Returns dict: pair → DataFrame
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT pair, ts, open, high, low, close, volume
        FROM ohlcv_1m
        WHERE pair = ANY(%s)
        ORDER BY pair, ts ASC
    """, (pairs,))
    rows = cur.fetchall()

    if not rows:
        return {}

    df_all = pd.DataFrame([dict(r) for r in rows])
    df_all = df_all.astype({'open': float, 'high': float, 'low': float,
                            'close': float, 'volume': float})
    result = {}
    for pair, grp in df_all.groupby('pair'):
        if len(grp) >= MIN_CANDLES:
            result[pair] = grp.reset_index(drop=True)
    return result


def update_db(conn, pair_results):
    """
    Update backtest_coin_analysis dengan optimal params per pair.
    Pairs dengan semua-negatif EV → threshold = 11.0 (effectively disabled).
    """
    cur = conn.cursor()
    skipped = 0
    updated = 0

    for pair, (best_params, best_stats) in pair_results.items():
        if best_params is None or best_stats['total_pnl_usdt'] <= 0:
            # Negative EV on all combos → disable this pair
            cur.execute("""
                UPDATE backtest_coin_analysis
                SET rec_buy_score_threshold = 11.0
                WHERE pair = %s
            """, (pair,))
            skipped += 1
        else:
            cur.execute("""
                UPDATE backtest_coin_analysis
                SET rec_buy_score_threshold = %s,
                    rec_target_tp_pct       = %s,
                    rec_target_sl_pct       = %s,
                    rec_max_hold_minutes    = %s
                WHERE pair = %s
            """, (
                best_params['threshold'],
                best_params['tp'],
                best_params['sl'],
                best_params['max_hold'],
                pair,
            ))
            updated += 1

    conn.commit()
    return updated, skipped


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  ALGOVIBE DATA-DRIVEN FORWARD RETURNS OPTIMIZER")
    print(f"  Target ROI: {TARGET_ROI*100:.0f}%  |  Capital: ${INITIAL_CAPITAL:.0f}")
    print("=" * 65)

    conn = get_connection()
    pairs = load_pairs_with_data(conn)
    print(f"\n[OK] Loaded {len(pairs)} pairs untuk dianalisis\n")

    print(f"[..] Loading OHLCV data dalam satu batch query...")
    t_load = time.time()
    pair_data = load_ohlcv_batch(conn, pairs)
    print(f"[OK] Data loaded: {len(pair_data)} pairs | {sum(len(v) for v in pair_data.values()):,} rows | {time.time()-t_load:.1f}s\n")

    pair_results   = {}
    all_scores_fwd = {}

    t0 = time.time()
    for i, pair in enumerate(pair_data.keys()):
        df = pair_data[pair]

        scores, sell_scores, valid_mask, adapt_thr, buy_verdicts, sell_verdicts = \
            compute_all_signals(df)

        fwd = build_forward_windows(
            df, scores, sell_scores, valid_mask, adapt_thr,
            buy_verdicts, sell_verdicts
        )
        if fwd is None or fwd['n_sig'] < MIN_SIGNALS:
            continue

        best_params, best_stats = optimize_pair(pair, fwd)

        # Score stratification untuk reporting
        score_strat = score_analysis(fwd)
        all_scores_fwd[pair] = score_strat

        pair_results[pair] = (best_params, best_stats)

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(pair_data) - i - 1)
            print(f"  Progress: {i+1}/{len(pair_data)} pairs ({elapsed:.0f}s elapsed, ETA {eta:.0f}s)")

    print(f"\n[OK] Analisis selesai: {time.time()-t0:.0f}s\n")

    # ── REPORTING ─────────────────────────────────────────────────────────────

    # Pisahkan coins positif EV vs negatif EV
    positive_ev = {
        p: (bp, bs) for p, (bp, bs) in pair_results.items()
        if bp is not None and bs['total_pnl_usdt'] > 0
    }
    negative_ev = {
        p: (bp, bs) for p, (bp, bs) in pair_results.items()
        if bp is None or bs['total_pnl_usdt'] <= 0
    }

    print(f"  Coins dengan POSITIVE EV : {len(positive_ev)}")
    print(f"  Coins dengan NEGATIVE EV : {len(negative_ev)} (akan di-disable)")

    # Top-20 coins by total PnL
    top_coins = sorted(
        positive_ev.items(),
        key=lambda x: x[1][1]['total_pnl_usdt'],
        reverse=True
    )[:20]

    print(f"\n  TOP-20 COINS by Projected PnL:")
    print(f"  {'Pair':<12} {'WR%':>6} {'AvgPnL%':>9} {'Trades':>7} {'PnL_USDT':>10} {'Thr':>5} {'TP%':>5} {'SL%':>6} {'Hold':>5}")
    print("  " + "-" * 75)
    for pair, (bp, bs) in top_coins:
        print(f"  {pair:<12} {bs['wr']:>6.1f} {bs['mean_pnl_pct']:>9.4f} "
              f"{bs['n_trades']:>7} {bs['total_pnl_usdt']:>10.2f} "
              f"{bp['threshold']:>5.1f} {bp['tp']*100:>5.2f} {bp['sl']*100:>6.2f} {bp['max_hold']:>5}")

    # Score stratification — aggregate across all pairs
    print(f"\n  SCORE STRATIFICATION (default tp=1%, sl=-1.5%, hold=10m):")
    print(f"  {'Bucket':<12} {'Pairs':>6} {'Trades':>8} {'WR%':>6} {'AvgPnL%':>10}")
    print("  " + "-" * 48)
    bucket_agg = {}
    for pair, strat in all_scores_fwd.items():
        for bucket, s in strat.items():
            if bucket not in bucket_agg:
                bucket_agg[bucket] = {'n': 0, 'pairs': 0, 'wins': 0, 'total_pnl': 0.0}
            bucket_agg[bucket]['n']         += s['n']
            bucket_agg[bucket]['pairs']     += 1
            bucket_agg[bucket]['wins']      += int(s['wr'] / 100 * s['n'])
            bucket_agg[bucket]['total_pnl'] += s['mean_pnl'] * s['n']
    for bucket in sorted(bucket_agg.keys()):
        a = bucket_agg[bucket]
        if a['n'] > 0:
            agg_wr = a['wins'] / a['n'] * 100
            agg_mean = a['total_pnl'] / a['n']
            print(f"  {bucket:<12} {a['pairs']:>6} {a['n']:>8} {agg_wr:>6.1f} {agg_mean:>10.4f}")

    # Projected portfolio ROI if only trading positive EV coins
    total_trades = sum(bs['n_trades'] for _, (bp, bs) in positive_ev.items())
    total_pnl_usdt = sum(bs['total_pnl_usdt'] for _, (bp, bs) in positive_ev.items())
    # Each trade uses MAX_POSITION USDT from INITIAL_CAPITAL (with reinvestment)
    # Approximate: sequential trades on rolling capital
    projected_roi_pct = total_pnl_usdt / INITIAL_CAPITAL * 100

    print(f"\n  PROJECTED PORTFOLIO PERFORMANCE (Positive EV coins only):")
    print(f"  Total trades    : {total_trades}")
    print(f"  Total net PnL   : ${total_pnl_usdt:,.2f} USDT")
    print(f"  Projected ROI   : {projected_roi_pct:.2f}%")
    print(f"  Target ROI      : {TARGET_ROI*100:.0f}%")
    if projected_roi_pct >= TARGET_ROI * 100:
        print(f"  [OK] TARGET TERCAPAI!")
    else:
        print(f"  [WARN] Target belum tercapai. Gap: {TARGET_ROI*100 - projected_roi_pct:.2f}%")

    # Param distribution for positive EV coins
    if positive_ev:
        thr_vals  = [bp['threshold'] for bp, _ in positive_ev.values()]
        tp_vals   = [bp['tp'] for bp, _ in positive_ev.values()]
        sl_vals   = [bp['sl'] for bp, _ in positive_ev.values()]
        mh_vals   = [bp['max_hold'] for bp, _ in positive_ev.values()]
        print(f"\n  PARAM DISTRIBUTION (positive EV coins):")
        print(f"  Threshold  : median={np.median(thr_vals):.1f}  mean={np.mean(thr_vals):.2f}")
        print(f"  TP %       : median={np.median(tp_vals)*100:.2f}%  mean={np.mean(tp_vals)*100:.2f}%")
        print(f"  SL %       : median={np.median(sl_vals)*100:.2f}%  mean={np.mean(sl_vals)*100:.2f}%")
        print(f"  Max Hold   : median={np.median(mh_vals):.0f}m  mean={np.mean(mh_vals):.1f}m")

    # ── DB UPDATE ─────────────────────────────────────────────────────────────
    print(f"\n[UPDATE] Menulis optimal params ke DB...")
    updated, skipped = update_db(conn, pair_results)
    print(f"[OK] Updated: {updated} pairs | Disabled: {skipped} pairs (threshold=11.0)")

    # ── GLOBAL PROFILE UPDATE ─────────────────────────────────────────────────
    if positive_ev:
        # Set profile to the most common optimal params across positive EV coins
        from collections import Counter
        common_thr = Counter(int(bp['threshold']*10) for bp, _ in positive_ev.values()).most_common(1)[0][0] / 10.0
        common_mh  = int(np.median([bp['max_hold'] for bp, _ in positive_ev.values()]))

        cur = conn.cursor()
        cur.execute("""
            UPDATE backtest_profiles SET
              buy_threshold = %s,
              max_hold_minutes = %s,
              target_tp_pct = 0.010,
              target_sl_pct = -0.015
        """, (common_thr, common_mh))
        conn.commit()
        print(f"[OK] Profile updated: threshold={common_thr}, max_hold={common_mh}m, tp=1%, sl=-1.5%")

    conn.close()
    print(f"\n{'='*65}")
    print(f"  Selesai. Run backtester untuk validasi:")
    print(f"  docker compose run --rm backtester run")
    print(f"{'='*65}\n")


if __name__ == '__main__':
    main()
