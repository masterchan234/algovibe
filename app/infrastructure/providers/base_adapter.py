"""
BaseExchangeAdapter — Abstract interface for all exchange integrations.

Every exchange adapter that plugs into the Algovibe middleware bridge MUST
inherit from this class and implement all abstract methods.  The core
execution engine only ever calls these standard methods, never exchange-
specific APIs directly.

Unified response contracts

get_balance()
    {"asset": str, "free": float, "used": float, "total": float}

get_ticker(symbol)
    {
        "symbol": str,
        "last": float,
        "bid": float,
        "ask": float,
        "high": float,
        "low": float,
        "volume": float,
        "timestamp": int,  # Unix ms
    }

place_order(symbol, side, type, volume, price)
    {
        "order_id": str,
        "symbol": str,
        "side": str,        # "buy" | "sell"
        "type": str,        # "limit" | "market"
        "price": float,
        "volume": float,
        "status": str,      # "open" | "closed" | "cancelled"
        "timestamp": int,   # Unix ms
        "raw": dict,        # raw exchange response preserved for debugging
    }

get_open_orders()
    List[dict]  — each element follows the place_order response shape

cancel_order(order_id)
    {"success": bool, "order_id": str}
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseExchangeAdapter(ABC):
    """Abstract base class — blueprint every exchange adapter must satisfy."""

    #  Identity 

    @property
    @abstractmethod
    def exchange_id(self) -> str:
        """Short identifier for this exchange, e.g. 'indodax' or 'binance'."""

    @property
    @abstractmethod
    def fee_structure(self) -> dict:
        """Return a dict describing the exchange fee tiers.

        Minimum required keys:
            {
                "maker_buy":  float,   # fraction, e.g. 0.001111
                "taker_buy":  float,
                "maker_sell": float,
                "taker_sell": float,
                "round_trip_taker": float,  # buy_taker + sell_taker combined
                "slippage_buffer": float,
            }
        """

    #  Account 

    @abstractmethod
    def get_balance(self) -> dict:
        """Return the primary fiat/quote balance in the unified format.

        Returns
        -------
        dict
            {"asset": str, "free": float, "used": float, "total": float}
        """

    @abstractmethod
    def get_all_balances(self) -> list[dict]:
        """Return balances for every asset held on the exchange.

        Returns
        -------
        list[dict]
            Each element: {"asset": str, "free": float, "used": float, "total": float}
        """

    #  Market data 

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        """Return unified ticker for *symbol* (e.g. 'BTCIDR' or 'BTC/IDR').

        Returns
        -------
        dict
            {symbol, last, bid, ask, high, low, volume, timestamp}
        """

    def fetch_summaries(self) -> dict:
        """Fetch bulk public market/ticker summaries from the exchange.
        
        Optional method; returns empty dict if not supported by the exchange.

        Returns
        -------
        dict
            {"tickers": dict, "prices_24h": dict}
        """
        return {}

    def fetch_ticker_all(self) -> dict:
        """Fetch all tickers in a single call. Optional; returns {} if unsupported."""
        return {}

    def fetch_trades(self, symbol: str, limit: int = 50) -> list:
        """Fetch recent public trades. Optional; returns [] if unsupported."""
        return []

    def fetch_trade_flow(self, symbol: str, limit: int = 50) -> dict:
        """Summarise recent trade flow. Optional; returns neutral result if unsupported."""
        return {"buy_vol": 0.0, "sell_vol": 0.0, "flow_ratio": 0.5, "net_pressure": "NEUTRAL"}

    def fetch_ohlc(self, symbol: str, tf: str = "1", from_ts=None, to_ts=None, limit: int = 100) -> list:
        """Fetch OHLC candles. Optional; returns [] if unsupported."""
        return []

    def fetch_ohlc_closes(self, symbol: str, tf: str = "1", limit: int = 100) -> list:
        """Fetch close prices from OHLC. Optional; returns [] if unsupported."""
        return []

    def fetch_pairs(self) -> list:
        """Fetch all trading pairs metadata. Optional; returns [] if unsupported."""
        return []

    def fetch_idr_pairs(self) -> list:
        """Return list of IDR pair IDs. Optional; returns [] if unsupported."""
        return []

    def fetch_market_movers(self, top_n: int = 20) -> dict:
        """Return top gainers/losers/trending/7d momentum. Optional; returns {} if unsupported."""
        return {}

    #  Order management 

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        volume: float,
        price: float | None = None,
    ) -> dict:
        """Place a new order and return the unified order response.

        Parameters
        ----------
        symbol:     Trading pair, e.g. 'btcidr' or 'BTC/IDR'.
        side:       'buy' or 'sell'.
        order_type: 'limit' or 'market'.
        volume:     Quantity in base-asset units.
        price:      Limit price (required for limit orders, ignored for market).

        Returns
        -------
        dict
            {order_id, symbol, side, type, price, volume, status, timestamp, raw}
        """

    @abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Return all open orders, optionally filtered by *symbol*.

        Returns
        -------
        list[dict]
            Each element follows the place_order response shape.
        """

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict:
        """Cancel an open order by *order_id*.

        Returns
        -------
        dict
            {"success": bool, "order_id": str}
        """

    #  Connectivity 

    @abstractmethod
    def test_connection(self) -> bool:
        """Perform a lightweight connectivity / credential check.

        Returns
        -------
        bool
            True if the exchange is reachable and credentials are valid.
        """
