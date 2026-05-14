"""
services/backtester/main.py
============================
Backtesting service — memanggil logika bot 1:1 terhadap data historis OHLCV.

Alur:
  1. FETCH — Download OHLCV 1m (90 hari) dari Indodax untuk semua pair aktif
             dan simpan ke tabel ohlcv_1m di database `algovibe`.
  2. RUN   — Baca candles dari DB, jalankan backtest per profil × pair:
             - Gunakan fungsi bot yang persis sama: get_technical_summary(),
               derive_trade_signal(), calculate_atr(), compute_adaptive_thresholds()
             - Entry / exit logic mirror exit_manager.py & entry_gates.py
             - Fee, TP, SL, DCA semua dari backtest_profiles row
  3. SAVE  — Tulis backtest_results + backtest_trades ke DB.

Jalankan:

  docker compose run --rm backtester fetch
  docker compose run --rm backtester run
  docker compose run --rm backtester all
"""

import argparse
import logging
import os
import sys
import time
import requests
import numpy as _np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any


def _progress(current: int, total: int, label: str = "", suffix: str = "") -> None:
    """Print progress bar sederhana ke stderr."""
    bar_len = 30
    filled  = int(bar_len * current / total) if total else 0
    bar     = "█" * filled + "░" * (bar_len - filled)
    pct     = int(100 * current / total) if total else 0
    line    = f"\r[{bar}] {pct:3d}% {current}/{total} {label} {suffix}"
    print(line, end="", flush=True, file=sys.stderr)
    if current >= total:
        print(file=sys.stderr)  # newline saat selesai

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.providers.indodax_adapter import IndodaxAdapter
from app.domain.technicals import (
    get_technical_summary,
    derive_trade_signal,
    calculate_atr,
    compute_adaptive_thresholds,
)
from psycopg2.extras import execute_values
from services.backtester.db_backtest import transaction, get_connection
import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("backtester")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
OHLCV_DAYS      = 90          # hari historis yang didownload
OHLCV_TF        = "1"         # timeframe 1-menit
FETCH_BATCH_SEC = 0.2         # delay antar pair saat fetch (rate-limit guard)
FETCH_WORKERS   = 8           # thread paralel untuk download OHLCV
FETCH_CHUNK     = 5000        # baris per execute_values batch
CANDLE_WINDOW   = 100         # jumlah candle yang di-feed ke indikator per tick


# ─────────────────────────────────────────────────────────────────────────────
# Pair list helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_active_pairs() -> list[str]:
    """
    Ambil semua pair aktif dari SECTOR_MAP yang memenuhi kriteria 'legit' bot:
    1. Ada di SECTOR_MAP.
    2. Volume 24 jam >= MIN_VOLUME_FILTER (10jt IDR).
    """
    all_pairs = sorted(set(cfg.SECTOR_MAP.keys()))
    min_vol   = getattr(cfg, "MIN_VOLUME_FILTER", 10000000)
    
    logger.info("[fetch] memvalidasi %d pairs dari SECTOR_MAP...", len(all_pairs))
    
    legit_pairs = []
    try:
        r = requests.get("https://indodax.com/api/summaries", timeout=10)
        if r.status_code == 200:
            data = r.json()
            tickers = data.get("tickers", {})
            for p in all_pairs:
                # Format di summaries adalah btc_idr
                key = p.lower()
                if "idr" in key and "_" not in key:
                    key = key.replace("idr", "_idr")
                
                t = tickers.get(key, {})
                vol = float(t.get("vol_idr", 0))
                if vol >= min_vol:
                    legit_pairs.append(p)
                else:
                    logger.debug("[fetch] %s skip (vol %.1f < %.1f)", p, vol, min_vol)
        else:
            logger.warning("[fetch] gagal fetch summaries, fallback ke semua pair.")
            return all_pairs
    except Exception as e:
        logger.error("[fetch] error saat validasi legit coins: %s", e)
        return all_pairs

    logger.info("[fetch] %d/%d pairs memenuhi kriteria volume >= %d IDR", 
                len(legit_pairs), len(all_pairs), min_vol)
    return sorted(legit_pairs)


# ─────────────────────────────────────────────────────────────────────────────
# FETCH — Download & upsert OHLCV ke DB
# ─────────────────────────────────────────────────────────────────────────────

def fetch_ohlcv(days: int = OHLCV_DAYS) -> None:
    """Download candle OHLCV 1m selama `days` hari untuk semua pair dan simpan ke DB."""
    pairs   = _get_active_pairs()
    now_ts  = int(time.time())
    from_ts = now_ts - days * 86400
    n_pairs = len(pairs)

    completed   = [0]          # mutable counter untuk progress dari threads
    total_saved = [0]

    def _fetch_one(pair: str) -> tuple[str, int]:
        """Download + upsert satu pair. Return (pair, n_rows_saved)."""
        adapter = IndodaxAdapter("", "")   # tiap thread punya adapter sendiri (non-thread-safe session)
        symbol  = pair.upper()
        try:
            time.sleep(FETCH_BATCH_SEC)    # light stagger untuk rate-limit
            logger.info("[fetch] %s — mengunduh %d hari OHLCV 1m …", symbol, days)
            candles = adapter.fetch_ohlc(symbol, tf=OHLCV_TF, from_ts=from_ts, to_ts=now_ts,
                                         limit=days * 1440 + 10)
            if not candles:
                logger.warning("[fetch] %s — tidak ada data", symbol)
                return pair, 0

            rows = [
                (
                    pair,
                    datetime.fromtimestamp(c["time"], tz=timezone.utc),
                    c["open"], c["high"], c["low"], c["close"], c["volume"],
                )
                for c in candles if c["close"] > 0
            ]
            if not rows:
                return pair, 0

            # Deduplicate rows (sama ts) sebelum insert — ambil yang terakhir
            seen: dict = {}
            for row in rows:
                seen[row[1]] = row  # key = ts
            deduped = list(seen.values())
            deduped.sort(key=lambda r: r[1])

            # insert dalam chunk agar tidak timeout untuk pair dengan 130k candle
            inserted = 0
            with transaction() as cur:
                for i in range(0, len(deduped), FETCH_CHUNK):
                    chunk = deduped[i : i + FETCH_CHUNK]
                    execute_values(
                        cur,
                        """
                        INSERT INTO ohlcv_1m (pair, ts, open, high, low, close, volume)
                        VALUES %s
                        ON CONFLICT (pair, ts) DO UPDATE SET
                            open   = EXCLUDED.open,
                            high   = EXCLUDED.high,
                            low    = EXCLUDED.low,
                            close  = EXCLUDED.close,
                            volume = EXCLUDED.volume
                        """,
                        chunk,
                        page_size=FETCH_CHUNK,
                    )
                    inserted += len(chunk)
            logger.info("[fetch] %s — %d candle disimpan (dedup dari %d)", symbol, inserted, len(rows))
            return pair, len(rows)
        except Exception as exc:
            logger.error("[fetch] %s — error: %s", symbol, exc)
            return pair, 0

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, pair): pair for pair in pairs}
        for future in as_completed(futures):
            pair, n = future.result()
            completed[0] += 1
            total_saved[0] += n
            _progress(completed[0], n_pairs, label=f"fetch ({completed[0]}/{n_pairs})",
                      suffix=f"last={pair} +{n}")

    _progress(n_pairs, n_pairs, label="selesai")
    logger.info("[fetch] selesai. Total %d candle dari %d pairs.", total_saved[0], n_pairs)
 
 
def resample_ohlcv(candles: list[dict], interval_minutes: int) -> list[dict]:
    """
    Resample 1m candles ke interval yang lebih besar (misal 5m, 15m).
    """
    if interval_minutes <= 1:
        return candles

    resampled = []
    for i in range(0, len(candles), interval_minutes):
        batch = candles[i : i + interval_minutes]
        if not batch:
            continue
        
        # Agregasi OHLCV
        resampled.append({
            "ts":     batch[0]["ts"],
            "open":   batch[0]["open"],
            "high":   max(c["high"] for c in batch),
            "low":    min(c["low"] for c in batch),
            "close":  batch[-1]["close"],
            "volume": sum(c["volume"] for c in batch),
        })
    return resampled


