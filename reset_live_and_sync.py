import os
import sqlite3
import ccxt
import time
from dotenv import load_dotenv
from datetime import datetime

# Load configuration
load_dotenv()

from config import DB_PATH_LIVE as DB_PATH, TP_PCT, SL_PCT
API_KEY = os.getenv('INDODAX_API_KEY')
API_SECRET = os.getenv('INDODAX_API_SECRET')

def sync_live_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        return

    print("🚀 Starting LIVE data reset and sync...")
    
    # 1. Connect to Database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 2. Reset Live Data
        print("🧹 Clearing local LIVE history and positions...")
        cursor.execute("DELETE FROM trade_history")
        hist_count = cursor.rowcount
        cursor.execute("DELETE FROM positions")
        pos_count = cursor.rowcount
        conn.commit()
        print(f"✅ Deleted {hist_count} history records and {pos_count} old positions.")

        # 3. Initialize Exchange
        print("🔗 Connecting to Indodax...")
        exchange = ccxt.indodax({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
        })

        # 4. Fetch Balance
        balance = exchange.fetch_balance()
        print("💰 Balance fetched. Scanning for holdings...")

        # 5. Identify Holdings
        for coin, total in balance['total'].items():
            if coin == 'IDR' or total <= 0:
                continue
            
            # Check if it's a significant amount (approx > 1000 IDR)
            # We'll try to get current price to estimate IDR value
            pair = f"{coin.lower()}idr"
            try:
                ticker = exchange.fetch_ticker(f"{coin}/IDR")
                last_price = ticker['last']
                value_idr = total * last_price
                
                if value_idr < 1: # Ignore benar-benar nol
                    continue
                
                print(f"📦 Found holding: {coin} ({total}) | Approx Value: Rp {value_idr:,.0f}")
                
                # 6. Try to find entry price from recent trades
                entry_price = last_price # Default to current price
                try:
                    trades = exchange.fetch_my_trades(f"{coin}/IDR", limit=5)
                    if trades:
                        # Find the last BUY trade
                        buy_trades = [t for t in trades if t['side'] == 'buy']
                        if buy_trades:
                            entry_price = buy_trades[-1]['price']
                            print(f"   🔍 Entry price recovered from history: {entry_price}")
                        else:
                            print(f"   ⚠️ No recent buy trades found. Using current price as entry.")
                except Exception as e:
                    print(f"   ⚠️ Could not fetch trade history for {coin}: {e}")

                # 7. Re-insert into positions table
                timestamp = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO positions (
                        pair, entry_price, amount_crypto, amount_idr, status, 
                        entry_order_id, timestamp, dry_run, target_tp_pct, target_sl_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pair, entry_price, total, value_idr, 'OPEN',
                    'SYNCED-BY-RESET', timestamp, 0, TP_PCT, SL_PCT
                ))
                print(f"   ✅ Position created for {pair}")

            except Exception as e:
                if "symbol not found" not in str(e).lower():
                    print(f"❌ Error processing {coin}: {e}")

        conn.commit()
        print("\n✨ Sync complete! Dashboard should now reflect your real Indodax holdings.")

    except Exception as e:
        print(f"💥 Critical error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    sync_live_data()
