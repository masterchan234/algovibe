import time
import logging
from datetime import datetime, timedelta
from engines.database import db

logger = logging.getLogger(__name__)

class MarketRegime:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    CHOPPY  = "CHOPPY"
    CRASH   = "CRASH"

class Oracle:
    """
    The 'Consciousness' engine of the bot.
    Responsible for market regime detection, self-health monitoring, and performance learning.
    """
    def __init__(self, market_ws):
        self.market_ws = market_ws
        self.last_regime = MarketRegime.CHOPPY
        self.last_regime_update = datetime.now()
        self.health_status = True
        self.latency_history = []
        
    def is_healthy(self) -> bool:
        """
        Check if the bot is in a state where it can trade safely.
        Checks WebSocket latency and connection status.
        """
        # 1. Check WebSocket Connection
        if not self.market_ws.is_connected():
            logger.warning("[Oracle] Market WS disconnected. Bot is BLIND.")
            return False
            
        # 2. Check Latency
        latency = self.market_ws.get_latency()
        if latency > 2.0: # Lebih dari 2 detik delay
            logger.warning(f"[Oracle] High Latency Detected: {latency:.2f}s. Entering Safe Mode.")
            return False
            
        return True

    def get_market_regime(self) -> str:
        """
        Analyze global market sentiment across all tracked pairs.
        """
        # Update regime every 1 minute
        if datetime.now() - self.last_regime_update < timedelta(minutes=1):
            return self.last_regime
            
        tickers = self.market_ws.get_tickers()
        if not tickers:
            return MarketRegime.CHOPPY
            
        # Analyze top 50 pairs by volume
        all_pairs = list(tickers.values())
        all_pairs.sort(key=lambda x: x.get('vol_idr', 0), reverse=True)
        top_50 = all_pairs[:50]
        
        # Calculate global price change
        up_count = 0
        down_count = 0
        total_change = 0
        
        for t in top_50:
            change = t.get('change_24h', 0)
            total_change += change
            if change > 1.0: up_count += 1
            if change < -1.0: down_count += 1
            
        avg_change = total_change / len(top_50) if top_50 else 0
        
        # Decide Regime
        if avg_change < -5.0 or down_count > 35:
            regime = MarketRegime.CRASH
        elif avg_change < -1.5:
            regime = MarketRegime.BEARISH
        elif avg_change > 1.5 and up_count > 25:
            regime = MarketRegime.BULLISH
        else:
            regime = MarketRegime.CHOPPY
            
        if regime != self.last_regime:
            logger.info(f"[Oracle] Market Regime Shift: {self.last_regime} -> {regime}")
            
        self.last_regime = regime
        self.last_regime_update = datetime.now()
        return regime

    def get_adaptive_config(self) -> dict:
        """
        Return configuration overrides based on the current market regime.
        """
        regime = self.get_market_regime()
        from config import AI_CONFIDENCE_THRESHOLD, MIN_VOLUME_FILTER, MAX_ALLOWED_SPREAD_PCT
        
        overrides = {
            "ai_threshold": AI_CONFIDENCE_THRESHOLD,
            "min_vol": MIN_VOLUME_FILTER,
            "max_spread": MAX_ALLOWED_SPREAD_PCT
        }
        
        if regime == MarketRegime.BULLISH:
            overrides["ai_threshold"] -= 0.05 # Loloskan lebih banyak di pasar Bullish
        elif regime == MarketRegime.BEARISH:
            overrides["ai_threshold"] += 0.10 # Lebih ketat di pasar Bearish
            overrides["min_vol"] *= 2.0       # Hanya ambil koin volume besar
        elif regime == MarketRegime.CRASH:
            overrides["ai_threshold"] = 0.95  # Hampir tidak beli apapun
            overrides["max_spread"] /= 2.0    # Spread harus sangat tipis
            
        # Self-Tuning based on recent performance
        performance_penalty = self.learn_from_history()
        overrides["ai_threshold"] += performance_penalty
        
        return overrides

    def learn_from_history(self) -> float:
        """
        Analyze recent trades to see if the current strategy is losing money.
        Returns a penalty to be added to AI_THRESHOLD.
        """
        try:
            recent_trades = db.get_recent_trades(limit=10)
            if not recent_trades: return 0.0
            
            wins = sum(1 for t in recent_trades if t['pnl_idr'] > 0)
            win_rate = wins / len(recent_trades)
            
            if win_rate < 0.4: # Win rate buruk
                logger.info(f"[Oracle] Self-Tuning: Recent Win Rate is low ({win_rate*100:.0f}%). Tightening filters.")
                return 0.10
            elif win_rate < 0.6:
                return 0.05
                
            return 0.0
        except Exception as e:
            logger.error(f"[Oracle] Learning Error: {e}")
            return 0.0

    def get_sizing_multiplier(self) -> float:
        """
        Adjust capital allocation based on market risk.
        """
        regime = self.get_market_regime()
        if regime == MarketRegime.CRASH: return 0.0 # Stop trading
        if regime == MarketRegime.BEARISH: return 0.5 # Half size
        if regime == MarketRegime.CHOPPY: return 0.8 # Conservative
        return 1.0 # Full speed

    def get_rationale(self, pair: str, decision: str) -> str:
        """
        Generate semantic explanation for a decision.
        """
        regime = self.get_market_regime()
        if decision == "SKIP_LATENCY":
            return "Saya memutuskan berhenti sejenak karena mata saya 'rabun' (latency tinggi). Terlalu berisiko."
        if decision == "SKIP_REGIME":
            return f"Saya tidak mau beli koin apapun sekarang karena pasar global sedang {regime}. Saya menjaga modal kita."
        if decision == "ADAPTIVE_SL":
            return f"Pasar sedang {regime}, saya akan sedikit lebih 'sabar' menahan guncangan harga."
        
        return "Keputusan berdasarkan analisa teknikal dan probabilitas AI yang kuat."
