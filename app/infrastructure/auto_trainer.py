"""
auto_trainer.py — Background Per-Pair Self-Training Loop

Berjalan sebagai background thread atau standalone process.
Setiap AUTO_TRAIN_INTERVAL_HOURS jam, trainer akan:
  1. Query semua pair dari ohlcv_1m dengan candle >= AUTO_TRAIN_MIN_CANDLES
  2. Build 20 OHLCV technical features per pair (RSI, MACD, BB, Stoch, dll.)
  3. Train RandomForestClassifier baru per pair
  4. Bandingkan accuracy dengan model lama — deploy hanya jika lebih baik
  5. Atomic write ke models/{pair}_1m_90d_1m_scalp_classifier.pkl
  6. predictor.py hot-reload otomatis via mtime check pada prediksi berikutnya
"""

import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, roc_auc_score

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("auto_trainer")
logger.setLevel(logging.INFO)
logger.propagate = False

_fmt = logging.Formatter("%(asctime)s [AUTO-TRAINER] %(message)s", "%Y-%m-%d %H:%M:%S")
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)

# ── Konstanta training ────────────────────────────────────────────────────────
MODEL_DIR     = Path(_PROJECT_ROOT) / "models"
DB_URL        = os.getenv(
    "DATABASE_URL_TRAIN",
    os.getenv(
        "DATABASE_URL_BACKTEST",
        "postgresql://algovibe:secretpassword@localhost:5432/algovibe",
    ),
)
FORWARD_BARS  = 5      # horizon prediksi: 5 candle ke depan
MIN_TARGET    = 0.003  # 0.3% profit threshold untuk label 1
N_ESTIMATORS  = 200
MAX_DEPTH     = 8
RANDOM_STATE  = 42

FEATURE_NAMES = [
    "RSI", "RSI_Change", "RSI_Slope", "MACD_Hist", "MACD_Signal_Cross",
    "BB_Pos", "BB_Width", "Stoch_K", "Stoch_D", "Stoch_Cross",
    "Williams_R", "EMA_9_21_Diff", "EMA_Above_50", "Vol_Ratio", "Vol_Change",
    "Price_Change_1m", "Price_Change_3m", "Price_Change_5m", "ATR_Pct", "OBV_Slope",
]


# ── Logging ───────────────────────────────────────────────────────────────────
def _setup_file_logger(log_path: str) -> None:
    from config import LOG_BACKUP_COUNT, LOG_MAX_BYTES, ENABLE_FILE_LOGGING
    if not ENABLE_FILE_LOGGING:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    fh = RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    fh.setFormatter(_fmt)
    logger.addHandler(fh)


# ── Database ──────────────────────────────────────────────────────────────────
def _get_pairs(conn, min_candles: int) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pair FROM ohlcv_1m GROUP BY pair HAVING COUNT(*) >= %s ORDER BY COUNT(*) DESC",
            (min_candles,),
        )
        return [r[0] for r in cur.fetchall()]


