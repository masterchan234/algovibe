# ============================================================
# CELL 1: Install dependencies
# ============================================================
# !pip install -q ta scikit-learn joblib pandas numpy

# ============================================================
# CELL 2: Mount Google Drive
# ============================================================
# from google.colab import drive
# drive.mount('/content/drive')

# ============================================================
# CELL 3: Imports & Config
# ============================================================
import os, sys, subprocess, warnings, time
import pandas as pd
import glob
import joblib
import numpy as np
from collections import defaultdict
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score

try:
    import ta
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ta"])
    import ta

warnings.filterwarnings('ignore')

# --- CONFIG ---
FOLDER_DATA    = '/content/drive/MyDrive/Algovibe_Data/ohlcv'   # CSV 1m 90 hari
FOLDER_MODEL   = '/content/drive/MyDrive/Algovibe_Models'
LOOKAHEAD      = 5        # prediksi t+5 candle (5 menit ke depan)
TARGET_PCT     = 0.003    # 0.3% default — akan di-override per koin secara adaptif
TARGET_PCT_MIN = 0.001    # floor 0.1% (minimal cover sebagian fee)
TARGET_PCT_MAX = 0.008    # cap 0.8% (koin terlalu volatile, noise bukan signal)
N_ESTIMATORS   = 200
MIN_SAMPLES    = 500
SUFFIX         = "_1m_scalp_classifier.pkl"

os.makedirs(FOLDER_MODEL, exist_ok=True)

semua_file = glob.glob(os.path.join(FOLDER_DATA, "*.csv"))
print(f"[OK] Ditemukan {len(semua_file)} file koin.")
print(f"[OK] Config: lookahead={LOOKAHEAD} candles, target={TARGET_PCT*100:.1f}%, suffix={SUFFIX}")


# ============================================================
# CELL 4: Feature engineering
# ============================================================
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """100% indicator-based — ZERO price leakage."""
    c = df['close']
    h = df['high']
    l = df['low']
    v = df['volume']

    feat = pd.DataFrame(index=df.index)

    # RSI
    rsi = ta.momentum.RSIIndicator(c, window=14).rsi()
    feat['RSI']        = rsi
    feat['RSI_Change'] = rsi.diff(3)
    feat['RSI_Slope']  = rsi.diff(1)

    # MACD
    macd_obj = ta.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    feat['MACD_Hist']         = macd_obj.macd_diff()
    feat['MACD_Signal_Cross'] = (macd_obj.macd_diff() > 0).astype(int)

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(c, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_width = bb_upper - bb_lower
    feat['BB_Pos']   = (c - bb_lower) / bb_width.replace(0, np.nan)
    feat['BB_Width'] = bb_width / c

    # Stochastic RSI
    stoch = ta.momentum.StochRSIIndicator(c, window=14, smooth1=3, smooth2=3)
    feat['Stoch_K']     = stoch.stochrsi_k() * 100
    feat['Stoch_D']     = stoch.stochrsi_d() * 100
    feat['Stoch_Cross'] = feat['Stoch_K'] - feat['Stoch_D']

    # Williams %R
    feat['Williams_R'] = ta.momentum.WilliamsRIndicator(h, l, c, lbp=14).williams_r()

    # EMA
    ema9  = ta.trend.EMAIndicator(c, window=9).ema_indicator()
    ema21 = ta.trend.EMAIndicator(c, window=21).ema_indicator()
    ema50 = ta.trend.EMAIndicator(c, window=50).ema_indicator()
    feat['EMA_9_21_Diff'] = (ema9 - ema21) / c
    feat['EMA_Above_50']  = (c > ema50).astype(int)

    # Volume
    vol_ma20 = v.rolling(20).mean()
    feat['Vol_Ratio']  = v / vol_ma20.replace(0, np.nan)
    feat['Vol_Change'] = v.pct_change(3)

    # Price momentum (perubahan relatif, bukan level harga)
    feat['Price_Change_1m'] = c.pct_change(1)
    feat['Price_Change_3m'] = c.pct_change(3)
    feat['Price_Change_5m'] = c.pct_change(5)

    # ATR
    feat['ATR_Pct'] = ta.volatility.AverageTrueRange(h, l, c, window=14).average_true_range() / c

    # OBV Slope
    obv = ta.volume.OnBalanceVolumeIndicator(c, v).on_balance_volume()
    feat['OBV_Slope'] = obv.diff(5) / (obv.abs().rolling(5).mean() + 1e-9)

    return feat


# ============================================================
# CELL 5: Label engineering
# ============================================================
def get_adaptive_target(df: pd.DataFrame) -> float:
    """
    Hitung threshold adaptif per koin berdasarkan median pergerakan harga
    dalam window LOOKAHEAD candle. Target = 0.8x median move (reachable tapi bermakna).
    """
    moves = df['close'].pct_change(LOOKAHEAD).abs().dropna()
    median_move = moves.median()
    target = round(float(median_move) * 0.8, 5)
    return max(TARGET_PCT_MIN, min(TARGET_PCT_MAX, target))


def build_label(df: pd.DataFrame, target_pct: float = TARGET_PCT) -> pd.Series:
    """BUY=1 jika harga naik > target_pct dalam LOOKAHEAD candle."""
    future = df['close'].shift(-LOOKAHEAD)
    return (future > df['close'] * (1 + target_pct)).astype(int)


# ============================================================
# CELL 6: Train loop
# ============================================================
results = []
failed  = []

for file in semua_file:
    nama_koin = os.path.basename(file).replace('.csv', '').lower()
    print(f"[{nama_koin:<20s}]", end=" ", flush=True)

    try:
        df = pd.read_csv(file)

        # Normalisasi nama kolom
        df.columns = [c.lower() for c in df.columns]

        # Sort by timestamp Unix int — lebih reliable dari datetime string
        if 'timestamp' in df.columns:
            df.sort_values('timestamp', inplace=True)
        elif 'datetime' in df.columns:
            df.sort_values('datetime', inplace=True)
        df.reset_index(drop=True, inplace=True)

        # Pastikan kolom OHLCV ada
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col not in df.columns:
                raise ValueError(f"Kolom '{col}' tidak ditemukan")

        df = df.astype({'open': float, 'high': float, 'low': float,
                        'close': float, 'volume': float})

        # Filter zero-volume candles (harga stagnan, tidak ada aktivitas nyata).
        # Forward-fill close dulu agar tidak ada gap harga setelah drop.
        df['close'] = df['close'].replace(0, np.nan).ffill()
        n_before = len(df)
        df = df[df['volume'] > 0].reset_index(drop=True)
        n_dropped = n_before - len(df)
        if n_dropped > 0:
            print(f"(drop {n_dropped} zero-vol = {n_dropped/n_before*100:.0f}%) ", end="", flush=True)

        if len(df) < MIN_SAMPLES:
            failed.append(nama_koin)
            print("SKIP (data < 500 rows)")
            continue

        # Hitung threshold adaptif untuk koin ini
        adaptive_target = get_adaptive_target(df)

        X = build_features(df)
        y = build_label(df, target_pct=adaptive_target)

        # Bersihkan NaN dan inf
        X = X.replace([np.inf, -np.inf], np.nan)
        valid = X.notna().all(axis=1) & y.notna()
        X, y = X[valid], y[valid]

        if len(X) < MIN_SAMPLES:
            failed.append(nama_koin)
            print("SKIP (setelah cleaning < 500 rows)")
            continue

        # Time-based split 80/20
        split = int(len(X) * 0.8)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]

        buy_ratio = y_train.mean()
        if buy_ratio < 0.05 or buy_ratio > 0.95:
            failed.append(nama_koin)
            print(f"SKIP (label imbalanced: buy_ratio={buy_ratio:.2f})")
            continue

        clf = RandomForestClassifier(
            n_estimators=N_ESTIMATORS,
            max_depth=8,
            min_samples_leaf=20,
            class_weight='balanced',
            n_jobs=-1,
            random_state=42,
        )
        clf.fit(X_train, y_train)

        y_prob = clf.predict_proba(X_test)[:, 1]
        y_pred = clf.predict(X_test)
        try:
            auc = round(roc_auc_score(y_test, y_prob), 3)
        except Exception:
            auc = 0.0

        report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
        precision_buy = round(report.get('1', {}).get('precision', 0), 3)
        recall_buy    = round(report.get('1', {}).get('recall', 0), 3)

        fi = dict(zip(X.columns, clf.feature_importances_))
        top3 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:3]

        out_path = os.path.join(FOLDER_MODEL, f"{nama_koin}{SUFFIX}")
        joblib.dump(clf, out_path)

        results.append({
            'pair': nama_koin, 'samples': len(X), 'buy_ratio': round(buy_ratio, 3),
            'target_pct': adaptive_target, 'auc': auc,
            'precision_buy': precision_buy, 'recall_buy': recall_buy,
            'top_features': top3,
        })
        print(f"AUC={auc:.3f} P={precision_buy:.2f} buy={buy_ratio:.2f} tgt={adaptive_target*100:.2f}% top={top3[0][0]}({top3[0][1]:.3f})")

    except Exception as e:
        failed.append(nama_koin)
        print(f"ERROR: {e}")

