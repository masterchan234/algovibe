import os
import sys
import ccxt
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from app.infrastructure.database import db
from app.application.exit_manager import ExitManager

def force_reconcile():
    load_dotenv(dotenv_path='.env')
    exchange = ccxt.indodax({
        'apiKey': os.getenv('INDODAX_API_KEY'),
        'secret': os.getenv('INDODAX_API_SECRET'),
        'enableRateLimit': True,
    })
    
    print("Running force reconciliation...")
    manager = ExitManager(exchange)
    manager.reconcile_with_exchange()
    print("Force reconciliation complete.")

if __name__ == "__main__":
    force_reconcile()