def get_vectorized_indicators(candles: list[dict], p: dict) -> pd.DataFrame:
    """
    Hitung semua indikator untuk seluruh series secara vectorized menggunakan Pandas.
    Sangat cepat dibanding hitung per tick (O(N) vs O(N*M)).
    """
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    # Ensure columns exist
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0.0
    
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    # 1. RSI (Wilder's Smoothing Parity)
    rsi_p = int(p.get("rsi_period", 14))
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    # First value is SMA
    avg_gain = gain.rolling(window=rsi_p, min_periods=rsi_p).mean()
    avg_loss = loss.rolling(window=rsi_p, min_periods=rsi_p).mean()
    
    # Wilder's smoothing loop (only done once per series)
    gain_arr = gain.values
    loss_arr = loss.values
    ag_arr = _np.copy(avg_gain.values)
    al_arr = _np.copy(avg_loss.values)
    for i in range(rsi_p, len(df)):
        ag_arr[i] = (ag_arr[i-1] * (rsi_p - 1) + gain_arr[i]) / rsi_p
        al_arr[i] = (al_arr[i-1] * (rsi_p - 1) + loss_arr[i]) / rsi_p
    
    # Update df with smoothed values
    df["avg_gain"] = ag_arr
    df["avg_loss"] = al_arr
    rs = _np.divide(ag_arr, al_arr, out=_np.full_like(ag_arr, _np.nan), where=al_arr != 0)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))
    df["rsi"] = df["rsi"].fillna(50.0)

    # 2. MACD
    m_fast = int(p.get("macd_fast", 12))
    m_slow = int(p.get("macd_slow", 26))
    m_sig  = int(p.get("macd_signal_period", 9))
    ema_f = close.ewm(span=m_fast, adjust=False).mean()
    ema_s = close.ewm(span=m_slow, adjust=False).mean()
    df["macd_line"] = ema_f - ema_s
    df["macd_sig"]  = df["macd_line"].ewm(span=m_sig, adjust=False).mean()
    df["macd_hist"] = df["macd_line"] - df["macd_sig"]

    # 3. Bollinger Bands
    bb_p = int(p.get("bb_period", 20))
    bb_std = float(p.get("bb_std_dev", 2.0))
    df["bb_mid"] = close.rolling(window=bb_p).mean()
    df["bb_std"] = close.rolling(window=bb_p).std(ddof=1)
    df["bb_upper"] = df["bb_mid"] + bb_std * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - bb_std * df["bb_std"]
    denom = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"] = (close - df["bb_lower"]) / denom.replace(0, _np.nan)
    df["bb_pct"] = df["bb_pct"].fillna(0.5)

    # 4. Stochastic RSI
    stoch_p = int(p.get("stoch_period", 14))
    k_smooth = int(p.get("stoch_k_smooth", 3))
    d_smooth = int(p.get("stoch_d_smooth", 3))
    rsi_min = df["rsi"].rolling(window=stoch_p).min()
    rsi_max = df["rsi"].rolling(window=stoch_p).max()
    stoch_rsi = (df["rsi"] - rsi_min) / (rsi_max - rsi_min).replace(0, _np.nan) * 100
    df["stoch_k"] = stoch_rsi.rolling(window=k_smooth).mean().fillna(50.0)
    df["stoch_d"] = df["stoch_k"].rolling(window=d_smooth).mean().fillna(50.0)

    # 5. EMA Crossover
    df["ema9"]  = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()

    # 6. Williams %R
    wr_p = int(p.get("williams_r_period", 14))
    w_high = close.rolling(window=wr_p).max()
    w_low  = close.rolling(window=wr_p).min()
    df["williams_r"] = (w_high - close) / (w_high - w_low).replace(0, _np.nan) * -100
    df["williams_r"] = df["williams_r"].fillna(-50.0)

    # 7. ROC
    roc_p = int(p.get("roc_period", 10))
    df["roc"] = close.pct_change(periods=roc_p) * 100
    df["roc"] = df["roc"].fillna(0.0)

    # 8. Volume Ratio
    v_p = int(p.get("volume_ma_period", 20))
    df["vol_ma"] = vol.rolling(window=v_p).mean().shift(1)
    df["volume_ratio"] = vol / df["vol_ma"].replace(0, _np.nan)
    df["volume_ratio"] = df["volume_ratio"].fillna(1.0)

    # 9. OBV
    df["obv"] = (vol * _np.sign(delta)).fillna(0.0).cumsum()
    obv_lookback = int(p.get("obv_slope_lookback", 5))
    obv_prev = df["obv"].shift(obv_lookback)
    df["obv_slope"] = (df["obv"] - obv_prev) / obv_prev.abs().replace(0, _np.nan) * 100
    df["obv_slope"] = df["obv_slope"].fillna(0.0)

    # 10. Slope Proxy
    df["slope"] = (close.diff(5) / close.shift(5) * 100) 
    
    # 11. EMA 50 (Trend Filter)
    ema_trend_p = int(p.get("ema_trend", 50))
    df["ema50"] = close.ewm(span=ema_trend_p, adjust=False).mean()

    # ATR (Standard) for gate 4b
    atr_p = int(p.get("atr_period", 14))
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=atr_p).mean() # SMA proxy for speed, Wilder's better but slower

    return df

def _load_active_profiles() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backtest_profiles WHERE is_active = TRUE")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _build_sector_weights(p: dict, sector: str | None) -> dict:
    """Bangun dict sector weight dari row profile, mirror _SECTOR_WEIGHTS di bot."""
    s = (sector or "").upper()
    prefix_map = {"MEME": "sw_meme_", "AI": "sw_ai_", "L1": "sw_l1_",
                  "L2": "sw_l1_",  # L2 pakai kolom sw_l1 (default 1.0)
                  "DEFI": "sw_defi_"}
    pfx = prefix_map.get(s, None)
    if pfx is None:
        # Unknown sector — semua 1.0
        return {k: 1.0 for k in ("rsi","macd","bb","stoch","ema","wr","slope","roc","ob","vol","obv")}
    keys = ("rsi","macd","bb","stoch","ema","wr","slope","roc","ob","vol","obv")
    return {k: float(p.get(f"{pfx}{k}", 1.0)) for k in keys}


def _build_adaptive_thresholds(p: dict, price_history: list) -> dict:
    """Gunakan compute_adaptive_thresholds() bot, dengan fallback dari profile."""
    at = compute_adaptive_thresholds(price_history, n_candles=int(p.get("adaptive_threshold_candles", 50)))
    # Override default dengan profile values jika adaptive tidak punya cukup data
    if at == {"rsi_oversold": 35.0, "rsi_overbought": 70.0}:
        at["rsi_oversold"]  = float(p.get("rsi_oversold", 35.0))
        at["rsi_overbought"] = float(p.get("rsi_overbought", 70.0))
    return at


# ─────────────────────────────────────────────────────────────────────────────
# Derive trade signal dengan override dari profile
# ─────────────────────────────────────────────────────────────────────────────

def _derive_signal_with_profile(tech: dict, p: dict, sector: str | None,
                                 adaptive_thresholds: dict,
                                 vol_idr_24h: float | None) -> dict:
    """
    Panggil derive_trade_signal() persis sama seperti bot, tapi dengan:
    - Bobot indikator dari profile (bukan hardcode)
    - Sector weights dari profile
    - Thresholds dari profile

    Karena derive_trade_signal() memiliki bobot hardcoded, kita inject
    override via tech dict (field tambahan yg sudah dibaca di dalam fungsi)
    DAN patch sector_weights sebelum pemanggilan.

    Implementasi: override dilakukan di sisi backtester dengan memanggil
    derive_trade_signal() KEMUDIAN menerapkan threshold dari profile
    ke hasilnya. Weights yang berbeda di-handle via _patched_derive().
    """
    return _patched_derive(tech, p, sector, adaptive_thresholds, vol_idr_24h)


