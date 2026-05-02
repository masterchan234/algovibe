import os
import ccxt
from dotenv import load_dotenv

load_dotenv()

exchange = ccxt.indodax({
    'apiKey': os.getenv('INDODAX_API_KEY'),
    'secret': os.getenv('INDODAX_API_SECRET'),
})

bal = exchange.fetch_balance()
print("--- ALL NON-ZERO BALANCES ---")
for coin, amount in bal['total'].items():
    if amount > 0:
        print(f"{coin}: {amount}")
