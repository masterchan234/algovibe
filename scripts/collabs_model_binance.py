# ============================================================
# CELL 1: Install dependencies
# ============================================================
# !pip install -q ta scikit-learn joblib pandas numpy requests

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
import joblib
import numpy as np
import requests
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
FOLDER_MODEL   = '/content/drive/MyDrive/Algovibe_Binance_Models'   # FOLDER TERPISAH dari Indodax
LOOKAHEAD      = 5        # prediksi t+5 candle (5 menit ke depan)
TARGET_PCT     = 0.003    # 0.3% default — akan di-override per koin secara adaptif
TARGET_PCT_MIN = 0.001    # floor 0.1%
TARGET_PCT_MAX = 0.008    # cap 0.8%
N_ESTIMATORS   = 200
MIN_SAMPLES    = 500
SUFFIX         = "_1m_scalp_classifier.pkl"

# Binance Data Vision config (CDN publik, tidak ada geo-block seperti API)
BINANCE_DATA_URL = "https://data.binance.vision/data/spot/daily/klines"
INTERVAL         = "1m"
DAYS_HISTORY     = 90
SLEEP_BETWEEN    = 0.3    # detik antar file download

# Daftar pair Binance yang ingin ditraining (USDT pairs)
# Tambah/kurangi sesuai kebutuhan
PAIRS_BINANCE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "TRXUSDT", "DOTUSDT",
    "MATICUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT",
    "ATOMUSDT", "UNIUSDT", "ETCUSDT", "FILUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT",
    "TIAUSDT", "ORDIUSDT", "WIFUSDT", "BONKUSDT", "JUPUSDT",
    "PENDLEUSDT", "DYMUSDT", "BLURUSDT", "JTOUSDT", "PYTHUSDT",
    "AEVOUSDT", "ALTUSDT", "RONINUSDT", "MANTAUSDT", "STRKUSDT",
    "PORTALUSDT", "PIXELUSDT", "ACEUSDT", "XAIUSDT", "NFTUSDT",
    "AIUSDT", "FETUSDT", "AGIXUSDT", "RENDERUSDT", "WLDUSDT",
    "NEARUSDT", "HBARUSDT", "ICPUSDT", "QNTUSDT", "GRTUSDT",
    "AAVEUSDT", "MKRUSDT", "SNXUSDT", "CRVUSDT", "COMPUSDT",
    "RUNEUSDT", "THETAUSDT", "FTMUSDT", "ALGOUSDT", "IOTAUSDT",
    "ZILUSDT", "ONEUSDT", "CELRUSDT", "ANKRUSDT", "COTIUSDT",
    "KSMUSDT", "KAVAUSDT", "BANDUSDT", "STORJUSDT", "SKLUSDT",
    "REQUSDT", "LRCUSDT", "OMGUSDT", "ZRXUSDT", "BATUSDT",
    "CHZUSDT", "ENJIDR", "MANAUSDT", "SANDUSDT", "AXSUSDT",
    "GALAUSDT", "IMXUSDT", "FLOWUSDT", "ENSUSDT", "SPELLUSDT",
    "HOOKUSDT", "MAGICUSDT", "HIFIUSDT", "IDUSDT", "ARKUSDT",
    "CYBERUSDT", "WAXPUSDT", "AGLDUSDT", "PERPUSDT", "ACHUSDT",
    "BADGERUSDT", "CELOUSDT", "CLVUSDT", "DOCKUSDT", "FIROUSDT",
    "FORTHUSDT", "GHSTUSDT", "GLMUSDT", "HIGHUSDT", "KNCUSDT",
    "MDXUSDT", "MLNUSDT", "MOVRUSDT", "NKNUSDT", "OGNUSDT",
    "PAXGUSDT", "POLSUSDT", "RAREUSDT", "REEFUSDT", "STMXUSDT",
    "TVKUSDT", "VOXELUSDT", "XVGUSDT", "YGGUSDT", "BELUSDT",
]

os.makedirs(FOLDER_MODEL, exist_ok=True)
print(f"[OK] Config: lookahead={LOOKAHEAD} candles, target={TARGET_PCT*100:.1f}%, suffix={SUFFIX}")
print(f"[OK] Output folder: {FOLDER_MODEL}")
print(f"[OK] Total pairs: {len(PAIRS_BINANCE)}")


# ============================================================
# CELL 4: Binance Data Vision fetcher (CDN publik, bebas geo-block)
# ============================================================
import io, zipfile
from datetime import datetime, timedelta