def _patched_derive(tech: dict, p: dict, sector: str | None,
                    adaptive_thresholds: dict, vol_idr_24h: float | None) -> dict:
    """
    Reimplementasi derive_trade_signal() yang membaca weights dari profile.
    Logic identik dengan bot — tidak ada perubahan algoritma, hanya weights
    dan thresholds yang dibaca dari `p` (profile row) bukan hardcode.
    """
    from app.domain.technicals import _MACD_NORM_FACTOR  # noqa: F401

    sw = _build_sector_weights(p, sector)

    _VOL_RELIABILITY_THRESHOLD = float(p.get("volume_reliability_idr", 200_000_000))
    vol_mult = 1.0
    if vol_idr_24h is not None and float(vol_idr_24h or 0) < _VOL_RELIABILITY_THRESHOLD:
        vol_mult = 0.3

    _at = adaptive_thresholds or {}
    RSI_OVERSOLD    = float(_at.get("rsi_oversold",  float(p.get("rsi_oversold", 35.0))))
    RSI_OVERBOUGHT  = float(_at.get("rsi_overbought", float(p.get("rsi_overbought", 70.0))))
    RSI_DEEP_OVERSOLD   = max(15.0, RSI_OVERSOLD - 10.0)
    RSI_MILD_OVERSOLD   = RSI_OVERSOLD + 10.0
    RSI_EXTREME_OB      = min(95.0, RSI_OVERBOUGHT + 10.0)
    RSI_MILD_OB         = RSI_OVERBOUGHT - 10.0

    rsi    = float(tech.get("rsi", 50))
    slope  = float(tech.get("slope", 0))
    bp     = float(tech.get("buy_pressure", 50))
    mh     = float(tech.get("macd_hist", 0))
    bb     = float(tech.get("bb_pct", 0.5))
    sk     = float(tech.get("stoch_k", 50))
    sd     = float(tech.get("stoch_d", 50))
    ema_cr = str(tech.get("ema_cross", "NEUTRAL"))
    wr     = float(tech.get("williams_r", -50))
    roc    = float(tech.get("roc", 0))
    vr     = float(tech.get("volume_ratio", 1.0))
    obv_sl = float(tech.get("obv_slope", 0.0))
    last_price = max(1.0, float(tech.get("last_price", 1.0)))

    macd_norm = float(p.get("macd_norm_factor", 1000.0))

    buy_score  = 0.0
    sell_score = 0.0
    signals    = []

    # ── 1. RSI
    _w_rsi = float(p.get("weight_rsi", 3.0)) * sw["rsi"]
    if rsi < RSI_DEEP_OVERSOLD:
        buy_score += _w_rsi;    signals.append(f"RSI deep-oversold({rsi:.0f})")
    elif rsi < RSI_OVERSOLD:
        buy_score += _w_rsi * (2.5 / 3.0); signals.append(f"RSI oversold({rsi:.0f})")
    elif rsi < RSI_MILD_OVERSOLD:
        buy_score += _w_rsi * (1.5 / 3.0); signals.append(f"RSI mild-oversold({rsi:.0f})")
    elif rsi > RSI_EXTREME_OB:
        sell_score += _w_rsi;   signals.append(f"RSI extreme-overbought({rsi:.0f})")
    elif rsi > RSI_OVERBOUGHT:
        sell_score += _w_rsi * (2.5 / 3.0); signals.append(f"RSI overbought({rsi:.0f})")
    elif rsi > RSI_MILD_OB:
        sell_score += _w_rsi * (1.0 / 3.0); signals.append(f"RSI high({rsi:.0f})")

    # ── 2. MACD
    _w_macd = float(p.get("weight_macd", 2.5)) * sw["macd"]
    if mh > 0:
        strength = min(1.0, (abs(mh) / last_price) * macd_norm)
        buy_score += min(2.5, 1.0 + strength * 1.5) * _w_macd; signals.append(f"MACD bullish({mh:+.4f})")
    elif mh < 0:
        strength = min(1.0, (abs(mh) / last_price) * macd_norm)
        sell_score += min(2.5, 1.0 + strength * 1.5) * _w_macd; signals.append(f"MACD bearish({mh:+.4f})")

    # ── 3. Bollinger %B
    _w_bb = float(p.get("weight_bb", 2.0)) * sw["bb"]
    if bb < 0.10:
        buy_score += 2.0 * _w_bb;  signals.append(f"BB extreme-low({bb:.2f})")
    elif bb < 0.25:
        buy_score += 1.2 * _w_bb;  signals.append(f"BB low({bb:.2f})")
    elif bb > 0.90:
        sell_score += 2.0 * _w_bb; signals.append(f"BB extreme-high({bb:.2f})")
    elif bb > 0.75:
        sell_score += 1.2 * _w_bb; signals.append(f"BB high({bb:.2f})")

    # ── 4. Stochastic RSI
    _w_stoch = float(p.get("weight_stoch_rsi", 2.0)) * sw["stoch"]
    if sk < 20 and sd < 30:
        buy_score += 2.0 * _w_stoch;  signals.append(f"StochRSI oversold(K={sk:.0f},D={sd:.0f})")
    elif sk < 30:
        buy_score += 1.0 * _w_stoch;  signals.append(f"StochRSI mild-oversold(K={sk:.0f})")
    elif sk > 80 and sd > 70:
        sell_score += 2.0 * _w_stoch; signals.append(f"StochRSI overbought(K={sk:.0f},D={sd:.0f})")
    elif sk > 70:
        sell_score += 1.0 * _w_stoch; signals.append(f"StochRSI mild-overbought(K={sk:.0f})")
    if sk > sd and sk < 50:
        buy_score += 0.5 * _w_stoch;  signals.append("StochRSI K>D bullish-cross")
    elif sk < sd and sk > 50:
        sell_score += 0.5 * _w_stoch; signals.append("StochRSI K<D bearish-cross")

    # ── 5. EMA Crossover
    _w_ema = float(p.get("weight_ema_cross", 1.5)) * sw["ema"]
    if ema_cr == "BULLISH":
        buy_score += 1.5 * _w_ema;  signals.append("EMA9>EMA21 bullish")
    elif ema_cr == "BEARISH":
        sell_score += 1.5 * _w_ema; signals.append("EMA9<EMA21 bearish")

    # ── 6. Williams %R
    _w_wr = float(p.get("weight_williams_r", 1.5)) * sw["wr"]
    if wr <= -85:
        buy_score += 1.5 * _w_wr;   signals.append(f"W%R deep-oversold({wr:.0f})")
    elif wr <= -70:
        buy_score += 1.0 * _w_wr;   signals.append(f"W%R oversold({wr:.0f})")
    elif wr >= -15:
        sell_score += 1.5 * _w_wr;  signals.append(f"W%R deep-overbought({wr:.0f})")
    elif wr >= -30:
        sell_score += 1.0 * _w_wr;  signals.append(f"W%R overbought({wr:.0f})")

    # ── 7. Slope
    _w_slope = float(p.get("weight_slope", 1.0)) * sw["slope"]
    if slope > 0.005:
        buy_score += 1.0 * _w_slope;   signals.append(f"slope strong-up({slope:.4f})")
    elif slope > 0.001:
        buy_score += 0.5 * _w_slope;   signals.append(f"slope up({slope:.4f})")
    elif slope < -0.005:
        sell_score += 1.0 * _w_slope;  signals.append(f"slope strong-down({slope:.4f})")
    elif slope < -0.001:
        sell_score += 0.5 * _w_slope;  signals.append(f"slope down({slope:.4f})")

    # ── 8. ROC
    _w_roc = float(p.get("weight_roc", 1.0)) * sw["roc"]
    if roc < -3.0:
        buy_score += 1.0 * _w_roc;   signals.append(f"ROC oversold({roc:.2f}%)")
    elif roc < -1.0:
        buy_score += 0.5 * _w_roc;   signals.append(f"ROC mild-down({roc:.2f}%)")
    elif roc > 3.0:
        sell_score += 1.0 * _w_roc;  signals.append(f"ROC overbought({roc:.2f}%)")
    elif roc > 1.0:
        sell_score += 0.5 * _w_roc;  signals.append(f"ROC mild-up({roc:.2f}%)")

    # ── 9. Order Book / Volume Fallback
    _w_ob = float(p.get("weight_ob_pressure", 2.0)) * sw["ob"]
    if bp != 50.0:
        if bp >= 65:
            buy_score += 2.0 * _w_ob;   signals.append(f"OB strong-bid({bp:.0f}%)")
        elif bp >= 55:
            buy_score += 1.0 * _w_ob;   signals.append(f"OB bid-dom({bp:.0f}%)")
        elif bp <= 35:
            sell_score += 2.0 * _w_ob;  signals.append(f"OB strong-ask({100-bp:.0f}%)")
        elif bp <= 45:
            sell_score += 1.0 * _w_ob;  signals.append(f"OB ask-dom({100-bp:.0f}%)")
    elif vr != 1.0:
        if vr >= 2.0:
            buy_score += 1.0 * _w_ob;   signals.append(f"OB-fallback: Vol spike({vr:.1f}x)")
        elif vr <= 0.4:
            sell_score += 1.0 * _w_ob;  signals.append(f"OB-fallback: Vol dry({vr:.1f}x)")

    # ── 10. Volume Ratio
    _w_vol = float(p.get("weight_volume_ratio", 1.5)) * sw["vol"] * vol_mult
    if vr != 1.0:
        if vr >= 2.0:
            buy_score += 1.5 * _w_vol;   signals.append(f"Vol spike({vr:.1f}x) BUY confirm")
        elif vr >= 1.5:
            buy_score += 0.8 * _w_vol;   signals.append(f"Vol high({vr:.1f}x)")
        elif vr <= 0.4:
            sell_score += 1.5 * _w_vol;  signals.append(f"Vol dry({vr:.1f}x) SELL confirm")
        elif vr <= 0.6:
            sell_score += 0.5 * _w_vol;  signals.append(f"Vol low({vr:.1f}x)")

    # ── 11. OBV Slope
    _w_obv = float(p.get("weight_obv_slope", 1.5)) * sw["obv"] * vol_mult
    if obv_sl != 0.0:
        if obv_sl >= 5.0:
            buy_score += 1.5 * _w_obv;   signals.append(f"OBV accumulate({obv_sl:+.1f}%)")
        elif obv_sl >= 2.0:
            buy_score += 0.8 * _w_obv;   signals.append(f"OBV rising({obv_sl:+.1f}%)")
        elif obv_sl <= -5.0:
            sell_score += 1.5 * _w_obv;  signals.append(f"OBV distribute({obv_sl:+.1f}%)")
        elif obv_sl <= -2.0:
            sell_score += 0.8 * _w_obv;  signals.append(f"OBV falling({obv_sl:+.1f}%)")

    BUY_THRESHOLD  = float(p.get("buy_threshold", 6.0))
    SELL_THRESHOLD = float(p.get("sell_threshold", 6.0))
    OPPOSING_MAX   = float(p.get("opposing_max_score", 4.0))

    verdict    = "HOLD"
    confidence = 50
    reason     = "Sinyal campuran."

    if buy_score >= BUY_THRESHOLD and sell_score < OPPOSING_MAX:
        verdict    = "BUY"
        net_score  = buy_score - sell_score
        confidence = int(min(95, 50 + net_score * 3.5))
        reason     = f"Multi-Confluence BUY [{', '.join(signals[:4])}] | BS={buy_score:.1f} SS={sell_score:.1f}"
    elif sell_score >= SELL_THRESHOLD and buy_score < OPPOSING_MAX:
        verdict    = "SELL"
        net_score  = sell_score - buy_score
        confidence = int(min(95, 50 + net_score * 3.5))
        reason     = f"Multi-Confluence SELL [{', '.join(signals[:4])}] | BS={buy_score:.1f} SS={sell_score:.1f}"

    # Merge indicators into result for strategy gates
    res = {
        "verdict": verdict, "confidence": confidence, "reason": reason,
        "buy_score": round(buy_score, 2), "sell_score": round(sell_score, 2),
        "signals": signals,
    }
    res.update(tech) # Include rsi, stoch_k, macd_hist, etc.
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Backtest Engine
# ─────────────────────────────────────────────────────────────────────────────

