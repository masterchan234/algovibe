"""
================================================================================
AlgoVibe — Scalping Signal Classifier Trainer
Google Colab Notebook
================================================================================

Target:
  Binary classifier: apakah harga akan naik > 0.3% dalam 5 candle 1m ke depan
  (setelah estimasi fee 0.3% taker Indodax)

Model: RandomForestClassifier (100% indicator-based, zero price leakage)
Output: {pair}_1m_scalp_classifier.pkl — kompatibel dengan get_coin_indicator_weights()

CARA PAKAI DI COLAB:
  1. Copy semua cell ke Colab
  2. Upload file pairs.txt (list koin Indodax) atau biarkan auto-fetch
  3. Klik Runtime → Run all
  4. Download folder /content/models/ lalu upload ke server /root/algovibe/models/
================================================================================
"""

# ============================================================
# CELL 1: Install dependencies
# ============================================================
# !pip install -q requests pandas numpy scikit-learn joblib ta

# ============================================================
# CELL 2: Config
# ============================================================
import os, time, requests, warnings
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import ta  # technical analysis library

warnings.filterwarnings('ignore')

# --- CONFIG ---
INDODAX_BASE   = "https://indodax.com/api"
TIMEFRAME      = "1m"          # candle timeframe
LOOKAHEAD      = 5             # prediksi t+5 candle ke depan
TARGET_PCT     = 0.003         # 0.3% minimum profit (setelah fee taker 0.3%)
N_CANDLES      = 10_000        # data per koin (~7 hari untuk 1m)
N_ESTIMATORS   = 200
MIN_SAMPLES    = 500           # skip koin jika data kurang
OUTPUT_DIR     = "/content/models"
SUFFIX         = "_1m_scalp_classifier.pkl"  # filename suffix baru

os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"[OK] Config: lookahead={LOOKAHEAD} candles, target={TARGET_PCT*100:.1f}%, output={OUTPUT_DIR}")


# ============================================================
# CELL 3: Fetch pair list from Indodax
# ============================================================
def get_all_idr_pairs() -> list[str]:
    """Fetch semua pair IDR aktif dari Indodax ticker API."""
    resp = requests.get(f"{INDODAX_BASE}/pairs", timeout=10)
    pairs = resp.json()
    idr_pairs = [p['id'] for p in pairs if p['id'].endswith('_idr')]
    print(f"[OK] Total IDR pairs: {len(idr_pairs)}")
    return idr_pairs

PAIRS = get_all_idr_pairs()
# Untuk test subset: PAIRS = ['btc_idr', 'eth_idr', 'bnb_idr', 'sol_idr', 'xrp_idr']