def _load_pair_data(conn, pair: str, days: int) -> pd.DataFrame:
    sql = """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv_1m
        WHERE pair = %s AND ts >= NOW() - INTERVAL '%s days'
        ORDER BY ts ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (pair, days))
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("ts").reset_index(drop=True).dropna(subset=["close"])


# ── Feature engineering ───────────────────────────────────────────────────────
def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Hitung 20 fitur teknikal secara vektorisasi dari DataFrame OHLCV."""
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    vol   = df["volume"].astype(float)

    feat = pd.DataFrame(index=df.index)

    # RSI (EWM Wilder)
    delta = close.diff()
    avg_g = delta.clip(lower=0).ewm(com=13, min_periods=14, adjust=False).mean()
    avg_l = (-delta).clip(lower=0).ewm(com=13, min_periods=14, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    rsi   = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    feat["RSI"]        = rsi
    feat["RSI_Change"] = rsi - rsi.shift(3)
    feat["RSI_Slope"]  = feat["RSI_Change"] / 3.0

    # MACD (12/26/9)
    ema12      = close.ewm(span=12, min_periods=12, adjust=False).mean()
    ema26      = close.ewm(span=26, min_periods=26, adjust=False).mean()
    macd_line  = ema12 - ema26
    sig_line   = macd_line.ewm(span=9, min_periods=9, adjust=False).mean()
    macd_hist  = (macd_line - sig_line).fillna(0.0)
    hist_prev  = macd_hist.shift(1)
    feat["MACD_Hist"] = macd_hist
    feat["MACD_Signal_Cross"] = np.where(
        (macd_hist > 0) & (hist_prev <= 0),  1.0,
        np.where((macd_hist < 0) & (hist_prev >= 0), -1.0, 0.0)
    )

    # Bollinger Bands (20)
    bb_mid   = close.rolling(20, min_periods=20).mean()
    bb_std   = close.rolling(20, min_periods=20).std(ddof=0)
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    bb_range = (bb_upper - bb_lower).replace(0, np.nan)
    feat["BB_Pos"]   = ((close - bb_lower) / bb_range).fillna(0.5)
    feat["BB_Width"] = (bb_range / bb_mid.replace(0, np.nan)).fillna(0.0)

    # Stochastic RSI (RSI-based, period=14)
    rsi_min  = rsi.rolling(14, min_periods=14).min()
    rsi_max  = rsi.rolling(14, min_periods=14).max()
    rsi_rng  = (rsi_max - rsi_min).replace(0, np.nan)
    stoch_k  = ((rsi - rsi_min) / rsi_rng * 100.0).fillna(50.0)
    stoch_d  = stoch_k.rolling(3, min_periods=3).mean().fillna(50.0)
    feat["Stoch_K"]    = stoch_k
    feat["Stoch_D"]    = stoch_d
    feat["Stoch_Cross"] = (stoch_k > stoch_d).astype(float)

    # Williams %R (14)
    hh = high.rolling(14, min_periods=14).max()
    ll = low.rolling(14, min_periods=14).min()
    wr_rng = (hh - ll).replace(0, np.nan)
    feat["Williams_R"] = ((hh - close) / wr_rng * -100.0).fillna(-50.0)

    # EMA 9/21 diff, EMA-50 filter
    e9  = close.ewm(span=9,  min_periods=9,  adjust=False).mean()
    e21 = close.ewm(span=21, min_periods=21, adjust=False).mean()
    e50 = close.ewm(span=50, min_periods=50, adjust=False).mean()
    feat["EMA_9_21_Diff"] = ((e9 - e21) / e21.replace(0, np.nan) * 100.0).fillna(0.0)
    feat["EMA_Above_50"]  = (close > e50).astype(float)

    # Volume features
    vol_ma = vol.rolling(20, min_periods=2).mean().replace(0, np.nan)
    feat["Vol_Ratio"]  = (vol / vol_ma).fillna(1.0)
    vol_prev5 = vol.shift(5).replace(0, np.nan)
    feat["Vol_Change"] = ((vol - vol_prev5) / vol_prev5 * 100.0).fillna(0.0)

    # Price changes
    feat["Price_Change_1m"] = (close.pct_change(1) * 100.0).fillna(0.0)
    feat["Price_Change_3m"] = (close.pct_change(3) * 100.0).fillna(0.0)
    feat["Price_Change_5m"] = (close.pct_change(5) * 100.0).fillna(0.0)

    # ATR % (14)
    tr  = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, min_periods=14, adjust=False).mean()
    feat["ATR_Pct"] = (atr / close.replace(0, np.nan) * 100.0).fillna(0.0)

    # OBV slope
    direction = np.sign(close.diff().fillna(0))
    obv       = (vol * direction).cumsum()
    obv_prev5 = obv.shift(5).replace(0, np.nan)
    feat["OBV_Slope"] = ((obv - obv_prev5) / obv_prev5.abs().replace(0, np.nan) * 100.0).fillna(0.0)

    return feat[FEATURE_NAMES]


def _build_labels(closes: np.ndarray, forward: int, threshold: float) -> np.ndarray:
    """Label 1 jika close[t+forward]/close[t]-1 >= threshold, else 0. Last `forward` rows = NaN."""
    n      = len(closes)
    labels = np.full(n, np.nan)
    for i in range(n - forward):
        ret = (closes[i + forward] / closes[i]) - 1.0
        labels[i] = 1 if ret >= threshold else 0
    return labels


# ── Evaluasi model lama ───────────────────────────────────────────────────────
def _score_model(model, X_test, y_test) -> float:
    """Gunakan ROC-AUC sebagai metric utama — tidak terpengaruh class imbalance.
    Fallback ke balanced_accuracy jika hanya ada 1 class di y_test."""
    try:
        proba = model.predict_proba(X_test)[:, 1]
        if len(set(y_test)) < 2:
            from sklearn.metrics import balanced_accuracy_score
            return float(balanced_accuracy_score(y_test, model.predict(X_test)))
        return float(roc_auc_score(y_test, proba))
    except Exception as e:
        logger.warning(f"Scoring error: {e}")
        return 0.0


def _existing_accuracy(model_path: Path, X_test, y_test) -> float:
    if not model_path.exists():
        return 0.0
    try:
        return _score_model(joblib.load(model_path), X_test, y_test)
    except Exception as e:
        logger.warning(f"Tidak bisa evaluate model lama {model_path.name}: {e}")
        return 0.0