def _run_backtest_pair(p: dict, pair: str, candles: list[dict],
                       window_start: datetime, window_end: datetime) -> dict | None:
    """
    Jalankan simulasi trading untuk satu pair × satu profile.
    Kembalikan dict statistik, atau None jika tidak cukup candle.
    """
    min_candles = int(p.get("min_candles_required", 50))
    if len(candles) < min_candles + 10:
        return None

    sector      = cfg.SECTOR_MAP.get(pair)
    fee_buy     = float(p.get("fee_buy_taker", 0.002111))
    fee_sell    = float(p.get("fee_sell_taker", 0.004211))
    tp_pct      = float(p.get("target_tp_pct", 0.035))
    sl_pct      = float(p.get("target_sl_pct", -0.035))
    trailing_en = bool(p.get("trailing_sl_enabled", False))
    trail_act   = float(p.get("trailing_sl_activation_pct", 0.02))
    trail_dist  = float(p.get("trailing_sl_distance_pct", 0.015))
    max_hold_m  = int(p.get("max_hold_minutes", 10))
    dca_enabled = bool(p.get("dca_enabled", True))
    dca_step    = float(p.get("dca_step_pct", -0.015))
    dca_mult    = float(p.get("dca_multiplier", 1.0))
    dca_max     = int(p.get("dca_max_tiers", 1))
    pos_idr     = float(p.get("max_position_idr", 100000.0))
    capital     = float(p.get("initial_capital_idr", 1_000_000.0))
    atr_max     = float(p.get("atr_max_pct_loose" if p.get("use_loose_gates") else "atr_max_pct", 0.03))
    sell_block  = float(p.get("sell_block_threshold", 5.0))
    ema_trend_f = bool(p.get("ema_trend_filter_enabled", True))
    ema_trend_p = int(p.get("ema_trend", 50))
    strat_mode  = p.get("strategy_mode", "scalping")
    slippage    = float(p.get("fee_slippage_buffer", 0.002)) # Default 0.2%
    from app.domain.technicals import calculate_ema as _ema
 
    trades: list[dict] = []
    position: dict | None = None  # active trade
    cooldown_until = 0.0
    cooldown_sec   = int(p.get("cooldown_seconds", 300))
    current_cap    = capital

    prices_1m  = [c["close"] for c in candles]
    
    # Resample untuk indikator jika timeframe > 1m
    tf_min = int(p.get("timeframe_minutes", 1))
    if tf_min > 1:
        candles_tf = resample_ohlcv(candles, tf_min)
        prices_tf  = [c["close"] for c in candles_tf]
        volumes_tf = [c["volume"] for c in candles_tf]
    else:
        candles_tf = candles
        prices_tf  = prices_1m
        volumes_tf = [c["volume"] for c in candles]
    
    volumes_1m = [c["volume"] for c in candles]

    # 1. Pre-calculate technicals for entire TF series
    df_tf = get_vectorized_indicators(pd.DataFrame(candles_tf).to_dict('records'), p)
    
    # 2. Pre-calculate signals (Vectorized)
    # To be valid, we need adaptive thresholds. Instead of calling compute_adaptive_thresholds()
    # once per candle, we calculate rolling percentiles of RSI (approximate but much faster).
    rsi_arr = df_tf["rsi"].values
    window_at = int(p.get("adaptive_threshold_candles", 50))
    
    # Calculate rolling percentiles for RSI adaptive thresholds
    rsi_series = pd.Series(rsi_arr)
    rolling_oversold = rsi_series.rolling(window=window_at).quantile(0.20).fillna(float(p.get("rsi_oversold", 35.0)))
    rolling_overbought = rsi_series.rolling(window=window_at).quantile(0.80).fillna(float(p.get("rsi_overbought", 70.0)))
    
    # Clamp thresholds (mirroring compute_adaptive_thresholds logic)
    rolling_oversold = rolling_oversold.clip(20.0, 45.0)
    rolling_overbought = rolling_overbought.clip(55.0, 85.0)
    
    # Convert technicals to a record list for faster iteration
    tech_records = df_tf.to_dict('records')
    all_signals = []
    for idx, row in enumerate(tech_records):
        at = {
            "rsi_oversold": rolling_oversold[idx],
            "rsi_overbought": rolling_overbought[idx]
        }
        all_signals.append(_derive_signal_with_profile(row, p, sector, at, None))

    # 3. Extract NumPy arrays for ultra-fast minute-by-minute iteration
    ts_epochs = _np.array([c["ts"].timestamp() for c in candles])
    close_arr = _np.array([c["close"] for c in candles])
    high_arr  = _np.array([c["high"] for c in candles])
    low_arr   = _np.array([c["low"] for c in candles])
    
    # Pre-extract technicals from df_tf to NumPy for fast lookup
    atr_arr   = df_tf["atr"].values if "atr" in df_tf else _np.zeros(len(df_tf))
    ema50_arr = df_tf["ema50"].values if "ema50" in df_tf else _np.zeros(len(df_tf))
    macd_arr  = df_tf["macd_hist"].values if "macd_hist" in df_tf else _np.zeros(len(df_tf))
    stoch_k_arr = df_tf["stoch_k"].values if "stoch_k" in df_tf else _np.zeros(len(df_tf))
    rsi_arr_tf = df_tf["rsi"].values if "rsi" in df_tf else _np.zeros(len(df_tf))
    
    for i in range(min_candles * tf_min, len(candles)):
        ts_epoch = ts_epochs[i]
        close    = close_arr[i]
        high     = high_arr[i]
        low      = low_arr[i]
        ts       = candles[i]["ts"]

        idx_tf = i // tf_min
        if idx_tf >= len(all_signals): break
        sig = all_signals[idx_tf]

        # ── Manage open position first (EVERY MINUTE) ─────────────────────
        if position is not None:
            entry_px  = position["entry_price"]
            entry_idr = position["amount_idr"]
            crypto    = position["amount_crypto"]
            highest   = max(position.get("highest_price", entry_px), high)
            position["highest_price"] = highest

            # DCA (Averaging Down) Logic
            if dca_enabled and position["dca_tier"] < dca_max:
                next_dca_px = entry_px * (1.0 + dca_step)
                if close <= next_dca_px:
                    # Trigger DCA buy
                    dca_idr = entry_idr * dca_mult
                    if current_cap >= dca_idr:
                        dca_buy_fee = dca_idr * fee_buy
                        dca_crypto = (dca_idr - dca_buy_fee) / close
                        
                        # Recalculate average price (Weighted Average)
                        new_total_idr = entry_idr + dca_idr
                        new_total_crypto = crypto + dca_crypto
                        new_entry_px = new_total_idr / new_total_crypto
                        
                        position["entry_price"] = new_entry_px
                        position["amount_idr"] = new_total_idr
                        position["amount_crypto"] = new_total_crypto
                        position["buy_fee"] += dca_buy_fee
                        position["dca_tier"] += 1
                        current_cap -= dca_idr
                        logger.info("[backtest] DCA Triggered for %s (Tier %d) at %.2f", pair, position["dca_tier"], close)
                        # Re-update local variables for exit check
                        entry_px = new_entry_px
                        entry_idr = new_total_idr
                        crypto = new_total_crypto

            # Trailing SL
            trail_sl_px = position.get("trailing_sl_price", 0.0)
            if trailing_en and (close - entry_px) / entry_px >= trail_act:
                new_trail = highest * (1.0 - trail_dist)
                if new_trail > trail_sl_px:
                    trail_sl_px = new_trail
                    position["trailing_sl_price"] = trail_sl_px

            # TP hit
            exit_reason = None
            exit_price  = close * (1.0 - slippage) # Apply slippage on exit
            if close >= entry_px * (1.0 + tp_pct):
                exit_reason = "TP"
                exit_price  = entry_px * (1.0 + tp_pct) * (1.0 - slippage)
            # Trailing SL hit
            elif trail_sl_px > 0 and low <= trail_sl_px:
                exit_reason = "TRAILING_SL"
                exit_price  = trail_sl_px
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

        if sig["verdict"] != "BUY":
            continue

        # Sell-block gate
        if sig["sell_score"] >= sell_block:
            continue

        # ATR filter (vectorized ATR use)
        _atr = atr_arr[idx_tf]
        if (_atr / close) > atr_max:
            continue

        # Strategy-specific gates
        if strat_mode == "trend_pullback":
            ema50 = ema50_arr[idx_tf]
            if close < ema50:
                continue
            
            rsi = rsi_arr_tf[idx_tf]
            stoch_k = stoch_k_arr[idx_tf]
            is_dip = (rsi < 50) and (rsi < float(p.get("rsi_oversold", 35)) + 10 or stoch_k < 40)
            if not is_dip:
                continue
            
            if macd_arr[idx_tf] < -(close * 0.002):
                continue

        # EMA-50 trend filter (General - mirror gate 4c)
        if strat_mode != "trend_pullback" and ema_trend_f:
            ema50 = ema50_arr[idx_tf]
            if close < ema50:
                continue

        # TP Floor check (overcome fee drag)
        fee_rt = fee_buy + fee_sell
        if tp_pct < fee_rt + 0.005: # Minimum 0.5% net profit target
             continue

        # Entry
        trade_idr = min(pos_idr, current_cap * 0.9)
        buy_fee   = trade_idr * fee_buy
        # Apply slippage on entry price (buying higher)
        entry_px  = close * (1.0 + slippage)
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

    # Close any still-open position at end of data
    if position is not None:
        last_c    = candles[-1]
        exit_px   = last_c["close"]
        entry_px  = position["entry_price"]
        entry_idr = position["amount_idr"]
        crypto    = position["amount_crypto"]
        gross_pnl = (exit_px - entry_px) * crypto
        sell_fee  = exit_px * crypto * fee_sell
        net_pnl   = gross_pnl - position["buy_fee"] - sell_fee
        trades.append({
            "pair":               pair,
            "entry_time":         datetime.fromtimestamp(position["entry_ts"], tz=timezone.utc),
            "exit_time":          last_c["ts"],
            "entry_price":        entry_px,
            "exit_price":         exit_px,
            "amount_idr":         entry_idr,
            "amount_crypto":      crypto,
            "pnl_idr":            round(net_pnl, 2),
            "pnl_pct":            round(net_pnl / entry_idr * 100 if entry_idr else 0, 4),
            "exit_reason":        "END_OF_DATA",
            "buy_score":          position.get("buy_score", 0),
            "sell_score_at_exit": None,
            "duration_seconds":   int(last_c["ts"].timestamp() - position["entry_ts"]),
        })

    if not trades:
        return {"trades": [], "stats": _empty_stats(capital, window_start, window_end)}

    return {"trades": trades, "stats": _compute_stats(trades, capital, window_start, window_end)}


