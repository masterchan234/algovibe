import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
import os

# Configuration
DB_PATH = "data/audit/production_analysis.db"
LOG_PATH = "data/audit/logs/production_analysis.log"

def run_analysis():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}. Please run fetch_sync.sh first.")
        return

    print("📊 ALGOVIBE PRODUCTION ANALYSIS REPORT")
    print("="*50)
    
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Overview Stats
    df_history = pd.read_sql_query("SELECT * FROM trade_history", conn)
    if df_history.empty:
        print("📭 No trade history found in the database.")
        return

    total_trades = len(df_history)
    wins = len(df_history[df_history['pnl_idr'] > 0])
    win_rate = (wins / total_trades) * 100
    total_pnl = df_history['pnl_idr'].sum()
    avg_pnl_pct = df_history['pnl_pct'].mean() * 100

    print(f"📈 OVERVIEW:")
    print(f"   Total Trades: {total_trades}")
    print(f"   Win Rate    : {win_rate:.1f}%")
    print(f"   Total PnL   : Rp {total_pnl:,.0f}")
    print(f"   Avg PnL %   : {avg_pnl_pct:.2f}%")
    print("-" * 30)

    # 2. AI Intelligence (DNA) Correlation
    # We join trade_history with trade_dna (if possible) 
    # Or use ai_confidence column in trade_history
    if 'ai_confidence' in df_history.columns:
        print(f"🧠 AI PERFORMANCE AUDIT:")
        df_history['prob_bucket'] = (df_history['ai_confidence'] // 10) * 10
        ai_perf = df_history.groupby('prob_bucket').agg(
            trades=('id', 'count'),
            win_rate=('pnl_idr', lambda x: (x > 0).mean() * 100),
            avg_pnl=('pnl_idr', 'mean')
        ).sort_index(ascending=False)
        
        print(ai_perf.to_string())
        print("-" * 30)

    # 3. Time-Based Analysis
    df_history['created_at'] = pd.to_datetime(df_history['created_at'])
    df_history['hour'] = df_history['created_at'].dt.hour
    
    hourly_perf = df_history.groupby('hour').agg(
        trades=('id', 'count'),
        win_rate=('pnl_idr', lambda x: (x > 0).mean() * 100),
        pnl=('pnl_idr', 'sum')
    )
    
    best_hour = hourly_perf['pnl'].idxmax()
    worst_hour = hourly_perf['pnl'].idxmin()
    
    print(f"🕒 TIME-ZONE INTELLIGENCE:")
    print(f"   Golden Hour : {best_hour:02d}:00 (Highest Profit)")
    print(f"   Danger Hour : {worst_hour:02d}:00 (Lowest Profit)")
    print("-" * 30)

    # 4. Top Performing Pairs
    print(f"💎 TOP ASSET CONTRIBUTORS:")
    asset_perf = df_history.groupby('pair').agg(
        trades=('id', 'count'),
        pnl=('pnl_idr', 'sum'),
        win_rate=('pnl_idr', lambda x: (x > 0).mean() * 100)
    ).sort_values(by='pnl', ascending=False).head(5)
    
    print(asset_perf.to_string())
    print("-" * 30)

    # 5. Holding Time Distribution
    if 'duration_seconds' in df_history.columns:
        avg_hold = df_history['duration_seconds'].mean() / 60
        median_hold = df_history['duration_seconds'].median() / 60
        print(f"⏱️ VELOCITY AUDIT:")
        print(f"   Avg Hold Time   : {avg_hold:.1f} minutes")
        print(f"   Median Hold Time: {median_hold:.1f} minutes")
    
    conn.close()
    print("="*50)
    print("✅ Analysis Complete.")

if __name__ == "__main__":
    run_analysis()
