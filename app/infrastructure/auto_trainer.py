"""
auto_trainer.py — Background Self-Training Loop for Scalper V2 RF Model

Berjalan sebagai standalone process (indodax-trainer.service).
Setiap AUTO_TRAIN_INTERVAL_HOURS jam, trainer akan:
  1. Query trade_analytics dari algotrade.db (fitur lengkap + pnl)
  2. Validasi jumlah sample (>= AUTO_TRAIN_MIN_SAMPLES)
  3. Train RandomForestClassifier baru
  4. Bandingkan accuracy dengan model lama
  5. Atomic swap ke scalper_v2.joblib HANYA jika lebih baik
  6. predictor.py hot-reload otomatis di predict() berikutnya via mtime check
"""

import os
import sys
import time
import psycopg2
import logging
from logging.handlers import RotatingFileHandler
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.metrics import accuracy_score

# Pastikan project root ada di path agar 'config' dan 'utils' bisa di-import
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("auto_trainer")
logger.setLevel(logging.INFO)
logger.propagate = False

_fmt = logging.Formatter("%(asctime)s [AUTO-TRAINER] %(message)s", "%Y-%m-%d %H:%M:%S")

# Always log to stdout so Docker captures trainer output via `docker logs`
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)


def _setup_file_logger(log_path: str):
    from config import LOG_BACKUP_COUNT, LOG_MAX_BYTES, ENABLE_FILE_LOGGING
    
    if not ENABLE_FILE_LOGGING:
        return

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    fh = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(_fmt)
    logger.addHandler(fh)


def _get_current_model_accuracy(model_path: str, X_test, y_test) -> float:
    """Evaluasi model lama menggunakan test set yang sama agar perbandingan fair."""
    if not os.path.exists(model_path):
        return 0.0
    try:
        old_model = joblib.load(model_path)
        return accuracy_score(y_test, old_model.predict(X_test))
    except Exception as e:
        logger.warning(f"Tidak bisa evaluate model lama: {e}")
        return 0.0