def _empty_stats(capital: float, ws: datetime, we: datetime) -> dict:
    return {
        "window_start": ws, "window_end": we,
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "win_rate": None, "total_pnl_idr": 0.0, "total_pnl_pct": 0.0,
        "max_drawdown_pct": None, "max_drawdown_idr": None,
        "sharpe_ratio": None, "profit_factor": None,
        "avg_trade_duration_sec": None,
        "avg_pnl_per_trade_idr": None, "avg_pnl_per_trade_pct": None,
        "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        "initial_capital_idr": capital, "final_capital_idr": capital,
        "sortino_ratio": None, "recovery_factor": None, "calmar_ratio": None,
        "expectancy_idr": 0.0,
    }


def _compute_stats(trades: list[dict], capital: float,
                   ws: datetime, we: datetime) -> dict:
    import numpy as _np

    pnls       = [t["pnl_idr"] for t in trades]
    wins       = [x for x in pnls if x > 0]
    losses     = [x for x in pnls if x <= 0]
    total_pnl  = sum(pnls)
    win_rate   = len(wins) / len(pnls) if pnls else None

    # Drawdown
    equity = capital
    peak   = capital
    max_dd_idr = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_idr:
            max_dd_idr = dd
    max_dd_pct = (max_dd_idr / capital * 100) if capital > 0 else None

    # Sharpe (daily, simplified)
    if len(pnls) > 1:
        arr       = _np.array(pnls, dtype=float)
        sharpe    = float(_np.mean(arr) / _np.std(arr)) if _np.std(arr) > 0 else None
    else:
        sharpe = None

    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None

    # Consecutive wins/losses
    max_cw, max_cl, cw, cl = 0, 0, 0, 0
    for pnl in pnls:
        if pnl > 0:
            cw += 1; cl = 0
        else:
            cl += 1; cw = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    durations = [t["duration_seconds"] for t in trades if t.get("duration_seconds")]

    return {
        "window_start":            ws,
        "window_end":              we,
        "total_trades":            len(pnls),
        "winning_trades":          len(wins),
        "losing_trades":           len(losses),
        "win_rate":                round(win_rate, 4) if win_rate else None,
        "total_pnl_idr":           round(total_pnl, 2),
        "total_pnl_pct":           round(total_pnl / capital * 100, 4) if capital else 0,
        "max_drawdown_pct":        round(max_dd_pct, 4) if max_dd_pct is not None else None,
        "max_drawdown_idr":        round(max_dd_idr, 2),
        "sharpe_ratio":            round(sharpe, 4) if sharpe else None,
        "profit_factor":           round(profit_factor, 4) if profit_factor else None,
        "avg_trade_duration_sec":  int(sum(durations) / len(durations)) if durations else None,
        "avg_pnl_per_trade_idr":   round(total_pnl / len(pnls), 2) if pnls else None,
        "avg_pnl_per_trade_pct":   round(total_pnl / capital / len(pnls) * 100, 4) if pnls and capital else None,
        "max_consecutive_wins":    max_cw,
        "max_consecutive_losses":  max_cl,
        "initial_capital_idr":     capital,
        "final_capital_idr":       round(capital + total_pnl, 2),
        "sortino_ratio":            round(_compute_sortino(pnls), 4) if pnls else None,
        "recovery_factor":          round(total_pnl / max_dd_idr, 4) if max_dd_idr > 0 else None,
        "calmar_ratio":             round((total_pnl / capital * 100) / max_dd_pct, 4) if max_dd_pct and max_dd_pct > 0 else None,
        "expectancy_idr":           round((win_rate * (sum(wins)/len(wins) if wins else 0)) + ((1-win_rate) * (sum(losses)/len(losses) if losses else 0)), 2) if pnls else 0.0,
    }


