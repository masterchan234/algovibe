"""
predictor.py — Pure Technical Probability Engine (v2.0)

Menghitung probabilitas eksekusi murni berbasis indikator teknikal.
LLM/AI bias dihapus sepenuhnya — semua keputusan dari rumus matematis.
Model ML (RandomForest) tetap dipakai sebagai refinement, bukan decision-maker utama.
"""

import os
import time
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from app.domain.technicals import (
    calculate_rsi, calculate_macd, calculate_bollinger_bands,
    calculate_stochastic_rsi, calculate_ema, calculate_volatility,
    calculate_ema_slope, calculate_williams_r, calculate_roc,
    _MACD_NORM_FACTOR,
)
from utils.logger import log_message

#  Sigmoid RSI parameters 
# Sigmoid: 1 / (1 + exp((rsi - RSI_SIGMOID_CENTER) / RSI_SIGMOID_SCALE))
# Center=50 menjadikan RSI=50 sebagai titik netral (prob=0.50).
# Scale=12 memberikan gradien: RSI=20→0.92, RSI=30→0.84, RSI=70→0.16, RSI=80→0.08.
_RSI_SIGMOID_CENTER = 50.0
_RSI_SIGMOID_SCALE  = 12.0

#  ROC scoring parameters 
# Cap pada ±5% agar outlier tidak mendominasi (scalping jarang > 5% dalam 10 tick).
# Koefisien 0.08: setiap 1% ROC menggeser probabilitas ±0.08 dari titik netral 0.5.
# Contoh: ROC=-5% → prob=0.90; ROC=+5% → prob=0.10.
_ROC_CAP_PCT   = 5.0
_ROC_COEFF     = 0.08

#  Global model cache 
MODEL_PATH   = "models/scalper_v2.joblib"
_model       = None
_model_mtime: float = 0.0

#  Coin-specific model cache 
if os.path.exists("/app/models_coin"):
    COIN_MODELS_DIR = "/app/models_coin"
else:
    COIN_MODELS_DIR = "models"

_COIN_MODELS_CACHE = {}  # {pair: (model, P_train_mean, mtime)}

#  Per-coin indicator weight cache (derived from feature_importances_) 
_COIN_WEIGHTS_CACHE: dict = {}  # {pair: (weights_dict, mtime)}

# Flexible feature name → signal component mapping (case-insensitive).
# Covers common naming conventions from various Colab training scripts.
_FEATURE_SIGNAL_MAP = {
    # RSI family
    'rsi': 'rsi', 'rsi_change': 'rsi', 'rsi_slope': 'rsi',
    # MACD family
    'macd_hist': 'macd', 'macd': 'macd', 'macd_histogram': 'macd', 'macd_signal': 'macd',
    'macd_signal_cross': 'macd',
    # Bollinger family
    'bb_pct': 'bb', 'bb': 'bb', 'bb_position': 'bb', 'bollinger_pct': 'bb',
    'bb_pos': 'bb', 'bb_width': 'bb', 'bollinger_b': 'bb', 'pct_b': 'bb',
    # Stochastic family
    'stoch_k': 'stoch', 'stoch_d': 'stoch', 'stoch_cross': 'stoch',
    'stochastic': 'stoch', 'stoch': 'stoch', 'stochrsi': 'stoch',
    'stoch_rsi': 'stoch', 'stoch_rsi_k': 'stoch',
    # Volume family
    'volume': 'volume', 'vol': 'volume', 'volume_ratio': 'volume',
    'vol_change': 'volume', 'volume_change': 'volume', 'vol_ratio': 'volume',
    'obv_slope': 'volume',
    # EMA / trend — mapped ke macd (trend component)
    'ema_9_21_diff': 'macd', 'ema_above_50': 'macd',
    'williams_r': 'rsi',       # oscillator, karakter mirip RSI
    'atr_pct': 'bb',           # volatility, karakter mirip BB width
    # Price momentum — mapped ke macd (momentum component)
    'price_change_1m': 'macd', 'price_change_3m': 'macd', 'price_change_5m': 'macd',
}

_WEIGHTS_DEFAULT = {'rsi': 1.0, 'macd': 1.0, 'bb': 1.0, 'stoch': 1.0, 'volume': 1.0}