def _load_training_data(db_path: str) -> pd.DataFrame:
    """
    Ambil data training dari trade_analytics.
    Menggunakan sliding window 5000 trade terbaru (data paling relevan).
    """
    conn = psycopg2.connect(DATABASE_URL_LIVE)
    query = """
        SELECT
            ai_prob, whale_boost, sector_sentiment, imbalance,
            volatility, rsi, ema_dist, entry_timestamp, pnl_quote
        FROM trade_analytics
        WHERE pnl_quote IS NOT NULL
          AND status = 'CLOSED'
        ORDER BY entry_timestamp DESC
        LIMIT 5000
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def _engineer_features(df: pd.DataFrame) -> tuple:
    """
    Membangun feature matrix dari data trade_analytics.
    Mengembalikan (X, y, feature_names).
    """
    df = df.copy()
    df["entry_timestamp"] = pd.to_datetime(df["entry_timestamp"])
    df["hour"] = df["entry_timestamp"].dt.hour
    df["target"] = (df["pnl_quote"] > 0).astype(int)

    # Cyclical time encoding — jam merepresentasikan siklus 24 jam, bukan angka linier
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)

    # Seberapa oversold (RSI < 30) atau overbought (RSI > 70) posisi entry
    df["rsi_oversold"]   = np.maximum(0.0, (30.0 - df["rsi"]) / 30.0)
    df["rsi_overbought"] = np.maximum(0.0, (df["rsi"] - 70.0) / 30.0)

    # Magnitude jarak harga dari EMA — semakin jauh, semakin mungkin mean-reversion
    df["ema_dist_abs"] = df["ema_dist"].abs()

    features = [
        "ai_prob",        # probabilitas teknikal saat entry
        "imbalance",      # rasio bid/ask depth
        "volatility",     # volatilitas harga historis
        "rsi",            # RSI saat entry
        "ema_dist",       # jarak harga ke EMA (arah)
        "ema_dist_abs",   # magnitude jarak ke EMA
        "rsi_oversold",   # seberapa oversold saat entry
        "rsi_overbought", # seberapa overbought saat entry
        "hour_sin",       # waktu entry — komponen sinus
        "hour_cos",       # waktu entry — komponen cosinus
    ]

    X = df[features].fillna(0)
    y = df["target"]
    return X, y, features


def run_training_cycle(db_path: str, model_path: str, model_tmp: str, min_samples: int,
                       min_accuracy: float, min_delta: float) -> dict:
    """
    Jalankan satu siklus training. Return dict berisi hasil dan status.
    """
    result = {
        "timestamp": datetime.now().isoformat(),
        "status": "skipped",
        "samples": 0,
        "new_accuracy": None,
        "old_accuracy": None,
        "deployed": False,
        "reason": "",
    }

    # 1. Load data
    try:
        df = _load_training_data(db_path)
    except Exception as e:
        result["status"] = "error"
        result["reason"] = f"DB error: {e}"
        logger.error(result["reason"])
        return result

    result["samples"] = len(df)

    # 2. Guard: minimum samples
    if len(df) < min_samples:
        result["reason"] = f"Sampel tidak cukup: {len(df)} < {min_samples}. Skip."
        logger.info(result["reason"])
        return result

    # 3. Feature engineering
    X, y, feature_names = _engineer_features(df)

    # 4. Split stratified — pertahankan proporsi kelas di train/test
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # 5. Eval model lama (pada test set yang sama)
    old_acc = _get_current_model_accuracy(model_path, X_test, y_test)
    result["old_accuracy"] = round(old_acc, 4)

    # 6. Train model baru — HistGradientBoosting: lebih akurat, tangani imbalance,
    #    dan lebih tahan overfitting dibanding RandomForest pada dataset kecil-menengah
    logger.info(f"Training HistGradientBoosting dengan {len(X_train)} samples...")
    new_model = HistGradientBoostingClassifier(
        max_iter=300,
        max_depth=4,
        learning_rate=0.05,
        min_samples_leaf=20,
        l2_regularization=0.1,
        class_weight="balanced",
        random_state=42,
    )
    new_model.fit(X_train, y_train)

    # 7. Cross-validation score sebagai ukuran generalisasi
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(new_model, X, y, cv=cv, scoring="accuracy", n_jobs=-1)
    cv_mean = float(cv_scores.mean())
    cv_std  = float(cv_scores.std())
    logger.info(f"CV accuracy: {cv_mean:.4f} ± {cv_std:.4f}")

    # Gunakan hold-out test set sebagai angka resmi untuk deployment guard
    new_acc = accuracy_score(y_test, new_model.predict(X_test))
    result["new_accuracy"] = round(new_acc, 4)

    logger.info(f"Accuracy → Lama: {old_acc:.4f} | Baru: {new_acc:.4f} | Min floor: {min_accuracy}")

    # 8. Guard: accuracy floor
    if new_acc < min_accuracy:
        result["status"] = "rejected"
        result["reason"] = f"Accuracy baru {new_acc:.4f} di bawah floor {min_accuracy}. Model lama dipertahankan."
        logger.warning(result["reason"])
        return result

    # 9. Guard: harus lebih baik dari model lama + delta
    if new_acc < old_acc + min_delta:
        result["status"] = "rejected"
        result["reason"] = (
            f"Accuracy baru {new_acc:.4f} tidak cukup lebih baik dari lama {old_acc:.4f} "
            f"(butuh delta >= {min_delta}). Model lama dipertahankan."
        )
        logger.warning(result["reason"])
        return result

    # 10. Atomic swap — simpan ke .tmp lalu rename agar tidak corrupt mid-read
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(new_model, model_tmp)
    os.replace(model_tmp, model_path)  # atomic pada POSIX

    result["status"] = "deployed"
    result["deployed"] = True
    result["reason"] = (
        f"Model baru di-deploy: accuracy {new_acc:.4f} "
        f"(CV {cv_mean:.4f}±{cv_std:.4f}, +{new_acc - old_acc:.4f} dari lama {old_acc:.4f})"
    )
    logger.info(f"[OK] {result['reason']}")
    return result


def main():
    from config import (
        AUTO_TRAIN_ENABLED, AUTO_TRAIN_INTERVAL_HOURS,
        AUTO_TRAIN_MIN_SAMPLES, AUTO_TRAIN_MIN_ACCURACY, AUTO_TRAIN_MIN_DELTA,
        AUTO_TRAIN_RF_MODEL_PATH, AUTO_TRAIN_RF_MODEL_TMP, AUTO_TRAIN_LOG_PATH
    )
    # Gunakan path DB aktif dari router agar trainer baca data dari DB yang benar
    from app.infrastructure.database import db as _db_router

    _setup_file_logger(AUTO_TRAIN_LOG_PATH)

    if not AUTO_TRAIN_ENABLED:
        logger.info("AUTO_TRAIN_ENABLED=False. Trainer tidak aktif.")
        return

    interval_sec = AUTO_TRAIN_INTERVAL_HOURS * 3600
    logger.info(f"Auto-Trainer dimulai. Interval: {AUTO_TRAIN_INTERVAL_HOURS} jam. Min samples: {AUTO_TRAIN_MIN_SAMPLES}.")

    while True:
        logger.info("=== Memulai siklus training ===")
        result = run_training_cycle(
            db_path=None,
            model_path=AUTO_TRAIN_RF_MODEL_PATH,
            model_tmp=AUTO_TRAIN_RF_MODEL_TMP,
            min_samples=AUTO_TRAIN_MIN_SAMPLES,
            min_accuracy=AUTO_TRAIN_MIN_ACCURACY,
            min_delta=AUTO_TRAIN_MIN_DELTA,
        )
        logger.info(
            f"Siklus selesai | status={result['status']} | samples={result['samples']} | "
            f"new_acc={result['new_accuracy']} | deployed={result['deployed']} | {result['reason']}"
        )
        logger.info(f"Tidur {AUTO_TRAIN_INTERVAL_HOURS} jam hingga siklus berikutnya...")
        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