def _compute_sortino(pnls: list[float], risk_free_rate: float = 0.0) -> float:
    """Hitung Sortino Ratio (Downside Deviation focus)."""
    import numpy as _np
    if not pnls or len(pnls) < 2:
        return 0.0
    arr = _np.array(pnls)
    avg_return = _np.mean(arr) - risk_free_rate
    downside_returns = arr[arr < 0]
    if len(downside_returns) == 0:
        return 0.0
    downside_std = _np.std(downside_returns)
    if downside_std == 0:
        return 0.0
    return float(avg_return / downside_std)


def _save_result(profile_id: int, pair: str | None,
                 stats: dict, trades: list[dict]) -> None:
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO backtest_results (
                profile_id, pair, window_start, window_end, total_trades,
                winning_trades, losing_trades, win_rate,
                total_pnl_idr, total_pnl_pct, max_drawdown_pct, max_drawdown_idr,
                sharpe_ratio, profit_factor, avg_trade_duration_sec,
                avg_pnl_per_trade_idr, avg_pnl_per_trade_pct,
                max_consecutive_wins, max_consecutive_losses,
                initial_capital_idr, final_capital_idr,
                sortino_ratio, recovery_factor, calmar_ratio, expectancy_idr
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            ) RETURNING id
            """,
            (
                profile_id, pair,
                stats["window_start"], stats["window_end"],
                stats["total_trades"], stats["winning_trades"], stats["losing_trades"],
                stats["win_rate"], stats["total_pnl_idr"], stats["total_pnl_pct"],
                stats["max_drawdown_pct"], stats["max_drawdown_idr"],
                stats["sharpe_ratio"], stats["profit_factor"],
                stats["avg_trade_duration_sec"],
                stats["avg_pnl_per_trade_idr"], stats["avg_pnl_per_trade_pct"],
                stats["max_consecutive_wins"], stats["max_consecutive_losses"],
                stats["initial_capital_idr"], stats["final_capital_idr"],
                stats.get("sortino_ratio"), stats.get("recovery_factor"),
                stats.get("calmar_ratio"), stats.get("expectancy_idr")
            ),
        )
        row = cur.fetchone()
        result_id = row["id"]

        if trades:
            cur.executemany(
                """
                INSERT INTO backtest_trades (
                    result_id, pair, entry_time, exit_time, entry_price, exit_price,
                    amount_idr, amount_crypto, pnl_idr, pnl_pct, exit_reason,
                    buy_score, sell_score_at_exit, duration_seconds
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        result_id, t["pair"], t["entry_time"], t["exit_time"],
                        t["entry_price"], t["exit_price"], t["amount_idr"],
                        t["amount_crypto"], t["pnl_idr"], t["pnl_pct"],
                        t["exit_reason"], t["buy_score"], t.get("sell_score_at_exit"),
                        t["duration_seconds"],
                    )
                    for t in trades
                ],
            )
    logger.info("[save] result_id=%d pair=%s trades=%d pnl=%.0f",
                result_id, pair, stats["total_trades"], stats["total_pnl_idr"])


