"""
BinanceAdapter — Binance Spot exchange implementation of BaseExchangeAdapter.

Wired completely through CCXT, handling:
  - Symbol normalization (btcusdt <-> BTC/USDT)
  - Unified REST calls for orders, tickers, and balances
  - Mapping decimal values cleanly to USD/USDT
"""

import logging
import time
from typing import Any

import ccxt
import requests

from .base_adapter import BaseExchangeAdapter

logger = logging.getLogger(__name__)


class BinanceAdapter(BaseExchangeAdapter):
    """Binance Spot exchange adapter.

    Parameters
    ----------
    api_key : str
        Binance REST API key.
    api_secret : str
        Binance REST API secret.
    """

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret

        # Initialize CCXT Binance client
        self._ccxt = ccxt.binance({
            'apiKey': self._api_key,
            'secret': self._api_secret,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True,
            }
        })

    #  Helpers 

    def _normalise_symbol(self, symbol: str) -> str:
        """Convert 'BTC/USDT' or 'BTC_USDT' or 'btcusdt' to lowercase 'btcusdt'."""
        return symbol.replace("/", "").replace("_", "").replace("-", "").lower()

    def _to_ccxt_symbol(self, symbol: str) -> str:
        """Convert 'btcusdt' or 'BTCUSDT' to CCXT format 'BTC/USDT'."""
        s = symbol.upper().replace("_", "").replace("-", "")
        if "/" in s:
            return s
        if s.endswith("USDT"):
            return f"{s[:-4]}/USDT"
        return s

    #  Identity 

    @property
    def exchange_id(self) -> str:
        return "binance"

    @property
    def fee_structure(self) -> dict:
        return {
            "maker_buy":        0.0010,   # 0.1% Spot Maker
            "taker_buy":        0.0010,   # 0.1% Spot Taker
            "maker_sell":       0.0010,   # 0.1% Spot Maker
            "taker_sell":       0.0010,   # 0.1% Spot Taker
            "round_trip_taker": 0.0020,   # buy_taker + sell_taker combined
            "slippage_buffer":  0.0005,   # 0.05% slippage buffer
        }

    #  Account 

    def get_balance(self) -> dict:
        """Return the primary quote balance (USDT) in the unified format."""
        try:
            balance = self._ccxt.fetch_balance()
            usdt = balance.get('USDT', {})
            return {
                "asset": "USDT",
                "free": float(usdt.get('free', 0.0)),
                "used": float(usdt.get('used', 0.0)),
                "total": float(usdt.get('total', 0.0)),
            }
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to fetch balance: {e}")
            return {"asset": "USDT", "free": 0.0, "used": 0.0, "total": 0.0}

    def get_all_balances(self) -> list[dict]:
        """Return balances for every asset held on the exchange."""
        try:
            balance = self._ccxt.fetch_balance()
            res = []
            for asset, data in balance.items():
                if isinstance(data, dict) and 'free' in data:
                    free = float(data.get('free', 0.0))
                    used = float(data.get('used', 0.0))
                    total = float(data.get('total', 0.0))
                    if total > 0.0:
                        res.append({
                            "asset": asset,
                            "free": free,
                            "used": used,
                            "total": total,
                        })
            return res
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to fetch all balances: {e}")
            return []

    #  Market data 

    def get_ticker(self, symbol: str) -> dict:
        """Return unified ticker for symbol."""
        try:
            ccxt_sym = self._to_ccxt_symbol(symbol)
            t = self._ccxt.fetch_ticker(ccxt_sym)
            return {
                "symbol": self._normalise_symbol(symbol).upper(),
                "last": float(t.get('last', 0.0) or t.get('close', 0.0)),
                "bid": float(t.get('bid', 0.0) or 0.0),
                "ask": float(t.get('ask', 0.0) or 0.0),
                "high": float(t.get('high', 0.0) or 0.0),
                "low": float(t.get('low', 0.0) or 0.0),
                "volume": float(t.get('quoteVolume', 0.0) or t.get('baseVolume', 0.0) or 0.0),
                "timestamp": int(t.get('timestamp', time.time() * 1000)),
            }
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to fetch ticker for {symbol}: {e}")
            return {
                "symbol": symbol.upper(), "last": 0.0, "bid": 0.0, "ask": 0.0,
                "high": 0.0, "low": 0.0, "volume": 0.0, "timestamp": int(time.time() * 1000)
            }

    def fetch_ticker_all(self) -> dict:
        """Fetch all USDT tickers in a single public API call."""
        try:
            tickers = self._ccxt.fetch_tickers()
            result = {}
            for ccxt_sym, t in tickers.items():
                if not ccxt_sym.endswith('/USDT'):
                    continue
                pair_norm = self._normalise_symbol(ccxt_sym)
                result[pair_norm] = {
                    "symbol": pair_norm.upper(),
                    "last": float(t.get('last', 0.0) or t.get('close', 0.0) or 0.0),
                    "bid": float(t.get('bid', 0.0) or 0.0),
                    "ask": float(t.get('ask', 0.0) or 0.0),
                    "high": float(t.get('high', 0.0) or 0.0),
                    "low": float(t.get('low', 0.0) or 0.0),
                    "vol_idr": float(t.get('quoteVolume', 0.0) or 0.0),  # concept-map USDT quote to vol_idr
                    "timestamp": int(t.get('timestamp', time.time() * 1000)),
                }
            return result
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to fetch all tickers: {e}")
            return {}

    def fetch_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Fetch recent public trades for symbol."""
        try:
            ccxt_sym = self._to_ccxt_symbol(symbol)
            trades = self._ccxt.fetch_trades(ccxt_sym, limit=limit)
            result = []
            for t in trades:
                result.append({
                    "tid": str(t.get('id', '')),
                    "side": str(t.get('side', '')).lower(),
                    "price": float(t.get('price', 0.0)),
                    "amount": float(t.get('amount', 0.0)),
                    "timestamp": int(t.get('timestamp', time.time() * 1000)),
                })
            return result
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to fetch trades for {symbol}: {e}")
            return []

    def fetch_trade_flow(self, symbol: str, limit: int = 50) -> dict:
        """Summarise recent trade flow."""
        trades = self.fetch_trades(symbol, limit=limit)
        if not trades:
            return {"buy_vol": 0.0, "sell_vol": 0.0, "flow_ratio": 0.5, "net_pressure": "NEUTRAL"}

        buy_vol = sum(t["price"] * t["amount"] for t in trades if t["side"] == "buy")
        sell_vol = sum(t["price"] * t["amount"] for t in trades if t["side"] == "sell")
        total = buy_vol + sell_vol
        flow_ratio = buy_vol / total if total > 0 else 0.5

        pressure = "NEUTRAL"
        if flow_ratio >= 0.55:
            pressure = "BUY"
        elif flow_ratio <= 0.45:
            pressure = "SELL"

        return {
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "flow_ratio": flow_ratio,
            "net_pressure": pressure,
        }

    def fetch_ohlc(self, symbol: str, tf: str = "1", from_ts=None, to_ts=None, limit: int = 100) -> list:
        """Fetch OHLC candles for symbol in a unified dictionary format."""
        try:
            ccxt_sym = self._to_ccxt_symbol(symbol)
            tf_map = {
                "1": "1m", "5": "5m", "15": "15m", "30": "30m",
                "60": "1h", "240": "4h", "1440": "1d"
            }
            ccxt_tf = tf_map.get(str(tf), "1m")
            since_ms = int(from_ts * 1000) if from_ts else None
            ohlcv = self._ccxt.fetch_ohlcv(ccxt_sym, timeframe=ccxt_tf, since=since_ms, limit=limit)
            result = []
            for c in ohlcv:
                result.append({
                    "time": int(c[0] // 1000),  # to seconds to match Indodax format
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5])
                })
            return result
        except Exception as e:
            logger.error(f"[BinanceAdapter] fetch_ohlc failed for {symbol}: {e}")
            return []

    def fetch_ohlc_closes(self, symbol: str, tf: str = "1", limit: int = 100) -> list:
        """Fetch close prices from OHLC candles."""
        ohlc = self.fetch_ohlc(symbol, tf, limit=limit)
        return [float(x["close"]) for x in ohlc]

    def fetch_idr_pairs(self) -> list:
        """Return conceptual active USDT pairs mapping."""
        from config import SECTOR_MAP
        return list(SECTOR_MAP.keys())

    # Stablecoin bases yang diexclude dari market movers scan
    _STABLECOIN_BASES = frozenset({
        'USDC', 'FDUSD', 'TUSD', 'USDP', 'BUSD', 'DAI', 'USDB', 'USD1', 'XUSD',
        'U', 'RLUSD', 'XAUT', 'PAXG', 'EUR', 'GBP', 'TRY', 'BRL', 'AUD',
    })

    def fetch_market_movers(self, top_n: int = 20) -> dict:
        """Return top gainers, losers, dan trending USDT pairs dari Binance public API.

        Menggunakan REST langsung (bukan CCXT) untuk kecepatan.
        Filter: vol >= $5M, exclude stablecoins.
        """
        _EMPTY = {"gainers": [], "losers": [], "trending": [], "momentum_7d": [],
                  "gainers_brief": "", "losers_brief": "", "trending_brief": ""}
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=8,
            )
            if resp.status_code != 200:
                logger.warning("[BinanceAdapter] fetch_market_movers HTTP %d", resp.status_code)
                return _EMPTY

            raw = resp.json()
            candidates = []
            for t in raw:
                sym = str(t.get('symbol', ''))
                if not sym.endswith('USDT'):
                    continue
                base = sym[:-4]
                if base in self._STABLECOIN_BASES:
                    continue
                vol = float(t.get('quoteVolume', 0) or 0)
                if vol < 5_000_000:   # min $5 juta notional/24h
                    continue
                change = float(t.get('priceChangePercent', 0) or 0)
                last  = float(t.get('lastPrice', 0) or t.get('prevClosePrice', 0) or 0)
                if last <= 0:
                    continue
                candidates.append({
                    "pair":       sym.lower(),
                    "last":       last,
                    "change_24h": change,
                    "change_24h_pct": change,
                    "vol_idr":    vol,   # USDT volume, mapped ke vol_idr slot
                })

            gainers  = sorted(candidates, key=lambda x: x["change_24h"], reverse=True)[:top_n]
            losers   = sorted(candidates, key=lambda x: x["change_24h"])[:top_n]
            trending = sorted(candidates, key=lambda x: x["vol_idr"],    reverse=True)[:top_n]

            gainers_brief  = " ".join(f"{x['pair'].replace('usdt','')}({x['change_24h']:+.1f}%)" for x in gainers[:8])
            losers_brief   = " ".join(f"{x['pair'].replace('usdt','')}({x['change_24h']:+.1f}%)" for x in losers[:8])
            trending_brief = " ".join(f"{x['pair'].replace('usdt','')}({x['vol_idr']/1e6:.0f}M)"  for x in trending[:8])

            return {
                "gainers":       gainers,
                "losers":        losers,
                "trending":      trending,
                "momentum_7d":   trending,  # alias — brain_inference menggunakan ini juga
                "gainers_brief": gainers_brief,
                "losers_brief":  losers_brief,
                "trending_brief": trending_brief,
            }
        except Exception as e:
            logger.error("[BinanceAdapter] fetch_market_movers failed: %s", e)
            return _EMPTY

    #  Order management 

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        volume: float,
        price: float | None = None,
    ) -> dict:
        """Place a Spot order and return unified format."""
        try:
            ccxt_sym = self._to_ccxt_symbol(symbol)
            res = self._ccxt.create_order(
                symbol=ccxt_sym,
                type=order_type.lower(),
                side=side.lower(),
                amount=volume,
                price=price
            )
            return {
                "order_id": str(res.get('id', '')),
                "symbol": self._normalise_symbol(symbol).upper(),
                "side": side.lower(),
                "type": order_type.lower(),
                "price": float(res.get('price', 0.0) or price or 0.0),
                "volume": float(res.get('amount', 0.0) or volume),
                "status": "closed" if res.get('status') == 'closed' else "open",
                "timestamp": int(res.get('timestamp', time.time() * 1000)),
                "raw": res
            }
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to place order on {symbol}: {e}")
            return {
                "order_id": "", "symbol": symbol.upper(), "side": side, "type": order_type,
                "price": price or 0.0, "volume": volume, "status": "cancelled",
                "timestamp": int(time.time() * 1000), "raw": {"error": str(e)}
            }

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Fetch all open orders from Binance Spot."""
        try:
            ccxt_sym = self._to_ccxt_symbol(symbol) if symbol else None
            orders = self._ccxt.fetch_open_orders(ccxt_sym)
            res = []
            for o in orders:
                res.append({
                    "order_id": str(o.get('id', '')),
                    "symbol": self._normalise_symbol(o.get('symbol', '')).upper(),
                    "side": str(o.get('side', '')).lower(),
                    "type": str(o.get('type', '')).lower(),
                    "price": float(o.get('price', 0.0)),
                    "volume": float(o.get('amount', 0.0)),
                    "status": "open",
                    "timestamp": int(o.get('timestamp', time.time() * 1000)),
                    "raw": o
                })
            return res
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to fetch open orders: {e}")
            return []

    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict:
        """Cancel an open order by ID."""
        try:
            ccxt_sym = self._to_ccxt_symbol(symbol) if symbol else None
            self._ccxt.cancel_order(order_id, ccxt_sym)
            return {"success": True, "order_id": order_id}
        except Exception as e:
            logger.error(f"[BinanceAdapter] Failed to cancel order {order_id}: {e}")
            return {"success": False, "order_id": order_id}

    #  Connectivity 

    def test_connection(self) -> bool:
        """輕量 Credentials & Connectivity Check."""
        try:
            self._ccxt.fetch_balance()
            return True
        except Exception as e:
            logger.error(f"[BinanceAdapter] Credentials / connection test failed: {e}")
            return False
