"""
services/knn_worker/main.py

Background microservice for isolated, asynchronous K-NN pattern classification.
Fetches active pairs, calculates their DNA patterns, compares against historical
trade DNAs, and caches the SAFE/AVOID flags in Redis for O(1) access by the bot.
"""

import asyncio
import logging
import json
import os
import sys
import time
from datetime import datetime
import numpy as np
import redis

# Add repository root to system path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.database import db
from app.domain.technicals import (
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_ema,
    get_technical_summary,
    _MACD_NORM_FACTOR,
)
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] knn-worker — %(message)s",
)
logger = logging.getLogger("knn-worker")


def compute_current_dna(pair: str, history: list, volatility: float, imbalance: float, tq_prob: float) -> dict:
    """Computes technical DNA features exclusively from price history."""
    if len(history) < 20:
        return {}

    # 1. Standard Indicators
    rsi_val = calculate_rsi(history)
    tech = get_technical_summary(history, None)

    # 2. EMA Distance (from EMA-20)
    prices = np.array(history, dtype=float)
    alpha = 2 / (20 + 1)
    ema_20 = prices[0]
    for price_v in prices[1:]:
        ema_20 = (price_v * alpha) + (ema_20 * (1 - alpha))
    last_price = float(history[-1])
    ema_dist = round((last_price - ema_20) / ema_20, 6)

    # 3. MACD Normal
    macd_hist = tech.get('macd_hist', 0.0)
    macd_norm = abs(macd_hist) / max(1.0, last_price) * _MACD_NORM_FACTOR

    return {
        "tq_prob": tq_prob,
        "ai_prob": tq_prob,
        "bb_pct": tech.get('bb_pct', 0.5),
        "macd_norm": macd_norm,
        "imbalance": imbalance,
        "volatility": volatility,
        "rsi": rsi_val,
        "ema_dist": ema_dist,
    }


def find_winning_pattern(pair: str, current_dna: dict, history_dnas: list) -> dict:
    """Matches the current DNA against historical trade DNAs using Weighted Manhattan distance."""
    default_resp = {"verdict": "BUY", "target_tp": 0.04, "target_sl": -0.02, "confidence": 75.0}

    if not history_dnas or len(history_dnas) < 3:
        return default_resp

    current_rsi = current_dna.get('rsi', 50.0)
    current_ema_dist = current_dna.get('ema_dist', 0.0)

    distances = []
    for h in history_dnas:
        try:
            d_vol = (current_dna.get('volatility', 0.0) - h.get('volatility', 0.0)) / 2.0
            d_imb = (current_dna.get('imbalance', 1.0) - h.get('imbalance', 1.0)) / 1.5
            d_ai  = (current_dna.get('ai_prob', 0.0) - h.get('ai_prob', 0.0)) / 20.0

            _h_rsi = h['rsi'] if h.get('rsi') is not None else 50.0
            _h_ema = h['ema_dist'] if h.get('ema_dist') is not None else 0.0
            _c_rsi = current_dna['rsi'] if current_dna.get('rsi') is not None else 50.0
            _c_ema = current_dna['ema_dist'] if current_dna.get('ema_dist') is not None else 0.0
            
            d_rsi = (_c_rsi - _h_rsi) / 15.0
            d_ema = (_c_ema - _h_ema) / 0.1

            # Weighted Manhattan
            dist = abs(d_vol) * 1.2 + abs(d_imb) * 1.0 + abs(d_ai) * 1.0 + abs(d_rsi) * 0.6 + abs(d_ema) * 2.5

            # Recency Bias (10 hours discount)
            try:
                created_at = h.get('entry_timestamp')
                if created_at:
                    if isinstance(created_at, str):
                        dt_dna = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    else:
                        dt_dna = created_at
                    
                    if (datetime.now(dt_dna.tzinfo) - dt_dna).total_seconds() < 36000:
                        dist *= 0.75
            except Exception:
                pass

            distances.append((dist, h))
        except Exception:
            continue

    if not distances:
        return default_resp

    distances.sort(key=lambda x: x[0])
    top_matches = [item[1] for item in distances[:5]]

    win_count = sum(1 for t in top_matches if t.get('pnl_idr', 0.0) > 0.0)
    win_rate_knn = win_count / len(top_matches) if top_matches else 0.0

    is_strong_setup = current_rsi < 35 or current_ema_dist > 0.10
    base_confidence = win_rate_knn * 100.0

    n_valid = sum(1 for t in top_matches if t.get('rsi') is not None or t.get('ema_dist') is not None)
    has_quality_features = n_valid >= 3

    if not has_quality_features:
        confidence = 75.0
        reason = f"K-NN Feature-Blind (WinRate={base_confidence:.0f}%)"
    elif is_strong_setup:
        confidence = min(max(base_confidence, 55.0), 70.0)
        reason = f"Deep-Oversold Mode (RSI {current_rsi:.0f}, k-NN WinRate: {win_rate_knn*100:.0f}%)"
    else:
        confidence = base_confidence
        reason = f"Historical Weighted (k-NN WinRate: {win_rate_knn*100:.0f}%)"

    # Default verdict is BUY; confidence floor determines entry downstream
    return {
        "verdict": "BUY",
        "confidence": confidence,
        "reason": reason,
        "target_tp": 0.04,
        "target_sl": -0.02,
    }