def get_coin_indicator_weights(pair: str) -> dict:
    """
    Derive per-coin indicator weight multipliers from model feature_importances_.

    Returns dict dengan multiplier [0.5, 2.0] untuk setiap komponen signal:
      {'rsi': float, 'macd': float, 'bb': float, 'stoch': float, 'volume': float}

    Multiplier > 1.0 = indikator ini lebih penting untuk koin ini dari rata-rata.
    Multiplier < 1.0 = indikator ini kurang relevan untuk koin ini.

    Fallback ke semua 1.0 jika:
    - Model tidak ditemukan
    - Model tidak memiliki feature_names_in_
    - Total importance indikator < 5% (model masih price-dominated)
    """
    global _COIN_WEIGHTS_CACHE

    if not pair:
        return dict(_WEIGHTS_DEFAULT)

    pair_key = pair.replace("/", "").replace("_", "").lower()

    # Check cache: valid selama mtime model tidak berubah.
    # Prioritas: _classifier.pkl (model baru, indicator-based) > _model.pkl (lama)
    current_mtime = 0.0
    model_path = None
    for suffix in (f"{pair_key}_1m_90d_1m_scalp_classifier.pkl", f"{pair_key}_1m_90d_classifier.pkl", f"{pair_key}_1m_90d_model.pkl"):
        candidate = os.path.join(COIN_MODELS_DIR, suffix)
        if os.path.exists(candidate):
            try:
                current_mtime = os.path.getmtime(candidate)
                model_path = candidate
            except OSError:
                pass
            break

    cached = _COIN_WEIGHTS_CACHE.get(pair_key)
    if cached and cached[1] == current_mtime and current_mtime > 0:
        return cached[0]

    if not model_path or current_mtime == 0:
        return dict(_WEIGHTS_DEFAULT)

    try:
        import joblib as _jl
        model = _jl.load(model_path)

        if not hasattr(model, 'feature_importances_') or not hasattr(model, 'feature_names_in_'):
            _COIN_WEIGHTS_CACHE[pair_key] = (dict(_WEIGHTS_DEFAULT), current_mtime)
            return dict(_WEIGHTS_DEFAULT)

        names = [n.lower() for n in model.feature_names_in_]
        importances = model.feature_importances_

        # Aggregate importances per signal component
        component_imp: dict = {}
        for name, imp in zip(names, importances):
            component = _FEATURE_SIGNAL_MAP.get(name)
            if component:
                component_imp[component] = component_imp.get(component, 0.0) + float(imp)

        # Guard: jika total importance indikator < 5%, model masih dominated by price
        total_ind_imp = sum(component_imp.values())
        if total_ind_imp < 0.05:
            _COIN_WEIGHTS_CACHE[pair_key] = (dict(_WEIGHTS_DEFAULT), current_mtime)
            return dict(_WEIGHTS_DEFAULT)

        # Normalize: weight = component_importance / mean_importance, clamped [0.5, 2.0]
        n_comp = len(component_imp)
        mean_imp = total_ind_imp / n_comp if n_comp > 0 else 1.0
        weights = dict(_WEIGHTS_DEFAULT)
        for comp, imp in component_imp.items():
            multiplier = imp / mean_imp if mean_imp > 0 else 1.0
            weights[comp] = max(0.5, min(2.0, round(multiplier, 3)))

        log_message(
            f"[Predictor] [OK] Coin weights {pair.upper()}: "
            f"RSI={weights['rsi']:.2f} MACD={weights['macd']:.2f} "
            f"BB={weights['bb']:.2f} Stoch={weights['stoch']:.2f} Vol={weights['volume']:.2f}"
        )
        _COIN_WEIGHTS_CACHE[pair_key] = (weights, current_mtime)
        return weights

    except Exception as e:
        log_message(f"[WARN] [Predictor] get_coin_indicator_weights({pair}) failed: {e}")
        _COIN_WEIGHTS_CACHE[pair_key] = (dict(_WEIGHTS_DEFAULT), current_mtime)
        return dict(_WEIGHTS_DEFAULT)


