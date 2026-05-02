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
import sqlite3
import logging
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# Pastikan project root ada di path agar 'config' dan 'utils' bisa di-import
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Setup logging — hanya file handler, stdout dihandle oleh systemd
logging.basicConfig(level=logging.WARNING)  # Suppress root logger ke stdout
logger = logging.getLogger("auto_trainer")
logger.setLevel(logging.INFO)
logger.propagate = False  # Jangan propagate ke root agar tidak duplikat


def _setup_file_logger(log_path: str):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter("%(asctime)s [AUTO-TRAINER] %(message)s", "%Y-%m-%d %H:%M:%S"))
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
    conn = sqlite3.connect(db_path, timeout=30)
    query = """
        SELECT
            ai_prob, whale_boost, sector_sentiment, imbalance,
            volatility, rsi, ema_dist, entry_timestamp, pnl_idr
        FROM trade_analytics
        WHERE pnl_idr IS NOT NULL
          AND status = 'CLOSED'
        ORDER BY entry_timestamp DESC
        LIMIT 5000
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


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
    df["entry_timestamp"] = pd.to_datetime(df["entry_timestamp"])
    df["hour"] = df["entry_timestamp"].dt.hour
    df["target"] = (df["pnl_idr"] > 0).astype(int)

    features = ["ai_prob", "whale_boost", "sector_sentiment", "imbalance",
                "volatility", "rsi", "ema_dist", "hour"]
    X = df[features].fillna(0)
    y = df["target"]

    # 4. Split — gunakan test set yang sama untuk eval model lama & baru
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # 5. Eval model lama (pada test set yang sama)
    old_acc = _get_current_model_accuracy(model_path, X_test, y_test)
    result["old_accuracy"] = round(old_acc, 4)

    # 6. Train model baru
    logger.info(f"Training RF dengan {len(X_train)} samples...")
    new_model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
    new_model.fit(X_train, y_train)
    new_acc = accuracy_score(y_test, new_model.predict(X_test))
    result["new_accuracy"] = round(new_acc, 4)

    logger.info(f"Accuracy → Lama: {old_acc:.4f} | Baru: {new_acc:.4f} | Min floor: {min_accuracy}")

    # 7. Guard: accuracy floor
    if new_acc < min_accuracy:
        result["status"] = "rejected"
        result["reason"] = f"Accuracy baru {new_acc:.4f} di bawah floor {min_accuracy}. Model lama dipertahankan."
        logger.warning(result["reason"])
        return result

    # 8. Guard: harus lebih baik dari model lama + delta
    if new_acc < old_acc + min_delta:
        result["status"] = "rejected"
        result["reason"] = (
            f"Accuracy baru {new_acc:.4f} tidak cukup lebih baik dari lama {old_acc:.4f} "
            f"(butuh delta >= {min_delta}). Model lama dipertahankan."
        )
        logger.warning(result["reason"])
        return result

    # 9. Atomic swap — simpan ke .tmp lalu rename agar tidak corrupt mid-read
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(new_model, model_tmp)
    os.replace(model_tmp, model_path)  # atomic pada POSIX

    result["status"] = "deployed"
    result["deployed"] = True
    result["reason"] = (
        f"Model baru di-deploy: accuracy {new_acc:.4f} "
        f"(+{new_acc - old_acc:.4f} dari lama {old_acc:.4f})"
    )
    logger.info(f"✅ {result['reason']}")
    return result


def main():
    from config import (
        DB_PATH, AUTO_TRAIN_ENABLED, AUTO_TRAIN_INTERVAL_HOURS,
        AUTO_TRAIN_MIN_SAMPLES, AUTO_TRAIN_MIN_ACCURACY, AUTO_TRAIN_MIN_DELTA,
        AUTO_TRAIN_RF_MODEL_PATH, AUTO_TRAIN_RF_MODEL_TMP, AUTO_TRAIN_LOG_PATH
    )

    _setup_file_logger(AUTO_TRAIN_LOG_PATH)

    if not AUTO_TRAIN_ENABLED:
        logger.info("AUTO_TRAIN_ENABLED=False. Trainer tidak aktif.")
        return

    interval_sec = AUTO_TRAIN_INTERVAL_HOURS * 3600
    logger.info(f"Auto-Trainer dimulai. Interval: {AUTO_TRAIN_INTERVAL_HOURS} jam. Min samples: {AUTO_TRAIN_MIN_SAMPLES}.")

    while True:
        logger.info("=== Memulai siklus training ===")
        result = run_training_cycle(
            db_path=DB_PATH,
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
