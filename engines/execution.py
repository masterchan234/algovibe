import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

class ExecutionEngine:
    def __init__(self):
        self.exchange = ccxt.indodax({
            'apiKey': os.getenv('INDODAX_API_KEY'),
            'secret': os.getenv('INDODAX_API_SECRET'),
            'enableRateLimit': True,
        })

    def get_available_balance(self):
        balance = self.exchange.fetch_balance()
        return balance['free']['IDR']

    def place_maker_order(self, symbol, side, amount, price):
        """Mengeksekusi order dengan tag post_only agar selalu jadi Maker"""
        try:
            params = {'post_only': True} # Menjamin tidak akan memakan Taker Fee
            if side == 'buy':
                # Offset 0.05% di bawah harga pasar sesuai spek
                entry_price = price * 0.9995 
                order = self.exchange.create_limit_buy_order(symbol, amount, entry_price, params)
            else:
                # Offset 0.05% di atas harga pasar untuk jual
                exit_price = price * 1.0005
                order = self.exchange.create_limit_sell_order(symbol, amount, exit_price, params)
            
            return order
        except Exception as e:
            print(f"Execution Error: {e}")
            return None

    def cancel_order(self, order_id, symbol):
        return self.exchange.cancel_order(order_id, symbol)