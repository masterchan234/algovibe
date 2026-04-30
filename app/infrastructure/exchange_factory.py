import os

import ccxt


def create_indodax_exchange():
    """Create configured ccxt Indodax client."""
    return ccxt.indodax(
        {
            'apiKey': os.getenv('INDODAX_API_KEY'),
            'secret': os.getenv('INDODAX_API_SECRET'),
            'enableRateLimit': True,
        }
    )