print(f"\n[DONE] Trained: {len(results)} | Gagal/Skip: {len(failed)}")


# ============================================================
# CELL 7: Summary report
# ============================================================
if results:
    df_res = pd.DataFrame(results).sort_values('auc', ascending=False)

    print("\n=== TOP 20 MODELS (by AUC) ===")
    print(df_res[['pair','samples','buy_ratio','target_pct','auc','precision_buy','recall_buy']].head(20).to_string(index=False))

    print(f"\n=== SUMMARY ===")
    print(f"Mean AUC:       {df_res['auc'].mean():.3f}")
    print(f"Median AUC:     {df_res['auc'].median():.3f}")
    print(f"AUC >= 0.55:    {(df_res['auc'] >= 0.55).sum()} models")

    print("\n=== GLOBAL FEATURE IMPORTANCE (avg top-3 across all coins) ===")
    global_fi = defaultdict(list)
    for r in results:
        for feat, imp in r['top_features']:
            global_fi[feat].append(imp)
    for feat, vals in sorted(global_fi.items(), key=lambda x: np.mean(x[1]), reverse=True):
        print(f"  {feat:<25s}: {np.mean(vals):.4f} (n={len(vals)})")

    summary_path = os.path.join(FOLDER_MODEL, "_training_summary.csv")
    df_res.drop(columns=['top_features']).to_csv(summary_path, index=False)
    print(f"\n[OK] Summary saved: {summary_path}")


# ============================================================
# CELL 8: Download sebagai zip (Colab only)
# ============================================================
# import shutil
# from google.colab import files
# shutil.make_archive('/content/algovibe_scalp_models', 'zip', FOLDER_MODEL)
# files.download('/content/algovibe_scalp_models.zip')
#
# Upload ke server:
# scp algovibe_scalp_models.zip root@SERVER_IP:/root/algovibe/
# cd /root/algovibe && unzip -o algovibe_scalp_models.zip -d models/