def _load_coin_model(pair: str):
    global _COIN_MODELS_CACHE
    if not pair:
        return None, 0.0
    
    pair = pair.replace("/", "").replace("_", "").lower()
    model_file = f"{pair}_1m_90d_model.pkl"
    path = os.path.join(COIN_MODELS_DIR, model_file)
    
    if not os.path.exists(path):
        # C1: log warning agar operator tahu model hilang (bukan silent fallback)
        log_message(f"[WARN] [Predictor] Coin model tidak ditemukan untuk {pair.upper()} ({path}) — fallback ke global model")
        return None, 0.0
        
    try:
        current_mtime = os.path.getmtime(path)
        cached = _COIN_MODELS_CACHE.get(pair)
        
        if cached is None or current_mtime > cached[2]:
            model = joblib.load(path)

            # H2: validasi tipe model setelah load untuk deteksi korupsi file
            from sklearn.ensemble import RandomForestRegressor as _RFR
            if not isinstance(model, _RFR):
                log_message(f"[WARN] [Predictor] Coin model {pair.upper()} tipe tidak valid ({type(model).__name__}) — fallback ke global model")
                return None, 0.0
            
            # C2 + H1: Tentukan index SMA_20 secara dinamis dari feature_names_in_ jika tersedia
            # Hindari asumsi hardcoded bahwa SMA_20 selalu di index 6.
            sma_index = 6  # default fallback
            if hasattr(model, 'feature_names_in_'):
                feat_list = list(model.feature_names_in_)
                if 'SMA_20' in feat_list:
                    sma_index = feat_list.index('SMA_20')
                else:
                    log_message(f"[WARN] [Predictor] Coin model {pair.upper()} tidak memiliki fitur SMA_20 — P_train_mean tidak dapat diekstrak")
                    return None, 0.0
            
            # Ekstrak split thresholds SMA_20 dari semua tree sebagai estimasi P_train_mean
            sma_thresholds = []
            for estimator in model.estimators_:
                tree = estimator.tree_
                for node_idx in range(tree.node_count):
                    if tree.feature[node_idx] == sma_index:
                        sma_thresholds.append(tree.threshold[node_idx])
            
            P_train_mean = np.mean(sma_thresholds) if sma_thresholds else 0.0

            # H1: sanity check — jika P_train_mean jauh dari harga wajar, model mungkin stale
            if P_train_mean <= 0:
                log_message(f"[WARN] [Predictor] Coin model {pair.upper()} P_train_mean={P_train_mean} tidak valid — fallback ke global model")
                return None, 0.0
            
            _COIN_MODELS_CACHE[pair] = (model, P_train_mean, current_mtime)
            log_message(
                f"[Predictor] [OK] Loaded coin model for {pair.upper()} "
                f"(P_train_mean={P_train_mean:.8f}, SMA_idx={sma_index}, mtime={datetime.fromtimestamp(current_mtime).strftime('%H:%M:%S')})"
            )
            
        return _COIN_MODELS_CACHE[pair][0], _COIN_MODELS_CACHE[pair][1]
        
    except Exception as e:
        log_message(f"[WARN] [Predictor] Failed to load coin model for {pair}: {e}")
        return None, 0.0


def _load_model():
    global _model, _model_mtime
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        current_mtime = os.path.getmtime(MODEL_PATH)
        if _model is None or current_mtime > _model_mtime:
            loaded = joblib.load(MODEL_PATH)
            # H2: validasi tipe global model
            from sklearn.ensemble import HistGradientBoostingClassifier as _HGBC
            from sklearn.ensemble import RandomForestClassifier as _RFC
            if not isinstance(loaded, (_HGBC, _RFC)):
                log_message(f"[WARN] [Predictor] Global model tipe tidak valid ({type(loaded).__name__}) — model tidak diganti")
            else:
                _model = loaded
                _model_mtime = current_mtime
                log_message(
                    f"[Predictor] Scalper V2 Model loaded "
                    f"(mtime={datetime.fromtimestamp(current_mtime).strftime('%H:%M:%S')})."
                )
    except Exception as e:
        log_message(f"[WARN] [Predictor] Failed to load model: {e}")
    return _model


def preload_model() -> None:
    """Pre-load the ML model into the global cache at startup.

    Calling this once at bot startup ensures subsequent calls to get_prediction()
    return immediately without paying the joblib load cost on the first trade.
    """
    model = _load_model()
    if model is not None:
        log_message("[OK] [Predictor] ML model pre-loaded into memory at startup.")
    else:
        log_message("[WARN] [Predictor] ML model not found at startup (will load on demand when available).")


