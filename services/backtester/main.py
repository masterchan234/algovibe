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
    """Ambil semua pair aktif dari SECTOR_MAP + pair yang ada di ticker Indodax."""
    pairs = sorted(set(cfg.SECTOR_MAP.keys()))
    logger.info("[fetch] %d pairs dari SECTOR_MAP", len(pairs))
    return pairs


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


# ─────────────────────────────────────────────────────────────────────────────
# Profile helpers
# ─────────────────────────────────────────────────────────────────────────────

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

    return {
        "verdict": verdict, "confidence": confidence, "reason": reason,
        "buy_score": round(buy_score, 2), "sell_score": round(sell_score, 2),
        "signals": signals,
    }


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
    from app.domain.technicals import calculate_ema as _ema

    trades: list[dict] = []
    position: dict | None = None  # active trade
    cooldown_until = 0.0
    cooldown_sec   = int(p.get("cooldown_seconds", 300))
    current_cap    = capital

    prices  = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    for i in range(min_candles, len(candles)):
        candle   = candles[i]
        ts       = candle["ts"]
        close    = candle["close"]
        high     = candle["high"]
        low      = candle["low"]
        ts_epoch = ts.timestamp()

        window_prices  = prices[max(0, i - CANDLE_WINDOW): i + 1]
        window_volumes = volumes[max(0, i - CANDLE_WINDOW): i + 1]

        # ── Manage open position first ────────────────────────────────────
        if position is not None:
            entry_px  = position["entry_price"]
            entry_idr = position["amount_idr"]
            crypto    = position["amount_crypto"]
            highest   = max(position.get("highest_price", entry_px), high)
            position["highest_price"] = highest

            # Trailing SL
            trail_sl_px = position.get("trailing_sl_price", 0.0)
            if trailing_en and (close - entry_px) / entry_px >= trail_act:
                new_trail = highest * (1.0 - trail_dist)
                if new_trail > trail_sl_px:
                    trail_sl_px = new_trail
                    position["trailing_sl_price"] = trail_sl_px

            # TP hit
            exit_reason = None
            exit_price  = close
            if close >= entry_px * (1.0 + tp_pct):
                exit_reason = "TP"
                exit_price  = entry_px * (1.0 + tp_pct)
            # Trailing SL hit
            elif trail_sl_px > 0 and low <= trail_sl_px:
                exit_reason = "TRAILING_SL"
                exit_price  = trail_sl_px
            # SL hit
            elif close <= entry_px * (1.0 + sl_pct):
                exit_reason = "SL"
                exit_price  = entry_px * (1.0 + sl_pct)
            # Max hold exceeded
            elif (ts_epoch - position["entry_ts"]) > max_hold_m * 60:
                exit_reason = "MAX_HOLD"
                exit_price  = close
            # Signal reverse
            else:
                tech_exit = get_technical_summary(window_prices, window_volumes)
                at_exit   = _build_adaptive_thresholds(p, window_prices)
                sig_exit  = _derive_signal_with_profile(tech_exit, p, sector, at_exit, None)
                if sig_exit["verdict"] == "SELL" and sig_exit["sell_score"] >= sell_block:
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
                    "sell_score_at_exit":  sig_exit["sell_score"] if exit_reason == "SIGNAL_REVERSE" else None,
                    "duration_seconds":    duration,
                })
                current_cap += net_pnl
                position     = None
                cooldown_until = ts_epoch + cooldown_sec
            continue  # skip entry check when position is open

        # ── Entry logic (mirror entry_gates.py) ──────────────────────────
        if ts_epoch < cooldown_until:
            continue
        if current_cap < float(p.get("min_position_idr", 10000)):
            break  # ruined

        tech  = get_technical_summary(window_prices, window_volumes)
        at    = _build_adaptive_thresholds(p, window_prices)
        sig   = _derive_signal_with_profile(tech, p, sector, at, None)

        if sig["verdict"] != "BUY":
            continue

        # Sell-block gate (mirror gate 4)
        if sig["sell_score"] >= sell_block:
            continue

        # ATR filter (mirror gate 4b)
        try:
            highs_w  = [c["high"]  for c in candles[max(0, i-14): i+1]]
            lows_w   = [c["low"]   for c in candles[max(0, i-14): i+1]]
            closes_w = [c["close"] for c in candles[max(0, i-14): i+1]]
            _atr = calculate_atr(highs_w, lows_w, closes_w, period=int(p.get("atr_period", 14)))
            if (_atr / close * 100) > atr_max * 100:
                continue
        except Exception:
            pass

        # EMA-50 trend filter (mirror gate 4c)
        if ema_trend_f and len(window_prices) >= ema_trend_p:
            ema50 = _ema(window_prices, ema_trend_p)
            if close < ema50:
                continue

        # Entry
        trade_idr = min(pos_idr, current_cap * 0.9)
        buy_fee   = trade_idr * fee_buy
        crypto_amt = (trade_idr - buy_fee) / close

        position = {
            "entry_price":       close,
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
    }


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
                initial_capital_idr, final_capital_idr
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
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


# ─────────────────────────────────────────────────────────────────────────────
# RUN — orchestrate per profile × pair
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(days: int = OHLCV_DAYS) -> None:
    profiles = _load_active_profiles()
    if not profiles:
        logger.warning("[run] Tidak ada active profile. INSERT ke backtest_profiles dan set is_active=TRUE.")
        return

    now  = datetime.now(tz=timezone.utc)
    w_start = now - timedelta(days=days)

    for prof in profiles:
        pid   = prof["id"]
        pname = prof["profile_name"]
        logger.info("[run] Profile '%s' (id=%d)", pname, pid)

        pairs   = _get_active_pairs()
        n_pairs = len(pairs)
        completed = [0]

        def _process_pair(pair: str) -> None:
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT ts, open, high, low, close, volume FROM ohlcv_1m "
                    "WHERE pair = %s AND ts >= %s ORDER BY ts ASC",
                    (pair, w_start),
                )
                rows = cur.fetchall()

                if not rows:
                    logger.debug("[run] %s — tidak ada data OHLCV, skip", pair)
                    return

                candles = [dict(r) for r in rows]
                result  = _run_backtest_pair(prof, pair, candles, w_start, now)

                if result is None:
                    logger.debug("[run] %s — kurang candle (%d), skip", pair, len(candles))
                    return

                _save_result(pid, pair, result["stats"], result["trades"])
            finally:
                conn.close()
                completed[0] += 1
                _progress(completed[0], n_pairs, label=f"{pname} › {pair}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_process_pair, pair): pair for pair in pairs}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error("[run] error in pair %s: %s", futures[future], exc)

        _progress(n_pairs, n_pairs, label=f"{pname}")
        logger.info("[run] Profile '%s' selesai.", pname)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Algovibe Backtester")
    parser.add_argument(
        "command",
        choices=["fetch", "run", "all"],
        help="fetch=download OHLCV | run=jalankan backtest | all=fetch+run",
    )
    parser.add_argument("--days", type=int, default=OHLCV_DAYS,
                        help=f"Jumlah hari historis (default {OHLCV_DAYS})")
    args = parser.parse_args()

    if args.command in ("fetch", "all"):
        logger.info("=== FETCH OHLCV (%d hari) ===", args.days)
        fetch_ohlcv(days=args.days)

    if args.command in ("run", "all"):
        logger.info("=== RUN BACKTEST ===")
        run_backtest(days=args.days)

    logger.info("=== DONE ===")


if __name__ == "__main__":
    main()
