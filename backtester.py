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

# Strategy Constants (imported from config.py)
from config import TP_PCT, SL_PCT, CAPITAL_PER_TRADE_PCT, INITIAL_CAPITAL
CAPITAL_PER_TRADE = INITIAL_CAPITAL * CAPITAL_PER_TRADE_PCT

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
        
        # Tuned Indicators for higher Win Rate
        df['avg_20'] = df['close'].rolling(window=20).mean()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        
        # RSI 14 calculation
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        df['max_prev_5'] = df['close'].shift(1).rolling(window=5).max()
        
        # Fee Indodax (Taker/Maker + Tax) ~ 0.51% round trip
        FEE_ROUND_TRIP = 0.0051 
        
        i = 20
        while i < len(df) - 20:
            row = df.iloc[i]
            prev_row = df.iloc[i-1]
            
            # Entry condition (Mean Reversion / Buy The Dip): 
            # 1. Trend Makro: EMA-20 > EMA-50 (Sedang Uptrend)
            # 2. Oversold: RSI < 45 (Mendingin sementara)
            # 3. Pullback: Low price menyentuh EMA-20
            # 4. BTC Filter: BTC must be in UPTREND
            btc_up = row.get('btc_up', True)
            
            if (row['ema_20'] > row['ema_50'] and 
                row['rsi'] < 45 and 
                row['low'] <= row['ema_20'] and
                btc_up):
                
                # Simulasi Entri: Limit Order di EMA-20
                entry_price = row['ema_20']
                entry_time = row['timestamp']
                
                # Synthetic DNA generation
                volatility = ((row['high'] - row['low']) / row['open']) * 100
                avg_vol = df['volume'].iloc[i-10:i].mean()
                
                # Whale Boost: Volume di atas rata-rata saat retest (indikasi dipertahankan)
                whale_boost = 1.5 if row['volume'] > avg_vol * 1.5 else 1.0
                
                # Imbalance proxy
                body_size = abs(row['close'] - row['open'])
                wick_size = (row['high'] - row['low']) - body_size
                imbalance_proxy = 1.0 + (body_size / (wick_size + 0.0001))
                imbalance_proxy = min(3.5, max(1.1, imbalance_proxy))
                
                # AI Prob: Simulasi kepercayaan berdasarkan 'Oversoldness'
                ai_prob_sim = 95 - row['rsi']  # Semakin oversold (misal 30), semakin pede (65)
                ai_prob_sim = min(95, max(60, ai_prob_sim + 20)) # Boost base prob
                
                # Tidak perlu loop ke depan lagi, asumsikan limit order di EMA-20 terisi karena low <= ema_20
                
                # Contextual Features for learning
                rsi_val = row['rsi'] if not np.isnan(row['rsi']) else 50.0
                ema_dist = ((entry_price - row['ema_20']) / row['ema_20']) * 100
                
                dna = {
                    'timestamp': entry_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'ai_prob': round(ai_prob_sim, 2),
                    'whale_boost': whale_boost,
                    'sector_sentiment': round(random.uniform(0.8, 1.2), 2),
                    'imbalance': round(imbalance_proxy, 2),
                    'volatility': round(volatility, 2),
                    'rsi': round(rsi_val, 2),
                    'ema_dist': round(ema_dist, 4)
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
                    
                    # 2. Check Take Profit (Swing Target 9%)
                    if future_row['high'] >= entry_price * (1 + TP_PCT):
                        outcome = "PROFIT"
                        exit_price = entry_price * (1 + TP_PCT)
                        pnl_pct = TP_PCT * 100
                        duration_sec = (future_row['timestamp'] - entry_time).total_seconds()
                        i = j
                        break
                
                if outcome:
                    # Deduct Transaction Fees
                    pnl_pct_net = pnl_pct - (FEE_ROUND_TRIP * 100)
                    pnl_idr_net = CAPITAL_PER_TRADE * (pnl_pct_net / 100)
                    
                    db.insert_backtest_data(
                        pair=pair,
                        dna=dna,
                        pnl_idr=pnl_idr_net,
                        pnl_pct=pnl_pct_net,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        amount_idr=CAPITAL_PER_TRADE,
                        duration_seconds=int(duration_sec)
                    )
                    trades_count += 1
            
            i += 1
        
        print(f"Finished {pair}: {trades_count} trades seeded.")

if __name__ == "__main__":
    print("--- Algovibe Backtester & DNA Seeder (BTC Filter + Taker Mode) ---")
    backtester = AlgovibeBacktester()
    
    # Fetch BTC for Market Filter
    print("Fetching BTC/IDR for market regime analysis...")
    btc_df = backtester.fetch_data("BTC/IDR")
    if btc_df is not None:
        btc_df['btc_ema'] = btc_df['close'].ewm(span=20, adjust=False).mean()
        btc_map = btc_df.set_index('timestamp')['close'] > btc_df.set_index('timestamp')['btc_ema']
    else:
        btc_map = None

    for pair in PAIRS:
        data = backtester.fetch_data(pair)
        if data is not None:
            # Map BTC trend to pair data
            if btc_map is not None:
                data['btc_up'] = data['timestamp'].map(btc_map).fillna(False)
            else:
                data['btc_up'] = True # Fallback
                
            backtester.run_simulation(pair, data)
    print("\n✅ High-Quality Seeding with BTC Filter Complete!")
