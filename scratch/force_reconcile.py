import os
import sys
import ccxt
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from engines.database import db
from engines.exit_manager import ExitManager

def force_reconcile():
    load_dotenv()
    exchange = ccxt.indodax({
        'apiKey': os.getenv('INDODAX_API_KEY'),
        'secret': os.getenv('INDODAX_API_SECRET'),
        'enableRateLimit': True,
    })
    
    print("Running force reconciliation...")
    manager = ExitManager(exchange, dry_run=False)
    manager.reconcile_with_exchange()
    print("Force reconciliation complete.")

if __name__ == "__main__":
    force_reconcile()