def _compute_technical_probability(price_history: list, ticker_data: dict = None) -> float:
    """
    Menghitung probabilitas BUY murni dari indikator teknikal.
    Returns float 0.0 - 1.0 (bukan bias LLM).

    Logika:
    - Setiap indikator berkontribusi ke score dalam rentang 0-1.
    - Agregasi dengan bobot berbeda per indikator.
    - Skor akhir = weighted average, dikembalikan sebagai probabilitas.
    """
    prices = list(price_history)
    n = len(prices)
    if n < 5:
        return 0.45  # Kurang data, netral

    last_price = max(1.0, float(prices[-1]))  # Harga terakhir untuk normalisasi MACD
    scores = []
    weights = []

    #  1. RSI (bobot 3.0) — Sigmoid continuous scoring 
    # Sigmoid menghilangkan quantization error dari step-function dan memberikan
    # gradien probabilitas yang lebih halus: RSI=20→0.92, RSI=30→0.84, RSI=50→0.50,
    # RSI=70→0.16, RSI=80→0.08.
    rsi = calculate_rsi(prices)
    rsi_score = float(1.0 / (1.0 + np.exp((rsi - _RSI_SIGMOID_CENTER) / _RSI_SIGMOID_SCALE)))
    scores.append(max(0.05, min(0.95, rsi_score)))
    weights.append(3.0)

    #  2. MACD Histogram (bobot 2.5) — price-relative normalisasi 
    # Normalisasi |hist| / harga menghilangkan scale-bias antar koin IDR.
    if n >= 35:
        _, _, macd_hist = calculate_macd(prices)
        norm_mh = abs(macd_hist) / last_price * _MACD_NORM_FACTOR
        if macd_hist > 0:
            macd_score = min(0.95, 0.55 + norm_mh)
        else:
            macd_score = max(0.05, 0.45 - norm_mh)
        scores.append(macd_score)
        weights.append(2.5)

    #  3. Bollinger %B (bobot 2.0) 
    if n >= 20:
        _, _, _, bb_pct = calculate_bollinger_bands(prices)
        # %B rendah = harga di lower band = peluang rebound
        if bb_pct < 0.10:
            bb_score = 0.90
        elif bb_pct < 0.25:
            bb_score = 0.70
        elif bb_pct < 0.50:
            bb_score = 0.55
        elif bb_pct < 0.75:
            bb_score = 0.40
        elif bb_pct < 0.90:
            bb_score = 0.28
        else:
            bb_score = 0.12
        scores.append(bb_score)
        weights.append(2.0)

    #  4. Stochastic RSI (bobot 2.0) 
    stoch_k, stoch_d = calculate_stochastic_rsi(prices)
    if stoch_k < 20:
        stoch_score = 0.88
    elif stoch_k < 40:
        stoch_score = 0.70
    elif stoch_k < 60:
        stoch_score = 0.50
    elif stoch_k < 80:
        stoch_score = 0.30
    else:
        stoch_score = 0.12
    # Bonus jika K dan D konfluens
    if stoch_k < stoch_d and stoch_k < 50:
        stoch_score = min(0.95, stoch_score + 0.08)
    scores.append(stoch_score)
    weights.append(2.0)

    #  5. EMA Cross (EMA9 vs EMA21) (bobot 1.5) 
    if n >= 21:
        ema9  = calculate_ema(prices, 9)
        ema21 = calculate_ema(prices, 21)
        if ema9 > ema21:
            # Bullish cross — seberapa jauh EMA9 di atas EMA21
            gap_pct = (ema9 - ema21) / ema21 * 100
            scores.append(min(0.90, 0.60 + gap_pct * 5))
        else:
            gap_pct = (ema21 - ema9) / ema21 * 100
            scores.append(max(0.10, 0.40 - gap_pct * 5))
        weights.append(1.5)

    #  6. Williams %R (bobot 1.5) 
    # Konfirmator oversold/overbought independen: -100=oversold(BUY), 0=overbought(SELL).
    # Mapping: abs(wr)/100 → oversold=-100 memberi score 1.0, overbought=0 memberi 0.0.
    if n >= 14:
        wr = calculate_williams_r(prices)
        wr_score = max(0.05, min(0.95, abs(wr) / 100.0))
        scores.append(wr_score)
        weights.append(1.5)

    #  7. EMA Slope/Momentum (bobot 1.0) 
    if n >= 25:
        slope = calculate_ema_slope(prices, ema_period=20, lookback=5)
        if slope > 0.5:
            scores.append(0.82)
        elif slope > 0.1:
            scores.append(0.65)
        elif slope > -0.1:
            scores.append(0.50)
        elif slope > -0.5:
            scores.append(0.35)
        else:
            scores.append(0.18)
        weights.append(1.0)

    #  8. Rate of Change / ROC-10 (bobot 1.0) 
    # Interpretasi mean-reversion: ROC negatif = oversold → BUY probability tinggi.
    if n >= 11:
        roc = calculate_roc(prices)
        roc_capped = max(-_ROC_CAP_PCT, min(_ROC_CAP_PCT, roc))
        roc_score = max(0.05, min(0.95, 0.5 - roc_capped * _ROC_COEFF))
        scores.append(roc_score)
        weights.append(1.0)

    #  9. Order Book Pressure (bobot 2.0) 
    if ticker_data:
        bid_depth = float(ticker_data.get("bid_depth", 0))
        ask_depth = float(ticker_data.get("ask_depth", 0))
        if (bid_depth + ask_depth) > 0:
            imbalance_val = bid_depth / (bid_depth + ask_depth)
            if imbalance_val > 0.65:
                ob_score = 0.88
            elif imbalance_val > 0.55:
                ob_score = 0.70
            elif imbalance_val > 0.45:
                ob_score = 0.50
            elif imbalance_val > 0.35:
                ob_score = 0.30
            else:
                ob_score = 0.15
            scores.append(ob_score)
            weights.append(2.0)

    if not scores:
        return 0.45

    # Weighted average
    total_weight = sum(weights)
    prob = sum(s * w for s, w in zip(scores, weights)) / total_weight
    return float(round(max(0.0, min(0.99, prob)), 4))