def fetch_binance_ohlcv(symbol: str, interval: str = "1m", days: int = 90) -> pd.DataFrame:
    """
    Ambil data OHLCV dari data.binance.vision (CDN resmi Binance).
    Download per file harian, tidak ada geo-restriction seperti REST API.
    Format URL: /data/spot/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{YYYY-MM-DD}.zip
    """
    today  = datetime.utcnow().date()
    frames = []

    for i in range(days):
        date = today - timedelta(days=days - i)
        date_str = date.strftime("%Y-%m-%d")
        url = f"{BINANCE_DATA_URL}/{symbol}/{interval}/{symbol}-{interval}-{date_str}.zip"

        try:
            resp = requests.get(url, timeout=20)
            if resp.status_code == 404:
                continue  # data belum tersedia untuk tanggal ini (wajar untuk hari ini)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    df_day = pd.read_csv(f, header=None, names=[
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_vol', 'n_trades', 'taker_buy_base',
                        'taker_buy_quote', 'ignore'
                    ])
                    frames.append(df_day[['timestamp', 'open', 'high', 'low', 'close', 'volume']])

            time.sleep(SLEEP_BETWEEN)

        except requests.exceptions.HTTPError:
            continue
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.astype({'timestamp': int, 'open': float, 'high': float,
                    'low': float, 'close': float, 'volume': float})
    df.sort_values('timestamp', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ============================================================
# CELL 5: Feature engineering
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

    # Price momentum
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
# CELL 6: Label engineering
# ============================================================
def get_adaptive_target(df: pd.DataFrame) -> float:
    moves  = df['close'].pct_change(LOOKAHEAD).abs().dropna()
    median_move = moves.median()
    target = round(float(median_move) * 0.8, 5)
    return max(TARGET_PCT_MIN, min(TARGET_PCT_MAX, target))


def build_label(df: pd.DataFrame, target_pct: float = TARGET_PCT) -> pd.Series:
    future = df['close'].shift(-LOOKAHEAD)
    return (future > df['close'] * (1 + target_pct)).astype(int)


# ============================================================
# CELL 7: Train loop
# ============================================================
results = []
failed  = []

for symbol in PAIRS_BINANCE:
    nama_koin = symbol.lower()
    print(f"[{nama_koin:<18s}]", end=" ", flush=True)

    try:
        # Ambil data dari Binance API
        df = fetch_binance_ohlcv(symbol, interval=INTERVAL, days=DAYS_HISTORY)

        if df.empty:
            failed.append(nama_koin)
            print("SKIP (data kosong dari API)")
            continue

        # Filter zero-volume candles
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

        # Threshold adaptif per koin
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
        split  = int(len(X) * 0.8)
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

        report        = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
        precision_buy = round(report.get('1', {}).get('precision', 0), 3)

        fi   = dict(zip(X.columns, clf.feature_importances_))
        top3 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:3]

        out_path = os.path.join(FOLDER_MODEL, f"{nama_koin}{SUFFIX}")
        joblib.dump(clf, out_path)

        results.append({
            'pair': nama_koin, 'samples': len(X), 'buy_ratio': round(buy_ratio, 3),
            'target_pct': adaptive_target, 'auc': auc,
            'precision_buy': precision_buy, 'top_features': top3,
        })
        print(f"AUC={auc:.3f} P={precision_buy:.2f} buy={buy_ratio:.2f} tgt={adaptive_target*100:.2f}% top={top3[0][0]}({top3[0][1]:.3f})")

    except Exception as e:
        failed.append(nama_koin)
        print(f"ERROR: {e}")

print(f"\n[DONE] Trained: {len(results)} | Gagal/Skip: {len(failed)}")


# ============================================================
# CELL 8: Summary report
# ============================================================
if results:
    df_res = pd.DataFrame(results).sort_values('auc', ascending=False)

    print("\n=== TOP 20 MODELS (by AUC) ===")
    print(df_res[['pair','samples','buy_ratio','target_pct','auc','precision_buy']].head(20).to_string(index=False))

    print(f"\n=== SUMMARY ===")
    print(f"Mean AUC:    {df_res['auc'].mean():.3f}")
    print(f"Median AUC:  {df_res['auc'].median():.3f}")
    print(f"AUC >= 0.55: {(df_res['auc'] >= 0.55).sum()} models")

    print("\n=== GLOBAL FEATURE IMPORTANCE ===")
    global_fi = defaultdict(list)
    for r in results:
        for feat, imp in r['top_features']:
            global_fi[feat].append(imp)
    for feat, vals in sorted(global_fi.items(), key=lambda x: np.mean(x[1]), reverse=True):
        print(f"  {feat:<25s}: {np.mean(vals):.4f} (n={len(vals)})")

    summary_path = os.path.join(FOLDER_MODEL, "_training_summary_binance.csv")
    df_res.drop(columns=['top_features']).to_csv(summary_path, index=False)
    print(f"\n[OK] Summary saved: {summary_path}")


# ============================================================
# CELL 9: Download sebagai zip (Colab only)
# ============================================================
# import shutil
# from google.colab import files
# shutil.make_archive('/content/algovibe_binance_models', 'zip', FOLDER_MODEL)
# files.download('/content/algovibe_binance_models.zip')
#
# Upload ke server:
# scp algovibe_binance_models.zip root@SERVER_IP:/root/algovibe/
# cd /root/algovibe && unzip -o algovibe_binance_models.zip -d models_binance/
