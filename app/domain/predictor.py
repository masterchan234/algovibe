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
    calculate_ema_slope,
)
from utils.logger import log_message

# ── Global model cache ────────────────────────────────────────────────────────
MODEL_PATH   = "models/scalper_v2.joblib"
_model       = None
_model_mtime: float = 0.0


def _load_model():
    global _model, _model_mtime
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        current_mtime = os.path.getmtime(MODEL_PATH)
        if _model is None or current_mtime > _model_mtime:
            _model = joblib.load(MODEL_PATH)
            _model_mtime = current_mtime
            log_message(
                f"🔬 [Predictor] Scalper V2 Model loaded "
                f"(mtime={datetime.fromtimestamp(current_mtime).strftime('%H:%M:%S')})."
            )
    except Exception as e:
        log_message(f"⚠️ [Predictor] Failed to load model: {e}")
    return _model


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

    scores = []
    weights = []

    # ── 1. RSI (bobot 3.0) ───────────────────────────────────────────────────
    rsi = calculate_rsi(prices)
    if rsi < 25:
        scores.append(0.95)
    elif rsi < 35:
        scores.append(0.80)
    elif rsi < 45:
        scores.append(0.65)
    elif rsi < 55:
        scores.append(0.50)
    elif rsi < 65:
        scores.append(0.38)
    elif rsi < 75:
        scores.append(0.25)
    else:
        scores.append(0.10)
    weights.append(3.0)

    # ── 2. MACD Histogram (bobot 2.5) ────────────────────────────────────────
    if n >= 35:
        _, _, macd_hist = calculate_macd(prices)
        # Normalisasi histogram ke 0-1 berdasarkan tanda dan kekuatan
        if macd_hist > 0:
            macd_score = min(0.95, 0.55 + abs(macd_hist) * 5000)
        else:
            macd_score = max(0.05, 0.45 - abs(macd_hist) * 5000)
        scores.append(macd_score)
        weights.append(2.5)

    # ── 3. Bollinger %B (bobot 2.0) ──────────────────────────────────────────
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

    # ── 4. Stochastic RSI (bobot 2.0) ────────────────────────────────────────
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

    # ── 5. EMA Cross (EMA9 vs EMA21) (bobot 1.5) ─────────────────────────────
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

    # ── 6. EMA Slope/Momentum (bobot 1.0) ────────────────────────────────────
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

    # ── 7. Order Book Pressure (bobot 2.0) ───────────────────────────────────
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


def get_prediction(price_history: list, ticker_data: dict = None, llm_boost: float = 0.0) -> float:
    """
    Probabilitas eksekusi trade — PURE TECHNICAL (v2.0).

    Perubahan dari v1:
    - llm_boost TIDAK lagi mempengaruhi keputusan entry langsung.
      llm_boost disimpan untuk backward-compat tapi dibatasi kontribusinya.
    - Base score sekarang 100% dari indikator teknikal (RSI, MACD, BB, StochRSI, EMA, OB).
    - Model ML (RF) tetap digunakan sebagai refinement dengan bobot lebih kecil.

    Returns:
        -1.0  → Warming Up (data kurang dari minimum)
        0.0-0.99 → Probabilitas bullish saat ini
    """
    from config import AI_MIN_HISTORY_POINTS

    n = len(price_history)
    if n < AI_MIN_HISTORY_POINTS:
        return -1.0  # Warming Up

    try:
        # ── 1. Pure Technical Base Score ─────────────────────────────────────
        tech_prob = _compute_technical_probability(price_history, ticker_data)

        # ── 2. Machine Learning Refinement ───────────────────────────────────
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

            features_df = pd.DataFrame([{
                "ai_prob":          tech_prob,      # Ganti llm_boost dengan tech_prob
                "whale_boost":      1.0,            # Neutral default
                "sector_sentiment": 50.0,           # Neutral default
                "imbalance":        imbalance_val,
                "volatility":       vol,
                "rsi":              rsi,
                "ema_dist":         ema_dist,
                "hour":             datetime.now().hour,
            }])

            ml_prob = model.predict_proba(features_df)[0][1]

            # Blend: Technical 60% + ML 40% (ML sebagai refinement, bukan driver)
            final_prob = (tech_prob * 0.60) + (ml_prob * 0.40)
        else:
            # Tanpa model ML — murni teknikal
            final_prob = tech_prob

        return float(round(max(0.0, min(final_prob, 0.99)), 4))

    except Exception as e:
        log_message(f"Prediction Error: {e}")
        return 0.45  # Default netral, bukan 0 (agar tidak memblokir semua sinyal)
