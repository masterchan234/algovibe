"""
scripts/train_scalp_classifiers.py

Training script untuk per-pair RandomForestClassifier scalp model.
Membaca data 1m OHLCV dari tabel ohlcv_1m, menghitung 20 features teknikal,
lalu melatih dan menyimpan model ke models/{pair}_1m_90d_1m_scalp_classifier.pkl

Jalankan:
    python3 scripts/train_scalp_classifiers.py [--pair btcidr] [--min-candles 500]

Env yang digunakan (diambil dari .env atau env variable):
    DATABASE_URL_TRAIN  → override koneksi DB (default: algovibe@localhost:5432/algovibe)
"""

import argparse
import os
import sys
import warnings
import time
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
import psycopg2
import psycopg2.extras

# ── Repo root ──────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from app.domain.technicals import (
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_stochastic_rsi,
    calculate_williams_r,
    calculate_atr,
    calculate_obv,
    calculate_volume_ratio,
)

# ── Config ─────────────────────────────────────────────────────────────────
DB_URL = os.getenv(
    "DATABASE_URL_TRAIN",
    "postgresql://algovibe:secretpassword@localhost:5432/algovibe",
)
MODEL_DIR = _REPO / "models"
MIN_CANDLES   = 500       # minimum candles per pair untuk training
FORWARD_BARS  = 5         # horizon prediksi: 5 menit ke depan
MIN_TARGET    = 0.003     # 0.3% minimum profit target untuk label 1
N_ESTIMATORS  = 200
MAX_DEPTH     = 8
RANDOM_STATE  = 42

FEATURE_NAMES = [
    "RSI", "RSI_Change", "RSI_Slope", "MACD_Hist", "MACD_Signal_Cross",
    "BB_Pos", "BB_Width", "Stoch_K", "Stoch_D", "Stoch_Cross",
    "Williams_R", "EMA_9_21_Diff", "EMA_Above_50", "Vol_Ratio", "Vol_Change",
    "Price_Change_1m", "Price_Change_3m", "Price_Change_5m", "ATR_Pct", "OBV_Slope",
]


# ── DB ─────────────────────────────────────────────────────────────────────
def get_pairs(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pair, COUNT(*) as n FROM ohlcv_1m GROUP BY pair "
            "HAVING COUNT(*) >= %s ORDER BY n DESC",
            (MIN_CANDLES,),
        )
        return [r[0] for r in cur.fetchall()]


