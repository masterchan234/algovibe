from services.backtester.db_backtest import get_connection
import pandas as pd

def show_results():
    conn = get_connection()
    try:
        query = """
        SELECT pair, total_trades, winning_trades, losing_trades, win_rate, total_pnl_idr, total_pnl_pct, max_drawdown_pct 
        FROM backtest_results 
        ORDER BY total_pnl_idr DESC;
        """
        df = pd.read_sql(query, conn)
        if df.empty:
            print("No results found in backtest_results table.")
            return

        print("=== OVERALL PERFORMANCE ===")
        total_trades = df['total_trades'].sum()
        total_wins = df['winning_trades'].sum()
        total_losses = df['losing_trades'].sum()
        winrate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = df['total_pnl_idr'].sum()
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
        
        print(f"Total Trades: {total_trades}")
        print(f"Total Wins:   {total_wins}")
        print(f"Total Losses: {total_losses}")
        print(f"Winrate:      {winrate:.2f}%")
        print(f"Total PnL:    {total_pnl:,.2f} IDR")
        print(f"Avg PnL/Trade:{avg_pnl:,.2f} IDR\n")
        
        print("=== TOP 5 PAIRS (BY PnL) ===")
        print(df.head(5).to_string(index=False))
        print("\n=== WORST 5 PAIRS (BY PnL) ===")
        print(df.tail(5).to_string(index=False))
        
    finally:
        conn.close()

if __name__ == "__main__":
    show_results()