# ============================================================
# CELL 4: OHLCV fetcher
# ============================================================
def fetch_ohlcv(pair: str, n: int = N_CANDLES) -> pd.DataFrame | None:
    """
    Fetch OHLCV candles dari Indodax chart API.
    Returns DataFrame dengan kolom: open, high, low, close, volume
    """
    # Indodax chart endpoint: /tradingview/history
    symbol = pair.replace("_", "").upper()
    url = "https://indodax.com/tradingview/history"
    
    to_ts   = int(time.time())
    from_ts = to_ts - (n * 60) - 3600  # extra buffer
    
    params = {
        "symbol": symbol,
        "resolution": "1",   # 1 = 1 minute
        "from": from_ts,
        "to": to_ts,
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        d = resp.json()
        
        if d.get('s') != 'ok' or not d.get('t'):
            return None
        
        df = pd.DataFrame({
            'time':   d['t'],
            'open':   d['o'],
            'high':   d['h'],
            'low':    d['l'],
            'close':  d['c'],
            'volume': d['v'],
        })
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time').sort_index()
        df = df.astype(float)
        
        # Drop duplicate timestamps
        df = df[~df.index.duplicated(keep='last')]
        
        return df if len(df) >= MIN_SAMPLES else None
        
    except Exception as e:
        return None


# ============================================================
# CELL 5: Feature engineering
# ============================================================
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Buat fitur 100% indicator-based (ZERO price leakage).
    Semua fitur adalah perubahan relatif atau posisi normalized,
    bukan nilai harga absolut.
    """
    c = df['close']
    h = df['high']
    l = df['low']
    v = df['volume']
    
    feat = pd.DataFrame(index=df.index)
    
    # --- RSI (14) ---
    rsi_ind = ta.momentum.RSIIndicator(c, window=14)
    feat['RSI']        = rsi_ind.rsi()
    feat['RSI_Change'] = feat['RSI'].diff(3)   # momentum RSI 3 candle
    feat['RSI_Slope']  = feat['RSI'].diff(1)   # arah RSI saat ini
    
    # --- MACD ---
    macd_ind = ta.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    feat['MACD_Hist']  = macd_ind.macd_diff()
    feat['MACD_Signal_Cross'] = (macd_ind.macd_diff() > 0).astype(int)  # 1=bullish
    
    # --- Bollinger Bands (20) ---
    bb_ind = ta.volatility.BollingerBands(c, window=20, window_dev=2)
    bb_upper = bb_ind.bollinger_hband()
    bb_lower = bb_ind.bollinger_lband()
    bb_mid   = bb_ind.bollinger_mavg()
    bb_width = bb_upper - bb_lower
    feat['BB_Pos']     = (c - bb_lower) / bb_width.replace(0, np.nan)  # [0,1]
    feat['BB_Width']   = bb_width / c  # volatility relatif
    
    # --- Stochastic RSI ---
    stoch_rsi = ta.momentum.StochRSIIndicator(c, window=14, smooth1=3, smooth2=3)
    feat['Stoch_K']    = stoch_rsi.stochrsi_k() * 100
    feat['Stoch_D']    = stoch_rsi.stochrsi_d() * 100
    feat['Stoch_Cross'] = (feat['Stoch_K'] - feat['Stoch_D'])  # positif = K > D
    
    # --- Williams %R (14) ---
    wr_ind = ta.momentum.WilliamsRIndicator(h, l, c, lbp=14)
    feat['Williams_R'] = wr_ind.williams_r()  # range [-100, 0]
    
    # --- EMA Cross ---
    ema9  = ta.trend.EMAIndicator(c, window=9).ema_indicator()
    ema21 = ta.trend.EMAIndicator(c, window=21).ema_indicator()
    ema50 = ta.trend.EMAIndicator(c, window=50).ema_indicator()
    feat['EMA_9_21_Diff']  = (ema9 - ema21) / c  # normalized
    feat['EMA_Above_50']   = (c > ema50).astype(int)  # trend filter
    
    # --- Volume ---
    vol_ma20 = v.rolling(20).mean()
    feat['Vol_Ratio']  = v / vol_ma20.replace(0, np.nan)   # spike relative to MA
    feat['Vol_Change'] = v.pct_change(3)                   # perubahan 3 candle
    
    # --- Price Momentum (BUKAN level harga) ---
    feat['Price_Change_1m']  = c.pct_change(1)   # 1 candle return
    feat['Price_Change_3m']  = c.pct_change(3)   # 3 candle return
    feat['Price_Change_5m']  = c.pct_change(5)   # 5 candle return
    
    # --- ATR (volatility) ---
    atr_ind = ta.volatility.AverageTrueRange(h, l, c, window=14)
    feat['ATR_Pct'] = atr_ind.average_true_range() / c  # normalized
    
    # --- OBV Slope ---
    obv = ta.volume.OnBalanceVolumeIndicator(c, v).on_balance_volume()
    feat['OBV_Slope'] = obv.diff(5) / (obv.abs().rolling(5).mean() + 1e-9)  # normalized
    
    return feat


# ============================================================
# CELL 6: Label engineering
# ============================================================
def build_label(df: pd.DataFrame, lookahead: int = LOOKAHEAD,
                target_pct: float = TARGET_PCT) -> pd.Series:
    """
    Label BUY = 1 jika close[t+lookahead] > close[t] * (1 + target_pct)
    Label BUY = 0 sebaliknya (hold/sell)
    
    Ini mensimulasikan: beli sekarang, profitable dalam {lookahead} menit?
    """
    future_close = df['close'].shift(-lookahead)
    current_close = df['close']
    label = (future_close > current_close * (1 + target_pct)).astype(int)
    return label


# ============================================================
# CELL 7: Train single pair
# ============================================================
def train_pair(pair: str) -> dict | None:
    """
    Fetch data, build features, train classifier, save model.
    Returns dict dengan metrics atau None jika gagal.
    """
    pair_key = pair.replace("_", "").lower()
    
    # 1. Fetch OHLCV
    df = fetch_ohlcv(pair)
    if df is None:
        return None
    
    # 2. Features + Label
    X = build_features(df)
    y = build_label(df)
    
    # Drop rows dengan NaN (warmup period indikator + lookahead tail)
    valid = X.notna().all(axis=1) & y.notna()
    X = X[valid]
    y = y[valid]
    
    if len(X) < MIN_SAMPLES:
        return None
    
    # 3. Train/test split (time-based, NO shuffle)
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    
    # 4. Class balance check
    buy_ratio = y_train.mean()
    if buy_ratio < 0.05 or buy_ratio > 0.95:
        # Skip koin dengan label terlalu imbalanced
        return None
    
    # 5. Train
    clf = RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_depth=8,             # prevent overfit
        min_samples_leaf=20,     # conservative
        class_weight='balanced', # handle imbalance
        n_jobs=-1,
        random_state=42,
    )
    clf.fit(X_train, y_train)
    
    # 6. Evaluate
    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]
    
    try:
        auc = roc_auc_score(y_test, y_prob)
    except Exception:
        auc = 0.0
    
    report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    precision_buy = report.get('1', {}).get('precision', 0)
    recall_buy    = report.get('1', {}).get('recall', 0)
    
    # Feature importances
    fi = dict(zip(X.columns, clf.feature_importances_))
    top3 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:3]
    
    # 7. Save model
    out_path = os.path.join(OUTPUT_DIR, f"{pair_key}{SUFFIX}")
    joblib.dump(clf, out_path)
    
    return {
        'pair':          pair,
        'samples':       len(X),
        'buy_ratio':     round(buy_ratio, 3),
        'auc':           round(auc, 3),
        'precision_buy': round(precision_buy, 3),
        'recall_buy':    round(recall_buy, 3),
        'top_features':  top3,
        'saved':         out_path,
    }


# ============================================================
# CELL 8: Train all pairs (main loop)
# ============================================================
results = []
failed  = []

total = len(PAIRS)
for i, pair in enumerate(PAIRS, 1):
    print(f"[{i:3d}/{total}] {pair:<20s}", end=" ", flush=True)
    
    try:
        r = train_pair(pair)
        if r:
            results.append(r)
            top = r['top_features'][0]
            print(f"AUC={r['auc']:.3f} P={r['precision_buy']:.2f} "
                  f"buy_ratio={r['buy_ratio']:.2f} "
                  f"top={top[0]}({top[1]:.3f})")
        else:
            failed.append(pair)
            print("SKIP (insufficient data or imbalanced)")
    except Exception as e:
        failed.append(pair)
        print(f"ERROR: {e}")
    
    # Rate limit courtesy delay
    time.sleep(0.3)

print(f"\n[DONE] Trained: {len(results)} | Failed/Skipped: {len(failed)}")


# ============================================================
# CELL 9: Summary report
# ============================================================
if results:
    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values('auc', ascending=False)
    
    print("\n=== TOP 20 MODELS (by AUC) ===")
    print(df_res[['pair','samples','buy_ratio','auc','precision_buy','recall_buy']].head(20).to_string(index=False))
    
    print(f"\n=== SUMMARY ===")
    print(f"Mean AUC:           {df_res['auc'].mean():.3f}")
    print(f"Median AUC:         {df_res['auc'].median():.3f}")
    print(f"AUC >= 0.55:        {(df_res['auc'] >= 0.55).sum()} models")
    print(f"Mean Precision BUY: {df_res['precision_buy'].mean():.3f}")
    
    # Feature importance aggregation across all coins
    print("\n=== GLOBAL FEATURE IMPORTANCE (avg across all coins) ===")
    from collections import defaultdict
    global_fi = defaultdict(list)
    for r in results:
        for feat, imp in r['top_features']:
            global_fi[feat].append(imp)
    global_avg = {k: np.mean(v) for k, v in global_fi.items()}
    for feat, avg in sorted(global_avg.items(), key=lambda x: x[1], reverse=True):
        print(f"  {feat:<25s}: {avg:.4f}")
    
    # Save summary CSV
    summary_path = os.path.join(OUTPUT_DIR, "_training_summary.csv")
    df_res.to_csv(summary_path, index=False)
    print(f"\n[OK] Summary saved: {summary_path}")


# ============================================================
# CELL 10: Download models (Colab only)
# ============================================================
# Jalankan cell ini untuk download semua model sebagai zip:
#
# import shutil
# shutil.make_archive('/content/algovibe_scalp_models', 'zip', OUTPUT_DIR)
# from google.colab import files
# files.download('/content/algovibe_scalp_models.zip')
#
# Setelah download, upload ke server:
# scp algovibe_scalp_models.zip root@SERVER_IP:/root/algovibe/
# cd /root/algovibe && unzip -o algovibe_scalp_models.zip -d models/


# ============================================================
# CELL 11: Verify single model (optional)
# ============================================================
# import joblib
# model = joblib.load('/content/models/btcidr_1m_scalp_classifier.pkl')
# print("Type:", type(model).__name__)
# print("Features:", list(model.feature_names_in_))
# print("Classes:", model.classes_)
# fi = dict(zip(model.feature_names_in_, model.feature_importances_))
# for f, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True):
#     print(f"  {f:<25s}: {imp:.4f}")