def load_pair_data(conn, pair: str, days: int = 90) -> pd.DataFrame:
    sql = """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv_1m
        WHERE pair = %s
          AND ts >= NOW() - INTERVAL '%s days'
        ORDER BY ts ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (pair, days))
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


# ── Feature engineering (rolling window, vectorised) ──────────────────────
def _rolling_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute RSI for every row using Wilder smoothing."""
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = float(np.mean(gains[:period]))
    avg_l = float(np.mean(losses[:period]))
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            rsi[i + 1] = 100.0
        elif avg_g == 0:
            rsi[i + 1] = 0.0
        else:
            rs = avg_g / avg_l
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all 20 features vectorized over the full DataFrame (O(n)).
    Rows with NaN features (insufficient history) are dropped by the caller.
    """
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    vol   = df["volume"].astype(float)

    feat = pd.DataFrame(index=df.index)

    # ── 1. RSI (period=14) ───────────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=13, min_periods=14, adjust=False).mean()
    avg_l = loss.ewm(com=13, min_periods=14, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    rsi   = 100.0 - (100.0 / (1.0 + rs))
    feat["RSI"] = rsi.fillna(50.0)

    # ── 2-3. RSI_Change, RSI_Slope ───────────────────────────────────────
    feat["RSI_Change"] = feat["RSI"] - feat["RSI"].shift(3)
    feat["RSI_Slope"]  = feat["RSI_Change"] / 3.0

    # ── 4. MACD_Hist (12/26/9) ───────────────────────────────────────────
    ema12 = close.ewm(span=12, min_periods=12, adjust=False).mean()
    ema26 = close.ewm(span=26, min_periods=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    signal_line = macd_line.ewm(span=9, min_periods=9, adjust=False).mean()
    macd_hist   = macd_line - signal_line
    feat["MACD_Hist"] = macd_hist.fillna(0.0)

    # ── 5. MACD_Signal_Cross ─────────────────────────────────────────────
    hist_prev = feat["MACD_Hist"].shift(1)
    cross = np.where((feat["MACD_Hist"] > 0) & (hist_prev <= 0),  1.0,
            np.where((feat["MACD_Hist"] < 0) & (hist_prev >= 0), -1.0, 0.0))
    feat["MACD_Signal_Cross"] = cross

    # ── 6-7. BB_Pos, BB_Width (period=20) ────────────────────────────────
    bb_mid   = close.rolling(20, min_periods=20).mean()
    bb_std   = close.rolling(20, min_periods=20).std(ddof=0)
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    bb_range = (bb_upper - bb_lower).replace(0, np.nan)
    feat["BB_Pos"]   = ((close - bb_lower) / bb_range).fillna(0.5)
    feat["BB_Width"] = (bb_range / bb_mid.replace(0, np.nan)).fillna(0.0)

    # ── 8-9. Stoch_K, Stoch_D (RSI-based stochastic, period=14) ─────────
    rsi_min = feat["RSI"].rolling(14, min_periods=14).min()
    rsi_max = feat["RSI"].rolling(14, min_periods=14).max()
    rsi_rng = (rsi_max - rsi_min).replace(0, np.nan)
    stoch_k = ((feat["RSI"] - rsi_min) / rsi_rng * 100.0).fillna(50.0)
    stoch_d = stoch_k.rolling(3, min_periods=3).mean().fillna(50.0)
    feat["Stoch_K"] = stoch_k
    feat["Stoch_D"] = stoch_d

    # ── 10. Stoch_Cross ──────────────────────────────────────────────────
    feat["Stoch_Cross"] = (stoch_k > stoch_d).astype(float)

    # ── 11. Williams_R (period=14) ───────────────────────────────────────
    hh = high.rolling(14, min_periods=14).max()
    ll = low.rolling(14, min_periods=14).min()
    wr_rng = (hh - ll).replace(0, np.nan)
    feat["Williams_R"] = ((hh - close) / wr_rng * -100.0).fillna(-50.0)

    # ── 12. EMA_9_21_Diff ────────────────────────────────────────────────
    e9  = close.ewm(span=9,  min_periods=9,  adjust=False).mean()
    e21 = close.ewm(span=21, min_periods=21, adjust=False).mean()
    feat["EMA_9_21_Diff"] = ((e9 - e21) / e21.replace(0, np.nan) * 100.0).fillna(0.0)

    # ── 13. EMA_Above_50 ─────────────────────────────────────────────────
    e50 = close.ewm(span=50, min_periods=50, adjust=False).mean()
    feat["EMA_Above_50"] = (close > e50).astype(float)

    # ── 14. Vol_Ratio ────────────────────────────────────────────────────
    vol_ma = vol.rolling(20, min_periods=2).mean().replace(0, np.nan)
    feat["Vol_Ratio"] = (vol / vol_ma).fillna(1.0)

    # ── 15. Vol_Change (% vs 5 candles ago) ─────────────────────────────
    vol_prev5 = vol.shift(5).replace(0, np.nan)
    feat["Vol_Change"] = ((vol - vol_prev5) / vol_prev5 * 100.0).fillna(0.0)

    # ── 16-18. Price changes (%) ─────────────────────────────────────────
    feat["Price_Change_1m"] = (close.pct_change(1) * 100.0).fillna(0.0)
    feat["Price_Change_3m"] = (close.pct_change(3) * 100.0).fillna(0.0)
    feat["Price_Change_5m"] = (close.pct_change(5) * 100.0).fillna(0.0)

    # ── 19. ATR_Pct (period=14) ──────────────────────────────────────────
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14, min_periods=14, adjust=False).mean()
    feat["ATR_Pct"] = (atr / close.replace(0, np.nan) * 100.0).fillna(0.0)

    # ── 20. OBV_Slope ────────────────────────────────────────────────────
    direction = np.sign(close.diff().fillna(0))
    obv = (vol * direction).cumsum()
    obv_prev5 = obv.shift(5).replace(0, np.nan)
    feat["OBV_Slope"] = ((obv - obv_prev5) / (obv_prev5.abs().replace(0, np.nan)) * 100.0).fillna(0.0)

    feat.insert(0, "close", close.values)
    return feat[["close"] + FEATURE_NAMES]


def build_labels(closes: np.ndarray, forward: int = FORWARD_BARS, threshold: float = MIN_TARGET) -> np.ndarray:
    """
    Label 1 jika close[t+forward] / close[t] - 1 >= threshold, else 0.
    Last `forward` rows mendapat NaN (tidak bisa dilabeli).
    """
    n = len(closes)
    labels = np.full(n, np.nan)
    for i in range(n - forward):
        ret = (closes[i + forward] / closes[i]) - 1.0
        labels[i] = 1 if ret >= threshold else 0
    return labels


# ── Train one pair ─────────────────────────────────────────────────────────
def train_pair(pair: str, df: pd.DataFrame, out_dir: Path) -> bool:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report

    if len(df) < MIN_CANDLES:
        print(f"  [SKIP] {pair}: hanya {len(df)} candles (min {MIN_CANDLES})")
        return False

    # Build features
    feat_df = build_features(df)
    labels  = build_labels(df["close"].to_numpy(dtype=float))

    feat_df["label"] = labels
    feat_df = feat_df.dropna()

    X = feat_df[FEATURE_NAMES].values
    y = feat_df["label"].values.astype(int)

    if len(X) < 100:
        print(f"  [SKIP] {pair}: sampel bersih hanya {len(X)}")
        return False

    n_pos = int(y.sum())
    n_neg = int((y == 0).sum())
    if n_pos < 10 or n_neg < 10:
        print(f"  [SKIP] {pair}: class imbalance ekstrem (pos={n_pos}, neg={n_neg})")
        return False

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False  # time-series: no shuffle
    )

    model = RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_depth=MAX_DEPTH,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    # Eval
    y_pred = model.predict(X_test)
    acc = float((y_pred == y_test).mean())

    out_path = out_dir / f"{pair}_1m_90d_1m_scalp_classifier.pkl"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        joblib.dump(model, out_path)

    n_pos_test = int(y_test.sum())
    print(f"  [OK] {pair}: {len(X)} sampel | acc={acc:.3f} | pos_rate={n_pos/len(y):.2%} | saved → {out_path.name}")
    return True


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    global MIN_CANDLES, FORWARD_BARS, MIN_TARGET

    parser = argparse.ArgumentParser(description="Train per-pair scalp classifiers")
    parser.add_argument("--pair",        type=str, default=None, help="Latih hanya 1 pair (misal: btcidr)")
    parser.add_argument("--min-candles", type=int, default=MIN_CANDLES, help=f"Min candles per pair (default: {MIN_CANDLES})")
    parser.add_argument("--days",        type=int, default=90,  help="Ambil N hari terakhir dari DB (default: 90)")
    parser.add_argument("--forward",     type=int, default=FORWARD_BARS, help=f"Horizon label dalam candle (default: {FORWARD_BARS})")
    parser.add_argument("--target",      type=float, default=MIN_TARGET, help=f"Return threshold untuk label 1 (default: {MIN_TARGET})")
    args = parser.parse_args()

    MIN_CANDLES  = args.min_candles
    FORWARD_BARS = args.forward
    MIN_TARGET   = args.target

    MODEL_DIR.mkdir(exist_ok=True)

    print(f"[INFO] Connecting to DB: {DB_URL.split('@')[-1]}")
    conn = psycopg2.connect(DB_URL)

    if args.pair:
        pairs = [args.pair.lower()]
    else:
        pairs = get_pairs(conn)
        print(f"[INFO] {len(pairs)} pairs dengan >= {MIN_CANDLES} candles ditemukan")

    ok = fail = skip = 0
    t0 = time.time()

    for i, pair in enumerate(pairs, 1):
        print(f"[{i}/{len(pairs)}] Training {pair} ...", end=" ", flush=True)
        try:
            df = load_pair_data(conn, pair, days=args.days)
            if df.empty:
                print(f"  [SKIP] no data")
                skip += 1
                continue
            success = train_pair(pair, df, MODEL_DIR)
            if success:
                ok += 1
            else:
                skip += 1
        except Exception as e:
            print(f"  [ERROR] {pair}: {e}")
            fail += 1

    conn.close()
    elapsed = time.time() - t0
    print(f"\n[DONE] {ok} trained | {skip} skipped | {fail} errors | {elapsed:.1f}s")


if __name__ == "__main__":
    main()
