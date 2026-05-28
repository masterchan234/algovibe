"""
predictor.py — Pure Technical Probability Engine (v2.0)

Menghitung probabilitas eksekusi murni berbasis indikator teknikal.
LLM/AI bias dihapus sepenuhnya — semua keputusan dari rumus matematis.
Model ML (RandomForest) tetap dipakai sebagai refinement, bukan decision-maker utama.
"""

import os
import joblib
import numpy as np
from app.domain.technicals import (
    calculate_rsi, calculate_macd, calculate_bollinger_bands,
    calculate_stochastic_rsi, calculate_ema,
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

#  Per-pair model cache: {pair: (model, mtime)} 
_PAIR_MODEL_DIR  = "models"
_pair_models: dict = {}

_PAIR_FEATURE_NAMES = [
    "RSI", "RSI_Change", "RSI_Slope", "MACD_Hist", "MACD_Signal_Cross",
    "BB_Pos", "BB_Width", "Stoch_K", "Stoch_D", "Stoch_Cross",
    "Williams_R", "EMA_9_21_Diff", "EMA_Above_50", "Vol_Ratio", "Vol_Change",
    "Price_Change_1m", "Price_Change_3m", "Price_Change_5m", "ATR_Pct", "OBV_Slope",
]


def _load_pair_model(pair: str):
    """Load per-pair scalp classifier dari models/{pair}_1m_90d_1m_scalp_classifier.pkl.
    Hot-reload otomatis jika file diperbarui. Returns model atau None.
    """
    global _pair_models
    path = os.path.join(_PAIR_MODEL_DIR, f"{pair}_1m_90d_1m_scalp_classifier.pkl")
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        cached = _pair_models.get(pair)
        if cached is None or mtime > cached[1]:
            model = joblib.load(path)
            _pair_models[pair] = (model, mtime)
            log_message(f"[OK] [Predictor] Per-pair model loaded: {pair}")
        return _pair_models[pair][0]
    except Exception as e:
        log_message(f"[WARN] [Predictor] Failed to load pair model {pair}: {e}")
        return None


def _build_pair_features(prices: list) -> "np.ndarray | None":
    """Bangun 20-feature vector yang kompatibel dengan train_scalp_classifiers.py.

    Fitur berbasis OHLCV yang tidak tersedia (volume, high, low) di-approximate
    dari data close saja — Williams %R via close min/max, ATR via selisih close,
    volume features (Vol_Ratio, Vol_Change, OBV_Slope) fallback ke nilai netral.
    """
    n = len(prices)
    if n < 30:
        return None

    closes = np.array(prices, dtype=float)

    # 1-3. RSI, RSI_Change, RSI_Slope
    rsi_now   = calculate_rsi(prices)
    rsi_prev3 = calculate_rsi(list(closes[:-3])) if n > 16 else rsi_now
    rsi_change = rsi_now - rsi_prev3
    rsi_slope  = rsi_change / 3.0

    # 4-5. MACD_Hist, MACD_Signal_Cross
    macd_hist, prev_hist = 0.0, 0.0
    if n >= 35:
        _, _, macd_hist = calculate_macd(prices)
    if n >= 36:
        _, _, prev_hist = calculate_macd(list(closes[:-1]))
    macd_cross = (1.0 if (macd_hist > 0 and prev_hist <= 0) else
                  -1.0 if (macd_hist < 0 and prev_hist >= 0) else 0.0)

    # 6-7. BB_Pos (= bb_pct), BB_Width
    if n >= 20:
        _, _, _, bb_pct = calculate_bollinger_bands(prices)
        bb_arr  = closes[-20:]
        bb_mid  = float(np.mean(bb_arr))
        bb_std  = float(np.std(bb_arr, ddof=0))
        bb_width = (4.0 * bb_std / bb_mid) if bb_mid > 0 else 0.0
    else:
        bb_pct   = 0.5
        bb_width = 0.0

    # 8-10. Stoch_K, Stoch_D, Stoch_Cross (RSI-based)
    stoch_k, stoch_d = calculate_stochastic_rsi(prices)
    stoch_cross = 1.0 if stoch_k > stoch_d else 0.0

    # 11. Williams %R — approximate menggunakan min/max close-14 sebagai proxy H/L
    if n >= 14:
        wnd = closes[-14:]
        hh  = float(np.max(wnd))
        ll  = float(np.min(wnd))
        wr  = ((hh - closes[-1]) / (hh - ll) * -100.0) if (hh - ll) > 0 else -50.0
    else:
        wr = -50.0

    # 12-13. EMA_9_21_Diff, EMA_Above_50
    ema9  = calculate_ema(prices, 9)  if n >= 9  else float(closes[-1])
    ema21 = calculate_ema(prices, 21) if n >= 21 else float(closes[-1])
    ema50 = calculate_ema(prices, 50) if n >= 50 else float(closes[-1])
    ema_9_21_diff = ((ema9 - ema21) / ema21 * 100.0) if ema21 > 0 else 0.0
    ema_above_50  = 1.0 if closes[-1] > ema50 else 0.0

    # 14-15. Vol_Ratio, Vol_Change — tidak ada data volume, gunakan nilai netral
    vol_ratio  = 1.0
    vol_change = 0.0

    # 16-18. Price_Change_1m/3m/5m
    def _pct(a, b): return ((a - b) / b * 100.0) if b > 0 else 0.0
    pc1 = _pct(closes[-1], closes[-2]) if n >= 2  else 0.0
    pc3 = _pct(closes[-1], closes[-4]) if n >= 4  else 0.0
    pc5 = _pct(closes[-1], closes[-6]) if n >= 6  else 0.0

    # 19. ATR_Pct — approximate via abs selisih close (tanpa high/low)
    if n >= 15:
        diffs   = np.abs(np.diff(closes[-15:]))
        atr_pct = (float(np.mean(diffs)) / closes[-1] * 100.0) if closes[-1] > 0 else 0.0
    else:
        atr_pct = 0.0

    # 20. OBV_Slope — tidak ada volume, nilai netral
    obv_slope = 0.0

    return np.array([[
        rsi_now, rsi_change, rsi_slope,
        macd_hist, macd_cross,
        bb_pct, bb_width,
        stoch_k, stoch_d, stoch_cross,
        wr, ema_9_21_diff, ema_above_50,
        vol_ratio, vol_change,
        pc1, pc3, pc5,
        atr_pct, obv_slope,
    ]], dtype=float)


def preload_model() -> None:
    """No-op — per-pair models di-load on-demand saat pertama kali pair diprediksi.

    Dipanggil dari bootstrap.py; dipertahankan agar signature tidak berubah.
    """
    pass


def get_raw_ml_prob(pair: str, price_history: list) -> "float | None":
    """Probabilitas ML murni untuk pair — tanpa blending dengan tech_prob.

    Berguna untuk ML fast-track gate: jika model sangat yakin (>= 0.68),
    bot bisa bypass beberapa gate teknikal yang terlalu konservatif.

    Returns:
        float (0.0-1.0)  — probabilitas class 1 (upward move) dari model
        None             — model tidak tersedia atau fitur kurang
    """
    if not pair or not price_history:
        return None
    model = _load_pair_model(pair.lower())
    if model is None:
        return None
    feat = _build_pair_features(price_history)
    if feat is None:
        return None
    try:
        return float(model.predict_proba(feat)[0][1])
    except Exception:
        return None


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
    Probabilitas eksekusi trade — PURE TECHNICAL (v2.0).

    Perubahan dari v1:
    - Semua pengaruh LLM/AI dihapus sepenuhnya.
    - Base score 100% dari indikator teknikal (RSI, MACD, BB, StochRSI, EMA, WilliamsR, ROC, OB).
    - Model ML (per-pair RandomForest) sebagai refinement jika model tersedia.
    - Bobot blend dinamis berdasarkan tingkat kecocokan tech vs ML.

    Args:
        pair: Jika diberikan, muat per-pair model dari models/{pair}_1m_90d_1m_scalp_classifier.pkl.

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

        #  2. Machine Learning Refinement (per-pair RandomForest) 
        pair_model = _load_pair_model(pair) if pair else None

        if pair_model:
            # Per-pair model: 20 OHLCV-derived features
            feat = _build_pair_features(price_history)
            if feat is not None:
                ml_prob    = float(pair_model.predict_proba(feat)[0][1])
                divergence = abs(tech_prob - ml_prob)
                if divergence < 0.10:
                    final_prob = tech_prob * 0.55 + ml_prob * 0.45
                elif divergence < 0.25:
                    final_prob = tech_prob * 0.65 + ml_prob * 0.35
                else:
                    final_prob = tech_prob * 0.75 + ml_prob * 0.25
            else:
                final_prob = tech_prob
        else:
            # Tidak ada per-pair model — murni teknikal
            final_prob = tech_prob

        return float(round(max(0.0, min(final_prob, 0.99)), 4))

    except Exception as e:
        log_message(f"Prediction Error: {e}")
        return 0.45  # Default netral, bukan 0 (agar tidak memblokir semua sinyal)