class KNNWorkerService:
    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis.from_url(redis_url)
        self.running = False

    async def start(self):
        self.running = True
        logger.info("[OK] Starting isolated K-NN Worker loop...")

        while self.running:
            try:
                # 1. Fetch all closed DNAs from DB (Pattern Library)
                history_dnas = db.get_all_closed_dnas(limit=2000)
                
                # 2. Get active price histories from DB
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT pair, history_json, volatility, score FROM ticker_history")
                    rows = cursor.fetchall()

                logger.info(f"Loaded {len(history_dnas)} historical DNAs. Processing {len(rows)} ticker pairs...")

                for row in rows:
                    pair = row['pair'].lower()
                    history_raw = row['history_json']
                    volatility = float(row['volatility'] or 0.0)
                    score = float(row['score'] or 0.0)

                    if not history_raw:
                        continue

                    try:
                        history = json.loads(history_raw)
                    except Exception:
                        continue

                    if len(history) < 20:
                        continue

                    # Retrieve dynamic tq_prob and imbalance from Redis (written by tick loop)
                    # If not present, default to score proxy or base parameters
                    tq_prob_raw = self.redis.get(f"tq_prob:{pair}")
                    tq_prob = float(tq_prob_raw) if tq_prob_raw else (score / 100.0)
                    
                    imbalance_raw = self.redis.get(f"imbalance:{pair}")
                    imbalance = float(imbalance_raw) if imbalance_raw else 1.0

                    # Compute current pair DNA
                    current_dna = compute_current_dna(pair, history, volatility, imbalance, tq_prob)
                    if not current_dna:
                        continue

                    # Match K-NN winning pattern
                    knn_decision = find_winning_pattern(pair, current_dna, history_dnas)

                    # Save decision verdict back to Redis (O(1) retrieval)
                    redis_key = f"knn_verdict:{pair}"
                    self.redis.set(redis_key, json.dumps(knn_decision), ex=120)  # 2 minute TTL

                logger.info("Successfully updated all active K-NN verdicts in Redis.")

            except Exception as e:
                logger.error(f"Error in K-NN Worker Loop: {e}")

            # Run once every 30 seconds (fits standard 1m kline updates)
            await asyncio.sleep(30.0)

    def stop(self):
        self.running = False
        logger.info("Stopped isolated K-NN Worker.")


def main():
    service = KNNWorkerService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        loop.close()


if __name__ == "__main__":
    main()