# ── Training satu pair ────────────────────────────────────────────────────────
def _train_pair(pair: str, df: pd.DataFrame, min_candles: int,
                min_accuracy: float, min_delta: float) -> dict:
    result = {"pair": pair, "status": "skipped", "new_acc": None, "old_acc": None,
              "deployed": False, "reason": ""}

    if len(df) < min_candles:
        result["reason"] = f"candles={len(df)} < {min_candles}"
        return result

    feat_df        = _build_features(df)
    feat_df["label"] = _build_labels(df["close"].to_numpy(dtype=float), FORWARD_BARS, MIN_TARGET)
    feat_df        = feat_df.dropna()
    X = feat_df[FEATURE_NAMES].values
    y = feat_df["label"].values.astype(int)

    if len(X) < 100:
        result["reason"] = f"clean samples={len(X)} < 100"
        return result

    n_pos, n_neg = int(y.sum()), int((y == 0).sum())
    if n_pos < 10 or n_neg < 10:
        result["reason"] = f"class imbalance ekstrem pos={n_pos} neg={n_neg}"
        return result

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False  # time-series: no shuffle
    )

    model_path = MODEL_DIR / f"{pair}_1m_90d_1m_scalp_classifier.pkl"
    old_acc    = _existing_accuracy(model_path, X_test, y_test)
    result["old_acc"] = round(old_acc, 4)

    new_model = RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_depth=MAX_DEPTH,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    new_model.fit(X_train, y_train)
    new_acc = _score_model(new_model, X_test, y_test)  # ROC-AUC
    result["new_acc"] = round(new_acc, 4)

    if new_acc < min_accuracy:
        result["status"] = "rejected"
        result["reason"]  = f"auc={new_acc:.4f} di bawah floor {min_accuracy}"
        return result

    if new_acc < old_acc + min_delta:
        result["status"] = "rejected"
        result["reason"]  = f"auc={new_acc:.4f} tidak cukup lebih baik dari {old_acc:.4f} (delta<{min_delta})"
        return result

    # Atomic write — tmp → replace agar tidak corrupt mid-read
    MODEL_DIR.mkdir(exist_ok=True)
    tmp_path = model_path.with_suffix(".tmp")
    joblib.dump(new_model, tmp_path)
    tmp_path.replace(model_path)

    result["status"]   = "deployed"
    result["deployed"] = True
    result["reason"]   = f"auc={new_acc:.4f} (+{new_acc - old_acc:.4f} dari {old_acc:.4f})"
    return result


# ── Siklus training semua pair ────────────────────────────────────────────────
def run_training_cycle(days: int, min_candles: int, min_accuracy: float, min_delta: float) -> dict:
    summary = {
        "timestamp": datetime.now().isoformat(),
        "pairs": 0, "deployed": 0, "rejected": 0, "skipped": 0, "errors": 0,
    }
    try:
        conn = psycopg2.connect(DB_URL)
    except Exception as e:
        logger.error(f"[ERROR] DB connect: {e}")
        return summary

    pairs          = _get_pairs(conn, min_candles)
    summary["pairs"] = len(pairs)
    logger.info(f"{len(pairs)} pairs memenuhi syarat (min_candles={min_candles}, days={days})")

    for pair in pairs:
        try:
            df = _load_pair_data(conn, pair, days)
            if df.empty:
                summary["skipped"] += 1
                continue
            r = _train_pair(pair, df, min_candles, min_accuracy, min_delta)
            if r["deployed"]:
                logger.info(f"[OK] {pair}: {r['reason']}")
                summary["deployed"] += 1
            elif r["status"] == "rejected":
                logger.debug(f"[SKIP] {pair}: {r['reason']}")
                summary["rejected"] += 1
            else:
                summary["skipped"] += 1
        except Exception as e:
            logger.error(f"[ERROR] {pair}: {e}")
            summary["errors"] += 1

    conn.close()
    return summary


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    from config import (
        AUTO_TRAIN_ENABLED, AUTO_TRAIN_INTERVAL_HOURS, AUTO_TRAIN_DAYS,
        AUTO_TRAIN_MIN_CANDLES, AUTO_TRAIN_MIN_ACCURACY,
        AUTO_TRAIN_MIN_DELTA, AUTO_TRAIN_LOG_PATH,
    )

    _setup_file_logger(AUTO_TRAIN_LOG_PATH)

    if not AUTO_TRAIN_ENABLED:
        logger.info("AUTO_TRAIN_ENABLED=False. Trainer tidak aktif.")
        return

    interval_sec = AUTO_TRAIN_INTERVAL_HOURS * 3600
    logger.info(
        f"Auto-Trainer dimulai. interval={AUTO_TRAIN_INTERVAL_HOURS}j "
        f"days={AUTO_TRAIN_DAYS} min_candles={AUTO_TRAIN_MIN_CANDLES}"
    )

    while True:
        logger.info("=== Memulai siklus training per-pair ===")
        t0      = time.time()
        summary = run_training_cycle(
            days         = AUTO_TRAIN_DAYS,
            min_candles  = AUTO_TRAIN_MIN_CANDLES,
            min_accuracy = AUTO_TRAIN_MIN_ACCURACY,
            min_delta    = AUTO_TRAIN_MIN_DELTA,
        )
        elapsed = time.time() - t0
        logger.info(
            f"Siklus selesai ({elapsed:.0f}s) | pairs={summary['pairs']} "
            f"deployed={summary['deployed']} rejected={summary['rejected']} "
            f"skipped={summary['skipped']} errors={summary['errors']}"
        )
        logger.info(f"Tidur {AUTO_TRAIN_INTERVAL_HOURS} jam hingga siklus berikutnya...")
        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
