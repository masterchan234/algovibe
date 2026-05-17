"""
exchange_factory — Central factory for exchange clients and the middleware bridge.

Two layers are available:

1. ccxt client (legacy, direct)
   ``create_indodax_exchange()`` — returns a ccxt.indodax instance.
   Used by ScalperBot and ExitManager for order execution today.

2. Middleware bridge (exchange-agnostic, recommended for new code)
   ``create_trading_bridge()``  — returns a TradingBridge wired to the
   exchange configured in the DB / EXCHANGE env var.
   ``create_exchange_provider()`` — returns the raw BaseExchangeAdapter for
   the given exchange_id without the bridge layer (e.g. for the dashboard).

The bridge and ccxt client can coexist; they share the same credentials.
"""

import os

import ccxt


def create_indodax_exchange():
    """Create configured ccxt client (returns ccxt.binance or ccxt.indodax depending on EXCHANGE env var)."""
    eid = os.getenv("EXCHANGE", "binance").lower()
    if eid == "binance":
        return ccxt.binance(
            {
                'apiKey': os.getenv('BINANCE_API_KEY') or os.getenv('INDODAX_API_KEY'),
                'secret': os.getenv('BINANCE_API_SECRET') or os.getenv('INDODAX_API_SECRET'),
                'enableRateLimit': True,
                'options': {
                    'adjustForTimeDifference': True,
                }
            }
        )
    else:
        return ccxt.indodax(
            {
                'apiKey': os.getenv('INDODAX_API_KEY'),
                'secret': os.getenv('INDODAX_API_SECRET'),
                'enableRateLimit': True,
            }
        )


def create_trading_bridge(
    exchange_id: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
):
    """Instantiate and return a TradingBridge for the given exchange.

    Parameters
    ----------
    exchange_id : str | None
        Exchange identifier (e.g. ``'indodax'``).  When *None* the bridge
        resolves the active exchange from the DB / ``EXCHANGE`` env var.
    api_key, api_secret : str | None
        Credentials.  Defaults to ``INDODAX_API_KEY`` / ``INDODAX_API_SECRET``
        environment variables.

    Returns
    -------
    TradingBridge
    """
    from app.infrastructure.trading_bridge import TradingBridge
    return TradingBridge(exchange_id=exchange_id, api_key=api_key, api_secret=api_secret)


def create_exchange_provider(
    exchange_id: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
):
    """Return a bare BaseExchangeAdapter for *exchange_id*.

    Useful when callers need low-level adapter access (e.g. dashboard
    connectivity tests) without the full bridge overhead.

    Parameters
    ----------
    exchange_id : str | None
        Exchange identifier.  Defaults to ``'binance'``.
    api_key, api_secret : str | None
        Credentials; fall back to environment variables when not provided.

    Returns
    -------
    BaseExchangeAdapter
    """
    eid = (exchange_id or os.getenv("EXCHANGE", "binance")).lower()

    if eid == "binance":
        key = api_key or os.getenv("BINANCE_API_KEY", "") or os.getenv("INDODAX_API_KEY", "")
        secret = api_secret or os.getenv("BINANCE_API_SECRET", "") or os.getenv("INDODAX_API_SECRET", "")
        from app.infrastructure.providers.binance_adapter import BinanceAdapter
        return BinanceAdapter(key, secret)
    elif eid == "indodax":
        key = api_key or os.getenv("INDODAX_API_KEY", "")
        secret = api_secret or os.getenv("INDODAX_API_SECRET", "")
        from app.infrastructure.providers.indodax_adapter import IndodaxAdapter
        return IndodaxAdapter(key, secret)

    raise ValueError(f"Unsupported exchange_id: '{eid}'. Registered: ['binance', 'indodax']")