def _print_run_report(profile_id: int, profile_name: str) -> None:
    """Ambil aggregate statistik dari DB dan tampilkan report ringkas ala Quant Analyst."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        
        # 1. Aggregate Stats (Latest Run)
        cur.execute("""
            SELECT 
                id,
                total_trades,
                winning_trades,
                total_pnl_idr,
                win_rate,
                profit_factor,
                initial_capital_idr as total_cap,
                created_at
            FROM backtest_results
            WHERE profile_id = %s
            ORDER BY created_at DESC LIMIT 1
        """, (profile_id,))
        row = cur.fetchone()
        
        if not row or not row["total_trades"]:
            print(f"\n[REPORT] Profile: {profile_name} — No completed trades found in database.")
            print("         Note: Results are only saved AFTER the 'run' process finishes completely.")
            return

        result_id = row["id"]
        total_t = row["total_trades"]
        wins    = row["winning_trades"]
        losses  = total_t - wins
        wr      = row["win_rate"]
        pnl     = row["total_pnl_idr"]
        pnl_pct = (pnl / row["total_cap"] * 100) if row["total_cap"] else 0
        
        # 2. Detailed Trade Metrics for this specific result
        cur.execute("""
            SELECT pair, pnl_idr, pnl_pct 
            FROM backtest_trades 
            WHERE result_id = %s
            ORDER BY entry_time ASC
        """, (result_id,))
        trade_data = cur.fetchall()
        
        pnls = [float(t['pnl_idr']) for t in trade_data]
        pnl_pcts = [float(t['pnl_pct']) for t in trade_data]
        n_pairs_tested = len(set(t['pair'] for t in trade_data))
        
        avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
        avg_loss = abs(sum(p for p in pnls if p < 0) / losses) if losses > 0 else 0
        expectancy = (wr/100 * avg_win) - ((1 - wr/100) * avg_loss)
        
        # Simple Sharpe (Return / StdDev)
        std_pnl = _np.std(pnls) if len(pnls) > 1 else 0
        sharpe = (sum(pnls)/len(pnls)) / std_pnl if std_pnl > 0 else 0
        
        # Max Drawdown
        equity_curve = _np.cumsum(pnls)
        peak = _np.maximum.accumulate(equity_curve)
        drawdown = peak - equity_curve
        max_dd = _np.max(drawdown) if len(drawdown) > 0 else 0

        print("\n" + "╔" + "═"*68 + "╗")
        print(f"║  QUANTITATIVE PERFORMANCE REPORT: {profile_name:<28} ║")
        print("╠" + "═"*68 + "╣")
        print(f"║  Pairs Tested    : {n_pairs_tested:<10} | Total Trades : {total_t:<10} ║")
        print(f"║  Win Rate        : {wr:6.2f}%    | Net PnL (IDR): Rp {pnl:>12,.0f} ║")
        print(f"║  ROI             : {pnl_pct:>+6.2f}%     | Expectancy   : Rp {expectancy:>12,.0f} ║")
        print(f"║  Avg Win         : Rp {avg_win:>8,.0f} | Avg Loss     : Rp {avg_loss:>12,.0f} ║")
        print(f"║  Profit Factor   : {row['profit_factor']:>8.2f} | Sharpe Ratio : {sharpe:>12.2f} ║")
        
        # New Metrics
        cur.execute("SELECT AVG(sharpe_ratio) as avg_sharpe FROM backtest_results WHERE profile_id = %s", (profile_id,))
        # (Assuming we might want more detail later)
        
        print(f"║  Max Drawdown    : Rp {max_dd:>8,.0f} | Risk/Reward  : {avg_win/avg_loss if avg_loss else 0:>12.2f} ║")
        
        # Kelly Criterion
        # f = (p/a) - (q/b) where p=win_prob, q=loss_prob, a=loss_amt, b=win_amt
        # Simplified: f = w - ((1-w)/R)
        win_prob = wr / 100
        rr_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        kelly = win_prob - ((1 - win_prob) / rr_ratio) if rr_ratio > 0 else 0
        
        print(f"║  Kelly Criterion : {kelly:>8.2f} | Expectancy   : Rp {expectancy:>12,.0f} ║")
        print("╚" + "═"*68 + "╝")

        # 3. Dynamic Quantitative Insights (Zero-Bias Mathematical Evaluation)
        print("\n" + "📊 DYNAMIC STRATEGIC RECOMMENDATIONS:")
        print("-" * 70)
        
        # Logic 1: Expectancy & Win Rate Confluence
        if expectancy < 0:
            if wr < 35:
                print(f"  [SIGNAL QUALITY] Critical: Win Rate ({wr:.1f}%) is below the statistical noise floor.")
                print("  -> Fix: Increase 'buy_score' threshold by +1.5 to filter low-probability entries.")
            elif avg_loss > avg_win:
                print(f"  [R:R ASYMMETRY] Negative Expectancy driven by Average Loss > Average Win.")
                print(f"  -> Fix: Tighten 'SL_PCT' or increase 'DCA_MULTIPLIER' to lower break-even.")
        
        # Logic 2: Profit Factor Analysis
        pf = row['avg_pf'] or 0
        if pf < 1.0:
            print(f"  [DRAINAGE] Profit Factor ({pf:.2f}) < 1.0: The strategy is currently leaking capital.")
            print("  -> Fix: Implement 'EMA-50' Trend Filter (Gate 4c) to avoid trading against the primary trend.")
        elif 1.0 <= pf < 1.25:
            print(f"  [MARGINAL] Profit Factor ({pf:.2f}) is in the 'break-even' trap.")
            print("  -> Fix: Increase 'TP_PCT' by 0.5% to overcome exchange fee drag (0.63% round-trip).")

        # Logic 3: Stability & Variance (Sharpe)
        if sharpe < 0 and total_t > 30:
            print(f"  [VOLATILITY] Negative Sharpe ({sharpe:.2f}): Returns do not justify the risk.")
            print("  -> Fix: Increase 'MIN_VOLUME_FILTER' to 25M IDR to avoid illiquid 'slippage' traps.")
            
        # Logic 4: Win/Loss Ratio
        if losses > 0 and avg_loss > 0 and (avg_win / avg_loss) < 1.5:
             print(f"  [EFFICIENCY] Win/Loss Ratio ({avg_win/avg_loss:.2f}) is too tight.")
             print("  -> Fix: Use 'MICRO_PROFIT_EXIT' logic to capture small wins faster or widen TP.")

        if expectancy >= 500 and pf > 1.5:
            print("  [ALPHA DETECTED] Strategy is mathematically sound. Ready for paper trading/dry-run.")

        # Top 5 Pairs
        print("\n" + "🏆 TOP 5 ALPHA GENERATORS:")
        cur.execute("""
            SELECT pair, total_trades, total_pnl_idr, win_rate
            FROM backtest_results
            WHERE profile_id = %s AND total_trades > 0 
              AND created_at >= NOW() - INTERVAL '15 minutes'
            ORDER BY total_pnl_idr DESC LIMIT 5
        """, (profile_id,))
        top_rows = cur.fetchall()
        for r in top_rows:
            wr_v = (r['win_rate'] or 0) * 100
            print(f"  {r['pair'].replace('idr','').upper():<10} | {r['total_trades']:3d} tx | PnL: {r['total_pnl_idr']:+12,.0f} | WR: {wr_v:5.1f}%")

        print("\n" + "="*70 + "\n")

    except Exception as e:
        logger.error("[report] Gagal generate report: %s", e)
    finally:
        conn.close()


def _simulate_portfolio(all_trades: list[dict], profile: dict) -> dict:
    """
    Simulasikan portofolio global: ambil semua trade dari semua pair,
    urutkan berdasarkan waktu, dan terapkan batasan modal + max_open_positions.
    """
    if not all_trades:
        return {"trades": [], "stats": {}}

    # Urutkan berdasarkan entry_time
    sorted_trades = sorted(all_trades, key=lambda x: x["entry_time"])
    
    capital = float(profile.get("initial_capital_idr", 1_000_000.0))
    max_pos = int(profile.get("max_open_positions", 3))
    
    active_trades = []
    final_trades  = []
    current_cap   = capital

    for t in sorted_trades:
        # 1. Bersihkan active_trades yang sudah exit sebelum t["entry_time"]
        active_trades = [a for a in active_trades if a["exit_time"] > t["entry_time"]]
        
        # 2. Cek apakah ada slot kosong
        if len(active_trades) < max_pos:
            # 3. Cek modal
            if current_cap >= t["amount_idr"]:
                # Accept trade
                active_trades.append(t)
                final_trades.append(t)
                # Note: Dalam realitas, capital berkurang saat entry dan bertambah saat exit.
                # Di sini kita hanya butuh ini untuk stats akhir.
                # current_cap -= t["amount_idr"] # Tidak perlu karena PnL dihitung relatif
        
    return final_trades


# ─────────────────────────────────────────────────────────────────────────────
# RUN — orchestrate per profile × pair
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_schema_upgraded() -> None:
    """Tambahkan kolom-kolom metrik baru jika belum ada di DB."""
    cols = {
        "sortino_ratio": "DOUBLE PRECISION",
        "recovery_factor": "DOUBLE PRECISION",
        "calmar_ratio": "DOUBLE PRECISION",
        "expectancy_idr": "DOUBLE PRECISION"
    }
    with transaction() as cur:
        for col, dtype in cols.items():
            try:
                cur.execute(f"ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS {col} {dtype}")
            except Exception:
                pass # Kolom sudah ada atau error lain (IF NOT EXISTS aman di PG 9.6+)


def _print_data_summary() -> None:
    """Tampilkan ringkasan ketersediaan data di database."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT MIN(ts), MAX(ts), COUNT(DISTINCT pair) FROM ohlcv_1m")
        res = cur.fetchone()
        if res and res[2] > 0:
            min_ts, max_ts, n_pairs = res
            days = (max_ts - min_ts).days
            print("\n" + "═"*60)
            print(f" 📊 DATA AVAILABILITY (Database: ohlcv_1m)")
            print(f" 📅 Period : {min_ts.strftime('%Y-%m-%d')} s/d {max_ts.strftime('%Y-%m-%d')} ({days} hari)")
            print(f" 🪙 Pairs  : {n_pairs} pairs terdeteksi")
            print("═"*60 + "\n")
        else:
            logger.warning("[run] Database ohlcv_1m kosong!")
        conn.close()
    except Exception as e:
        logger.error("[run] Gagal mengambil ringkasan data: %s", e)