def get_prediction(price_history: list, ticker_data: dict = None, pair: str = None) -> float:
    """
    Probabilitas eksekusi trade — PURE TECHNICAL (v2.0) + Coin-Specific ML Refinement.

    Perubahan dari v1:
    - Semua pengaruh LLM/AI dihapus sepenuhnya.
    - Base score 100% dari indikator teknikal (RSI, MACD, BB, StochRSI, EMA, WilliamsR, ROC, OB).
    - Model ML koin khusus (RandomForestRegressor) dimuat dinamis dengan penskalaan harga otomatis.
    - Bobot blend dinamis berdasarkan tingkat kecocokan tech vs ML.

    Returns:
        -1.0  → Warming Up (data kurang dari minimum)
        0.0-0.99 → Probabilitas bullish saat ini
    """
    from config import TECH_MIN_HISTORY_POINTS

    n = len(price_history)
    if n < TECH_MIN_HISTORY_POINTS:
        return -1.0  # Warming Up

    try:
        #  1. Pure Technical Base Score 
        tech_prob = _compute_technical_probability(price_history, ticker_data)

        #  2. Machine Learning Refinement (Coin-Specific RandomForestRegressor)
        if not pair and ticker_data:
            pair = ticker_data.get("pair")
            
        model_coin, P_train_mean = _load_coin_model(pair) if pair else (None, 0.0)
        
        if model_coin and P_train_mean > 0:
            prices = list(price_history)
            
            # Compute input features: ['open', 'high', 'low', 'close', 'volume', 'RSI', 'SMA_20']
            close_val = float(prices[-1])
            open_val = float(prices[-2]) if len(prices) >= 2 else close_val
            high_val = max(open_val, close_val)
            low_val = min(open_val, close_val)
            
            rsi_val = calculate_rsi(prices)
            sma_val = sum(prices[-20:]) / 20.0
            
            # Dummy volume (since importance is 0.0)
            vol_val = 10000.0
            
            # Apply Dynamic Price Scaling
            P_current = sma_val if sma_val > 0 else close_val
            S = P_train_mean / P_current if P_current > 0 else 1.0
            
            open_scaled = open_val * S
            high_scaled = high_val * S
            low_scaled = low_val * S
            close_scaled = close_val * S
            sma_scaled = sma_val * S
            
            # Feature DataFrame
            features_df = pd.DataFrame([{
                "open": open_scaled,
                "high": high_scaled,
                "low": low_scaled,
                "close": close_scaled,
                "volume": vol_val,
                "RSI": rsi_val,
                "SMA_20": sma_scaled
            }])
            
            # Predict
            pred_scaled = model_coin.predict(features_df)[0]
            
            # Rescale prediction back up
            pred_final = pred_scaled / S
            
            # Projected expected return
            expected_return = (pred_final - close_val) / close_val if close_val > 0 else 0.0
            
            # Map return to probability using sigmoid: 0% -> 50%, +1% -> 82%, -1% -> 18%
            ml_prob = float(1.0 / (1.0 + np.exp(-expected_return * 150.0)))
            
            # Blend dynamically
            divergence = abs(tech_prob - ml_prob)
            if divergence < 0.10:
                final_prob = (tech_prob * 0.55) + (ml_prob * 0.45)
            elif divergence < 0.25:
                final_prob = (tech_prob * 0.65) + (ml_prob * 0.35)
            else:
                final_prob = (tech_prob * 0.75) + (ml_prob * 0.25)
                
            return float(round(max(0.0, min(final_prob, 0.99)), 4))

        # Fallback to original global model (scalper_v2.joblib) if available
        model = _load_model()
        if model:
            rsi = calculate_rsi(price_history)
            _, _, macd_hist = calculate_macd(price_history) if n >= 35 else (0, 0, 0)
            _, _, _, bb_pct = calculate_bollinger_bands(price_history) if n >= 20 else (0, 0, 0, 0.5)
            stoch_k, stoch_d = calculate_stochastic_rsi(price_history)
            vol = calculate_volatility(price_history)

            ema9  = calculate_ema(price_history, 9) if n >= 9 else price_history[-1]
            ema21 = calculate_ema(price_history, 21) if n >= 21 else price_history[-1]
            ema_dist = (price_history[-1] - ema21) / ema21 if ema21 > 0 else 0.0

            imbalance_val = 0.5
            if ticker_data:
                bd = float(ticker_data.get("bid_depth", 0))
                ad = float(ticker_data.get("ask_depth", 0))
                if (bd + ad) > 0:
                    imbalance_val = bd / (bd + ad)

            hour = datetime.now().hour

            features_df = pd.DataFrame([{
                "ai_prob":          tech_prob,
                "imbalance":        imbalance_val,
                "volatility":       vol,
                "rsi":              rsi,
                "ema_dist":         ema_dist,
                "ema_dist_abs":     abs(ema_dist),
                "rsi_oversold":     max(0.0, (30.0 - rsi) / 30.0),
                "rsi_overbought":   max(0.0, (rsi - 70.0) / 30.0),
                "hour_sin":         np.sin(2 * np.pi * hour / 24.0),
                "hour_cos":         np.cos(2 * np.pi * hour / 24.0),
            }])

            # Validasi feature compatibility — model lama mungkin expect feature set berbeda
            if hasattr(model, 'feature_names_in_'):
                expected = set(model.feature_names_in_)
                provided = set(features_df.columns)
                if not expected.issubset(provided):
                    model = None
            if model is None:
                final_prob = tech_prob
            else:
                ml_prob = model.predict_proba(features_df)[0][1]

                divergence = abs(tech_prob - ml_prob)
                if divergence < 0.10:
                    final_prob = (tech_prob * 0.55) + (ml_prob * 0.45)
                elif divergence < 0.25:
                    final_prob = (tech_prob * 0.65) + (ml_prob * 0.35)
                else:
                    final_prob = (tech_prob * 0.75) + (ml_prob * 0.25)
        else:
            final_prob = tech_prob

        return float(round(max(0.0, min(final_prob, 0.99)), 4))

    except Exception as e:
        log_message(f"[WARN] [Predictor] Prediction Error: {e}")
        return 0.45  # Default netral, bukan 0 (agar tidak memblokir semua sinyal)
