import os
import sys
import ccxt
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from engines.database import db
from engines.exit_manager import ExitManager

def test_exit():
    load_dotenv()
    exchange = ccxt.indodax({
        'apiKey': os.getenv('INDODAX_API_KEY'),
        'secret': os.getenv('INDODAX_API_SECRET'),
        'enableRateLimit': True,
    })
    
    pair = 'ethidr'
    pos = db.get_position(pair)
    if not pos:
        print(f"No position found for {pair}")
        return

    manager = ExitManager(exchange, dry_run=False)
    
    print(f"Attempting manual exit for {pair}...")
    current_price = 41131000.0 # From my previous check
    try:
        # We use a reason that triggers Maker exit
        order_id = manager.execute_exit(pair, pos['amount_crypto'], current_price, "TAKE_PROFIT", 
                                       entry_price=pos['entry_price'], amount_idr=pos['amount_idr'], position_data=pos)
        print(f"Order ID: {order_id}")
    except Exception as e:
        print(f"Caught top-level exception: {e}")

if __name__ == "__main__":
    test_exit()