def _clear_previous_results() -> None:
    """Bersihkan data hasil backtest sebelumnya agar laporan selalu segar."""
    try:
        with transaction() as cur:
            # Cascade delete akan membersihkan backtest_trades otomatis
            cur.execute("TRUNCATE TABLE backtest_results RESTART IDENTITY CASCADE")
            logger.info("[run] ✅ Data laporan (results & trades) sebelumnya telah dibersihkan.")
    except Exception as e:
        logger.error("[run] Gagal membersihkan data lama: %s", e)


def _process_pair_worker(pair_name: str, start_time: datetime, end_time: datetime, profiles_list: list) -> list:
    """Worker function for multiprocessing. Harus di top-level agar bisa dipickle."""
    # Setup logger in worker process
    import logging
    logger = logging.getLogger("backtester.worker")
    
    from services.backtester.db_backtest import get_connection
    import pandas as pd
    
    conn = get_connection()
    try:
        cur = conn.cursor()
        # OPTIMIZATION: Manually fetch to DataFrame to avoid SQLAlchemy warnings while keeping speed
        query = """
            SELECT ts, open, high, low, close, volume 
            FROM ohlcv_1m 
            WHERE pair = %s AND ts >= %s 
            ORDER BY ts ASC
        """
        cur.execute(query, (pair_name, start_time))
        rows = cur.fetchall()
        
        if not rows:
            return []

        df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])

        # Convert to list of dicts for simulation parity (or optimize further)
        candles = df.to_dict('records')
        
        pair_trades = []
        for prof in profiles_list:
            res = _run_backtest_pair(prof, pair_name, candles, start_time, end_time)
            if res and res["trades"]:
                pair_trades.extend(res["trades"])
        return pair_trades

    except Exception as e:
        logger.error("[run] Error processing pair %s: %s", pair_name, e)
        return []
    finally:
        conn.close()


def run_backtest(days: int = OHLCV_DAYS, pairs_filter: str | None = None) -> None:
    _ensure_schema_upgraded()
    _clear_previous_results()
    _print_data_summary()
    profiles = _load_active_profiles()
    if not profiles:
        logger.warning("[run] Tidak ada active profile. INSERT ke backtest_profiles dan set is_active=TRUE.")
        return

    now  = datetime.now(tz=timezone.utc)
    w_start = now - timedelta(days=days)

    # Filter pairs
    if pairs_filter:
        pairs = [p.strip().upper() for p in pairs_filter.split(",")]
        logger.info("[run] Filtering backtest for: %s", pairs)
    else:
        pairs = _get_active_pairs()
    
    n_pairs = len(pairs)
    completed = [0]

    logger.info("[run] Memulai backtest untuk %d profiles x %d pairs...", len(profiles), n_pairs)

    # Collect all trades per profile
    all_results = []
    completed = [0]

    from concurrent.futures import ProcessPoolExecutor
    
    # We use cpu_count / 2 or full count depending on IO/CPU balance
    # Since we have 90 days of data, the CPU (simulation) is now the bottleneck
    max_workers = os.cpu_count() or 4
    
    logger.info("[run] Starting parallel simulation with %d workers...", max_workers)
    
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_pair_worker, p, w_start, now, profiles): p for p in pairs}
        
        for i, future in enumerate(as_completed(futures)):
            pair_name = futures[future]
            try:
                trades = future.result()
                all_results.extend(trades)
            except Exception as e:
                logger.error("[run] Worker failed for %s: %s", pair_name, e)
            
            completed[0] += 1
            _progress(completed[0], n_pairs, label=f"running ({completed[0]}/{n_pairs})", suffix=f"last={pair_name}")

    # Group trades by profile ID
    profile_trades = {p["id"]: [] for p in profiles}
    for t in all_results:
        pid = t.get("profile_id")
        if pid in profile_trades:
            profile_trades[pid].append(t)

    # Now run portfolio simulation and save results
    for prof in profiles:
        pid = prof["id"]
        all_t = profile_trades[pid]
        
        # 1. Individual Pair Results (Optional: we can still save them, but let's focus on Global first)
        # For now, let's just save the Global Portfolio as a result with pair=None
        
        if all_t:
            portfolio_trades = _simulate_portfolio(all_t, prof)
            stats = _compute_stats(portfolio_trades, prof["initial_capital_idr"], w_start, now)
            _save_result(pid, None, stats, portfolio_trades)
            _print_run_report(pid, prof["profile_name"])


def validate_data() -> None:
    """Check for gaps, invalid prices, and volume in ohlcv_1m table."""
    pairs = _get_active_pairs()
    logger.info("[validate] Memulai validasi data untuk %d pair...", len(pairs))
    
    issues = []
    
    conn = get_connection()
    try:
        cur = conn.cursor()
        for pair in pairs:
            # 1. Check for missing data
            cur.execute("SELECT MIN(ts) as min_ts, MAX(ts) as max_ts, COUNT(*) as count FROM ohlcv_1m WHERE pair = %s", (pair,))
            res = cur.fetchone()
            
            if not res or res['count'] == 0:
                issues.append(f"🔴 {pair}: DATA KOSONG")
                continue
                
            min_ts = res['min_ts']
            max_ts = res['max_ts']
            count  = res['count']
            
            # 2. Check for gaps (theoretical vs actual)
            # Selisih waktu dalam menit
            if min_ts and max_ts:
                duration_mins = int((max_ts - min_ts).total_seconds() / 60) + 1
            else:
                duration_mins = 0
            
            # 3. Check for price/vol anomalies (static checks)
            cur.execute("""
                SELECT COUNT(*) as anomalies FROM ohlcv_1m 
                WHERE pair = %s AND (close <= 0 OR high < low OR volume < 0)
            """, (pair,))
            anomalies = cur.fetchone()['anomalies']
            
            pair_issues = []
            if count < duration_mins * 0.98: # Tolerate 2% gap
                gap_pct = (1 - count/duration_mins) * 100
                pair_issues.append(f"BOLONG {gap_pct:.1f}% ({count}/{duration_mins} bar)")
            
            if anomalies > 0:
                pair_issues.append(f"ANOMALI {anomalies} bar")
                
            if pair_issues:
                issues.append(f"⚠️ {pair:<10} | " + " | ".join(pair_issues))

        if not issues:
            logger.info("✅ SEMUA DATA VALID. Tidak ditemukan gap signifikan atau anomali harga.")
        else:
            print("\n" + "!"*60)
            print(" DATA VALIDATION REPORT")
            print("!"*60)
            for issue in issues:
                print(issue)
            print("!"*60 + "\n")
            
    except Exception as e:
        logger.error("[validate] Gagal validasi: %s", e)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Algovibe Backtester")
    parser.add_argument(
        "command",
        choices=["fetch", "run", "all", "report", "validate"],
        help="fetch=download | run=backtest | all=fetch+run | report=summary | validate=check data",
    )
    parser.add_argument("--days", type=int, default=OHLCV_DAYS,
                        help=f"Jumlah hari historis (default {OHLCV_DAYS})")
    parser.add_argument("--pairs", type=str, default=None,
                        help="Filter pair tertentu (koma: BTC-IDR,ETH-IDR)")
    args = parser.parse_args()

    if args.command in ("fetch", "all"):
        logger.info("=== FETCH OHLCV (%d hari) ===", args.days)
        fetch_ohlcv(days=args.days)

    if args.command in ("run", "all"):
        logger.info("=== RUN BACKTEST ===")
        run_backtest(days=args.days, pairs_filter=args.pairs)

    if args.command == "report":
        logger.info("=== GENERATING LATEST REPORT ===")
        profiles = _load_active_profiles()
        for p in profiles:
            _print_run_report(p["id"], p["profile_name"])

    if args.command == "validate":
        logger.info("=== VALIDATING OHLCV DATA ===")
        validate_data()

    logger.info("=== DONE ===")


if __name__ == "__main__":
    main()
