import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import random
import sys
import os

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from engines.database import db

# Configuration
PAIRS = [
    'BTC/IDR', 'ETH/IDR', 'SOL/IDR', 'PEPE/IDR', 'DOGE/IDR', 
    'XRP/IDR', 'ADA/IDR', 'AVAX/IDR', 'DOT/IDR', 'LINK/IDR',
    'SHIB/IDR', 'BONK/IDR', 'FLOKI/IDR', 'BOME/IDR', 'WIF/IDR',
    'NEAR/IDR', 'ARB/IDR', 'OP/IDR', 'SUI/IDR', 'TIA/IDR',
    'RENDER/IDR', 'FET/IDR', 'JUP/IDR', 'PENDLE/IDR', 'ENA/IDR'
]

TIMEFRAME = '15m'
SINCE_DAYS = 60
CAPITAL_PER_TRADE = 500000 # 500k IDR

# Strategy Constants (matching main.py/config.py)
TP_PCT = 0.02
SL_PCT = -0.012

class AlgovibeBacktester:
    def __init__(self):
        self.exchange = ccxt.indodax()
        
    def fetch_data(self, pair):
        print(f"Fetching {pair} historical data...")
        try:
            since = self.exchange.parse8601((datetime.now() - timedelta(days=SINCE_DAYS)).isoformat())
            ohlcv = self.exchange.fetch_ohlcv(pair, TIMEFRAME, since=since)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"Error fetching {pair}: {e}")
            return None

    def run_simulation(self, pair, df):
        if df is None or len(df) < 30:
            return
            
        print(f"Simulating {pair}...")
        trades_count = 0
        
        # Simple Breakout Logic for simulation
        df['avg_20'] = df['close'].rolling(window=20).mean()
        df['max_prev_5'] = df['close'].shift(1).rolling(window=5).max()
        
        i = 20
        while i < len(df) - 20:
            row = df.iloc[i]
            
            # Entry condition: Price > max of previous 5 and above 20-avg
            # We add a bit of 'momentum' requirement
            if row['close'] > row['max_prev_5'] and row['close'] > row['avg_20'] * 1.002:
                entry_price = row['close']
                entry_time = row['timestamp']
                
                # Synthetic DNA generation (Tuned: Derived from price action, not random)
                volatility = ((row['high'] - row['low']) / row['open']) * 100
                avg_vol = df['volume'].iloc[i-10:i].mean()
                
                # Whale Boost: Loncatan volume murni
                whale_boost = 1.5 if row['volume'] > avg_vol * 1.8 else 1.0
                
                # Imbalance: Tiruan dari intensitas candle (Body vs Wick)
                body_size = abs(row['close'] - row['open'])
                wick_size = (row['high'] - row['low']) - body_size
                imbalance_proxy = 1.0 + (body_size / (wick_size + 0.0001))
                imbalance_proxy = min(3.5, max(1.1, imbalance_proxy))
                
                # AI Prob: Simulasi kepercayaan berdasarkan 'Sharpness' of breakout
                breakout_strength = (row['close'] / row['avg_20']) - 1.0
                ai_prob_sim = 65 + (breakout_strength * 1000)
                ai_prob_sim = min(95, max(66, ai_prob_sim))
                
                dna = {
                    'timestamp': entry_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'ai_prob': round(ai_prob_sim, 2),
                    'whale_boost': whale_boost,
                    'sector_sentiment': round(random.uniform(0.8, 1.2), 2),
                    'imbalance': round(imbalance_proxy, 2),
                    'volatility': round(volatility, 2)
                }
                
                # Simulate Outcome
                outcome = None
                exit_price = 0
                pnl_pct = 0
                duration_sec = 0
                
                # Look forward up to 48 hours (192 candles of 15m)
                for j in range(i + 1, min(i + 192, len(df))):
                    future_row = df.iloc[j]
                    
                    # 1. Check Stop Loss first (conservative)
                    if future_row['low'] <= entry_price * (1 + SL_PCT):
                        outcome = "LOSS"
                        exit_price = entry_price * (1 + SL_PCT)
                        pnl_pct = SL_PCT * 100
                        duration_sec = (future_row['timestamp'] - entry_time).total_seconds()
                        i = j 
                        break
                    
                    # 2. Check Take Profit
                    if future_row['high'] >= entry_price * (1 + TP_PCT):
                        # Add some randomness for dynamic profit (simulating trailing/llm decision)
                        extra_profit = random.uniform(0, 0.03) if random.random() > 0.7 else 0
                        actual_tp = TP_PCT + extra_profit
                        
                        outcome = "PROFIT"
                        exit_price = entry_price * (1 + actual_tp)
                        pnl_pct = actual_tp * 100
                        duration_sec = (future_row['timestamp'] - entry_time).total_seconds()
                        i = j
                        break
                
                if outcome:
                    pnl_idr = CAPITAL_PER_TRADE * (pnl_pct / 100)
                    db.insert_backtest_data(
                        pair=pair,
                        dna=dna,
                        pnl_idr=pnl_idr,
                        pnl_pct=pnl_pct,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        amount_idr=CAPITAL_PER_TRADE,
                        duration_seconds=int(duration_sec)
                    )
                    trades_count += 1
            
            i += 1
        
        print(f"Finished {pair}: {trades_count} trades seeded.")

if __name__ == "__main__":
    print("--- Algovibe Backtester & DNA Seeder ---")
    backtester = AlgovibeBacktester()
    for pair in PAIRS:
        data = backtester.fetch_data(pair)
        if data is not None:
            backtester.run_simulation(pair, data)
    print("\n✅ Database Seeding Complete!")
