import os
import time
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from utils.logger import log_message

# Global model cache
MODEL_PATH = "models/scalper_v2.joblib"
_model = None
_model_mtime: float = 0.0  # Timestamp file saat model terakhir di-load


def _load_model():
    global _model, _model_mtime
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        current_mtime = os.path.getmtime(MODEL_PATH)
        if _model is None or current_mtime > _model_mtime:
            # Load pertama kali, atau ada versi lebih baru (atomic swap dari auto_trainer)
            _model = joblib.load(MODEL_PATH)
            _model_mtime = current_mtime
            log_message(f"🧠 [AI Engine] Scalper V2 Model loaded (mtime={datetime.fromtimestamp(current_mtime).strftime('%H:%M:%S')}).")
    except Exception as e:
        log_message(f"⚠️ [AI Engine] Failed to load model: {e}")
    return _model

def get_prediction(price_history: list, ticker_data: dict = None, llm_boost: float = 0.0) -> float:
    """
    Menghitung probabilitas eksekusi menggunakan Heuristic + Machine Learning (Scalper V2).
    """
    from config import AI_MIN_HISTORY_POINTS
    
    n = len(price_history)
    if n < AI_MIN_HISTORY_POINTS:
        return -1.0  # Warming Up

    try:
        # --- 1. HEURISTIC BASE SCORE (Legacy Logic) ---
        prob = 0.45 
        current_price = price_history[-1]
        prob += llm_boost

        imbalance_val = 0.5
        if ticker_data:
            bid_depth = float(ticker_data.get("bid_depth", 0))
            ask_depth = float(ticker_data.get("ask_depth", 0))
            if (bid_depth + ask_depth) > 0:
                imbalance_val = bid_depth / (bid_depth + ask_depth)
                if imbalance_val > 0.60: prob += 0.15
                elif imbalance_val < 0.35: prob -= 0.15

        # EMA & Slope logic
        alpha = 0.2
        ema_val = price_history[0]
        for p in price_history:
            ema_val = (p * alpha) + (ema_val * (1 - alpha))
        
        ema_dist = (current_price - ema_val) / ema_val if ema_val > 0 else 0

        # --- 2. MACHINE LEARNING REFINEMENT (Scalper V2) ---
        model = _load_model()
        if model:
            # Prepare features for RF Model
            # features = ['ai_prob', 'whale_boost', 'sector_sentiment', 'imbalance', 'volatility', 'rsi', 'ema_dist', 'hour']
            
            # [Note] Kita butuh estimasi volatility & RSI untuk model
            volatility = np.std(price_history[-10:]) / np.mean(price_history[-10:]) if len(price_history) >= 10 else 0
            
            # Simple RSI approximation
            deltas = np.diff(price_history[-15:]) if len(price_history) >= 15 else []
            if len(deltas) > 0:
                gains = np.mean(deltas[deltas > 0]) if any(deltas > 0) else 0
                losses = np.mean(-deltas[deltas < 0]) if any(deltas < 0) else 0.0001
                rsi = 100 - (100 / (1 + gains / losses))
            else:
                rsi = 50

            features_df = pd.DataFrame([{
                'ai_prob': prob, # Masukkan heuristic score sebagai input
                'whale_boost': 1.0, # Default if not provided
                'sector_sentiment': 50.0, # Default
                'imbalance': imbalance_val,
                'volatility': volatility,
                'rsi': rsi,
                'ema_dist': ema_dist,
                'hour': datetime.now().hour
            }])

            # Predict probability of being profitable (class 1)
            ml_prob = model.predict_proba(features_df)[0][1]
            
            # Blend Heuristic (40%) + ML (60%)
            final_prob = (prob * 0.4) + (ml_prob * 0.6)
            return round(max(0.0, min(final_prob, 0.99)), 4)

        # Fallback to pure heuristic if model not found
        prob = max(0.0, min(prob, 0.99))
        return prob

    except Exception as e:
        log_message(f"Prediction Error: {e}")
        return 0.0
