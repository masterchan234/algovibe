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
        self.weights = db.get_all_weights() or {
            "ai_prob": 1.0, "whale_boost": 1.0, 
            "sector_sentiment": 1.0, "imbalance": 1.0
        }
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
        if decision == "WHALE_BOOST":
            return "Ada paus besar sedang menahan harga di bawah kita. Kesempatan emas!"
        if decision == "SECTOR_BOOST":
            return "Sektor koin ini sedang 'hot'. Momentum sedang berpihak pada kita."
        
        return "Keputusan berdasarkan analisa teknikal dan probabilitas AI yang kuat."

    def get_whale_boost(self, pair: str) -> float:
        """
        Scan Order Book for massive buy walls.
        Returns a score multiplier boost (e.g., 1.5) if a whale is found.
        """
        try:
            from config import WHALE_THRESHOLD_IDR
            # Ambil OB data (MarketWS cache)
            ob = self.market_ws.get_order_book(pair)
            if not ob or not ob.get('bids'): return 1.0
            
            # Cari tembok beli raksasa di 10 level pertama
            # format bid: [price, amount]
            for bid in ob['bids'][:10]:
                val_idr = float(bid[0]) * float(bid[1])
                if val_idr >= WHALE_THRESHOLD_IDR:
                    return 1.5 # Boost skor 50%
                    
            return 1.0
        except Exception:
            return 1.0

    def get_sector_boost(self, pair: str) -> float:
        """
        Check if the pair's sector is pumping.
        """
        try:
            sentiment = self.get_sector_sentiment_val(pair)
            if sentiment > 2.0: # Sektor naik > 2% rata-rata
                return 1.3 # Boost skor 30%
                
            return 1.0
        except Exception:
            return 1.0

    def get_sector_sentiment_val(self, pair: str) -> float:
        """
        Returns the raw sentiment score for a pair's sector.
        """
        try:
            from config import SECTOR_MAP
            sector = SECTOR_MAP.get(pair.lower())
            if not sector: return 0.0
            return db.get_latest_sector_sentiment(sector)
        except Exception:
            return 0.0

    def update_sector_sentiment(self):
        """
        Calculate and persist global sector sentiment.
        Called periodically (e.g., every 5-10 minutes).
        """
        try:
            from config import SECTOR_MAP
            tickers = self.market_ws.get_tickers()
            if not tickers: return
            
            # {SECTOR: [change1, change2, ...]}
            sector_data = {}
            for pair, data in tickers.items():
                s = SECTOR_MAP.get(pair.lower())
                if s:
                    if s not in sector_data: sector_data[s] = []
                    sector_data[s].append(data.get('change_24h', 0))
            
            # Calculate and save average
            for sector, changes in sector_data.items():
                avg_change = sum(changes) / len(changes)
                db.save_sector_sentiment(sector, avg_change)
                
        except Exception as e:
            logger.error(f"[Oracle] Sector Update Error: {e}")

    # ── Self-Learning Engine (Project Evolution) ───────────────────────

    def learn_from_trade(self, pair, pnl_idr):
        """
        Analisa DNA transaksi terakhir dan sesuaikan bobot di database.
        """
        try:
            dna = db.get_latest_trade_dna(pair)
            if not dna: return
            
            # [Anti-Hallucination] Outlier Filter
            # Jika rugi > 15%, kemungkinan besar ada anomali pasar (flash crash/delist)
            # Bot tidak boleh belajar berlebihan dari kejadian 'Bencana Alam'
            if pnl_idr < -0.15: 
                logger.warning(f"[Oracle] Learning SKIP: Trade {pair} was an extreme outlier (Bencana Alam).")
                return
            
            # Learning Rate (Seberapa cepat bot berubah pikiran)
            LR = 0.05 
            
            is_profit = pnl_idr > 0
            
            # Update Bobot berdasarkan fitur yang ada saat beli
            # Jika fitur tersebut hadir saat PROFIT, naikkan bobotnya.
            # Jika fitur tersebut hadir saat LOSS, turunkan bobotnya.
            
            new_weights = {}
            for feature in ["ai_prob", "whale_boost", "sector_sentiment", "imbalance"]:
                current_w = self.weights.get(feature, 1.0)
                
                # Check presence/strength of feature in DNA
                strength = dna.get(feature, 0)
                if feature == "ai_prob": strength -= 0.5 # Center AI prob at 0.5
                if feature == "sector_sentiment": strength /= 5.0 # Normalize sentiment
                
                if is_profit:
                    new_w = current_w + (LR * abs(strength))
                else:
                    new_w = current_w - (LR * abs(strength))
                
                # Clamp weights between 0.5 and 2.0 (Jangan biarkan terlalu ekstrim)
                new_w = max(0.5, min(2.0, new_w))
                new_weights[feature] = new_w
                db.update_dynamic_weight(feature, new_w)
            
            self.weights = new_weights
            logger.info(f"[Oracle] Evolution: Adjusted weights after {pair} trade. New Weights: {new_weights}")
            
        except Exception as e:
            logger.error(f"[Oracle] Learning Error: {e}")

    def apply_learning_to_score(self, base_score, features):
        """
        Gunakan bobot hasil belajar untuk memodifikasi skor akhir.
        """
        multiplier = 1.0
        for feat, val in features.items():
            weight = self.weights.get(feat, 1.0)
            # Jika weight > 1.0, fitur ini dianggap "pembawa hoki"
            # Jika weight < 1.0, fitur ini dianggap "pembawa sial"
            if val > 0: # Hanya jika fitur tersebut ada/aktif
                multiplier *= weight
                
        return base_score * multiplier

    def apply_weight_decay(self):
        """
        [Anti-Hallucination] 
        Secara perlahan mengembalikan bobot ke 1.0 agar bot tidak terjebak di masa lalu.
        """
        try:
            DECAY_RATE = 0.01 # 1% setiap jam
            new_weights = {}
            for feat, weight in self.weights.items():
                if weight > 1.0:
                    new_w = max(1.0, weight - DECAY_RATE)
                elif weight < 1.0:
                    new_w = min(1.0, weight + DECAY_RATE)
                else:
                    new_w = 1.0
                
                new_weights[feat] = new_w
                db.update_dynamic_weight(feat, new_w)
            
            self.weights = new_weights
            logger.info("[Oracle] Weight Decay: Dynamic weights normalized toward 1.0.")
        except Exception as e:
            logger.error(f"[Oracle] Decay Error: {e}")
