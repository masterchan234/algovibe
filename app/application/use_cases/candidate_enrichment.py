"""
candidate_enrichment.py

Per-pair technical enrichment: update history, compute momentum changes,
apply AI/whale/sector boosts, derive signal, and persist to DB.
Always completes (never skips) — gate filtering is done downstream.
"""

from app.infrastructure.database import db
from app.domain.predictor import get_prediction
from app.domain.technicals import get_technical_summary, derive_trade_signal
from utils.logger import log_message


def enrich_pair(bot, p, tickers):
    """
    Enriches a candidate pair with technical and AI data, then saves to DB.

    Returns a dict with all computed enrichment values. Never returns None —
    caller is responsible for gate filtering after enrichment.
    """
    pair = p.get('pair', 'UNKNOWN')
    trend, breakout, history = bot.update_history_and_trend(pair, p['last'])
    imbalance_ratio = p.get('imbalance', 1.0)

    ai_boost = bot.oracle.get_sentiment_boost(pair)
    tq_prob = get_prediction(history, tickers.get(pair), ai_boost)

    ch_5m = ch_15m = ch_1h = 0.0
    if len(history) >= 2:
        curr = float(p['last'])
        p_5 = float(history[-5]) if len(history) >= 5 else float(history[0])
        p_15 = float(history[-15]) if len(history) >= 15 else float(history[0])
        p_60 = float(history[-60]) if len(history) >= 60 else float(history[0])
        ch_5m = ((curr - p_5) / p_5) * 100 if p_5 > 0 else 0.0
        ch_15m = ((curr - p_15) / p_15) * 100 if p_15 > 0 else 0.0
        ch_1h = ((curr - p_60) / p_60) * 100 if p_60 > 0 else 0.0

    whale_boost = bot.oracle.get_whale_boost(pair)
    sector_boost = bot.oracle.get_sector_boost(pair)
    boost_adj = ((whale_boost - 1.0) * 0.1) + ((sector_boost - 1.0) * 0.1)
    final_prob = max(0.0, min(0.99, tq_prob + boost_adj))

    tech = get_technical_summary(history)
    tech_sig = derive_trade_signal(tech)
    signal_verdict = tech_sig['verdict']

    db.save_ticker_history(
        pair,
        history,
        prediction_prob=final_prob,
        trend=trend,
        signal=signal_verdict,
        ch_5m=ch_5m,
        ch_15m=ch_15m,
        ch_1h=ch_1h,
        volatility=p.get('volatility', 0),
        score=p.get('score', 0),
    )

    return {
        "trend": trend,
        "breakout": breakout,
        "history": history,
        "imbalance_ratio": imbalance_ratio,
        "tq_prob": tq_prob,
        "whale_boost": whale_boost,
        "sector_boost": sector_boost,
        "final_prob": final_prob,
        "tech": tech,
        "tech_sig": tech_sig,
        "signal_verdict": signal_verdict,
    }
