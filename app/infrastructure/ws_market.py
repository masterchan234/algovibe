"""
Market Data WebSocket Client — Indodax & Binance Spot
===================================================
Menggantikan REST polling dengan WebSocket real-time stream.
Mendukung dynamic exchange switching antara Indodax dan Binance.
"""

import json
import time
import logging
import threading
import websocket   # websocket-client
import zmq
import os
from app.infrastructure.volume_tracker import RealtimeVolumeTracker

logger = logging.getLogger(__name__)

# Config switching
def _get_ws_url():
    eid = os.getenv("EXCHANGE", "binance").lower()
    if eid == "binance":
        return "wss://stream.binance.com:9443/stream"
    return "wss://ws3.indodax.com/ws/"

WS_MARKET_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJleHAiOjE5NDY2MTg0MTV9"
    ".UR1lBM6Eqh0yWz-PVirw1uPCxe60FdchR8eNVdsskeo"
)

CHANNEL_SUMMARY = "market:summary-24h"
CHANNEL_OB_PFX  = "market:order-book-"   # Indodax
CHANNEL_TRADE_PFX = "market:trade-activity-" # Indodax

OB_TOP_PAIRS     = 9999
OB_MIN_VOL_IDR   = 20000.0  # USDT or IDR scale depending on active exchange

# Pair yang SELALU di-subscribe order book terlepas dari volume threshold
def _get_high_priority_pairs():
    eid = os.getenv("EXCHANGE", "binance").lower()
    if eid == "binance":
        return frozenset({"btcusdt"})
    return frozenset({"btcidr"})

# Indeks kolom pada payload market:summary-24h (Indodax)
IDX_PAIR     = 0
IDX_EPOCH    = 1
IDX_LAST     = 2
IDX_LOW      = 3
IDX_HIGH     = 4
IDX_OPEN     = 5
IDX_VOL_IDR  = 6
IDX_VOL_COIN = 7


class MarketDataWS:
    """
    WebSocket client untuk Market Data (Indodax / Binance Spot).
    """

    def __init__(self, on_tick=None, on_trade=None):
        self._tickers: dict = {}          # {pair: {last, buy, sell, high, low, vol_idr, ...}}
        self._lock    = threading.Lock()
        self._ws      = None
        self._thread  = None
        self._running = False
        self._req_id  = 0
        self._last_msg_time = 0
        self._connected = threading.Event()  
        self._ob_subscribed: set = set()     
        self._trade_subscribed: set = set()  
        self._trades: dict = {}              # {pair: [[ts, price, vol, side, id], ...]}
        self._ws_ref = None                  
        self._ob_done = False                
        self._pending_subs: dict = {}        
        
        # Callbacks
        self.on_tick = on_tick
        self.on_trade = on_trade
        self.volume_tracker = RealtimeVolumeTracker()

        # SaaS Hub Config
        self._use_hub = os.getenv("USE_MARKET_HUB", "false").lower() == "true"
        self._hub_host = os.getenv("MARKET_HUB_HOST", "127.0.0.1")
        self._hub_port = int(os.getenv("MARKET_HUB_PORT", "5555"))
        
        # Database Persistence Config
        self.write_to_db = False # Only set to True by primary ingress (market-pipe)

    #  Public API 

    def start(self):
        """Mulai koneksi WS di background thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True, name="MarketWS")
        self._thread.start()
        logger.info("[MarketWS] Background thread started.")

    def stop(self):
        """Hentikan koneksi WS."""
        self._running = False
        if self._ws:
            self._ws.close()
        logger.info("[MarketWS] Stopped.")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """Blok hingga data pertama diterima."""
        return self._connected.wait(timeout=timeout)

    def subscribe_open_positions(self):
        """Subscribe to orderbook for all open live positions."""
        eid = os.getenv("EXCHANGE", "binance").lower()
        try:
            from app.infrastructure.database import db
            live_pos = db.get_open_positions()
            if not live_pos:
                return
            
            for pair in live_pos.keys():
                if pair not in self._ob_subscribed and self._ws_ref:
                    try:
                        req_id = self._next_id()
                        if eid == "binance":
                            stream_name = f"{pair.lower()}@depth10"
                            sub_msg = {"method": "SUBSCRIBE", "params": [stream_name], "id": req_id}
                        else:
                            channel = f"{CHANNEL_OB_PFX}{pair}"
                            sub_msg = {"method": 1, "params": {"channel": channel}, "id": req_id}
                        
                        self._ws_ref.send(json.dumps(sub_msg))
                        self._ob_subscribed.add(pair)
                        logger.info(f"[MarketWS] Subscribed OB for open position: {pair}")
                        time.sleep(0.05)
                    except Exception as e:
                        logger.warning(f"[MarketWS] Failed to subscribe OB for {pair}: {e}")
        except Exception as e:
            logger.warning(f"[MarketWS] Failed to subscribe_open_positions: {e}")

    def get_tickers(self) -> dict:
        """Kembalikan snapshot ticker saat ini."""
        with self._lock:
            return dict(self._tickers)

    def get_trades(self, pair: str) -> list:
        """Ambil data aktivitas market terbaru (trades)."""
        p = pair.lower().replace('/', '')
        with self._lock:
            return list(self._trades.get(p, []))

    def get_market_depth(self, pair: str) -> dict:
        """Ambil Top 10 Bids & Asks langsung dari cache memori."""
        p = pair.lower().replace('/', '')
        with self._lock:
            t = self._tickers.get(p, {})
            return {
                "bids": t.get("bids_list", []),
                "asks": t.get("asks_list", [])
            }

    def is_connected(self):
        return self._connected.is_set() and (time.time() - self._last_msg_time < 30)

    def get_latency(self):
        if self._last_msg_time == 0: return 0
        return time.time() - self._last_msg_time

    #  Internal 

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _run(self):
        if self._use_hub:
            self._run_subscriber()
        else:
            self._run_direct()

    def _run_subscriber(self):
        logger.info(f"[MarketWS] Starting in SUBSCRIBER mode (Hub: {self._hub_host}:{self._hub_port})")
        context = zmq.Context()
        subscriber = context.socket(zmq.SUB)
        subscriber.connect(f"tcp://{self._hub_host}:{self._hub_port}")
        subscriber.setsockopt_string(zmq.SUBSCRIBE, "tickers")
        subscriber.setsockopt_string(zmq.SUBSCRIBE, "heartbeat")
        
        subscriber.RCVTIMEO = 2000 

        while self._running:
            try:
                topic_data = subscriber.recv_string()
                topic, payload_json = topic_data.split(' ', 1)
                payload = json.loads(payload_json)
                
                if topic == "tickers":
                    with self._lock:
                        self._tickers.update(payload["data"])
                        if not self._connected.is_set():
                            self._connected.set()
                        self._last_msg_time = time.time()
                
                elif topic == "heartbeat":
                    self._last_msg_time = time.time()
                    
            except zmq.Again:
                continue
            except Exception as e:
                logger.error(f"[MarketWS] Subscriber error: {e}")
                time.sleep(2)

        subscriber.close()
        context.term()

    def _run_direct(self):
        url = _get_ws_url()
        logger.info(f"[MarketWS] Connecting directly to {url}")
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                self._ws.run_forever(suppress_origin=True, ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"[MarketWS] run_forever exception: {e}")

            if self._running:
                self._ob_done = False         
                logger.warning("[MarketWS] Disconnected, reconnecting in 5s…")
                time.sleep(5)

    def _on_open(self, ws):
        self._ws_ref = ws
        eid = os.getenv("EXCHANGE", "binance").lower()
        logger.info(f"[MarketWS] Connected to {eid.upper()}. Initiating subscriptions...")
        
        if eid == "binance":
            # Subscribe to the Binance global mini-ticker array (much more lightweight than standard ticker)
            req_id = self._next_id()
            sub_msg = {
                "method": "SUBSCRIBE",
                "params": ["!miniTicker@arr"],
                "id": req_id
            }
            ws.send(json.dumps(sub_msg))
            
            # Auto-subscribe to any previously registered trades/orderbooks on connect/reconnect!
            with self._lock:
                if self._trade_subscribed:
                    pairs_list = list(self._trade_subscribed)
                    chunk_size = 20
                    for i in range(0, len(pairs_list), chunk_size):
                        chunk = pairs_list[i:i+chunk_size]
                        streams = [f"{p.lower()}@trade" for p in chunk]
                        sub_msg_trades = {"method": "SUBSCRIBE", "params": streams, "id": self._next_id()}
                        ws.send(json.dumps(sub_msg_trades))
                        logger.warning(f"[MarketWS] Subscribed to trade streams on connect/reconnect: {chunk}")
                        time.sleep(0.5)
                        
                if self._ob_subscribed:
                    pairs_list = list(self._ob_subscribed)
                    chunk_size = 20
                    for i in range(0, len(pairs_list), chunk_size):
                        chunk = pairs_list[i:i+chunk_size]
                        streams = [f"{p.lower()}@depth10" for p in chunk]
                        sub_msg_ob = {"method": "SUBSCRIBE", "params": streams, "id": self._next_id()}
                        ws.send(json.dumps(sub_msg_ob))
                        logger.warning(f"[MarketWS] Subscribed to depth streams on connect/reconnect: {chunk}")
                        time.sleep(0.5)
                        
            self._connected.set()
        else:
            # Indodax Auth & Summary
            auth_msg = {
                "params": {"token": WS_MARKET_TOKEN},
                "id": self._next_id(),
            }
            ws.send(json.dumps(auth_msg))

    def _safe_float(self, val, default=0.0):
        try:
            if val is None: return default
            return float(val)
        except (ValueError, TypeError):
            return default

    def _on_message(self, ws, raw: str):
        self._last_msg_time = time.time()
        eid = os.getenv("EXCHANGE", "binance").lower()
        logger.info(f"[MarketWS] Received raw: {raw[:200]}")
        try:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                return

            if eid == "binance":
                self._on_message_binance(ws, msg)
            else:
                self._on_message_indodax(ws, msg)
        except Exception as e:
            logger.error(f"[MarketWS] Error in _on_message: {e}", exc_info=True)

    def _on_message_binance(self, ws, msg: dict):
        if "result" in msg:
            # Subscription ACK
            return
        
        stream = msg.get("stream", "").lower()
        data = msg.get("data", {})
        
        if stream in ("!ticker@arr", "!miniticker@arr"):
            self._process_binance_summary(data)
        elif stream.endswith("@depth10"):
            symbol = stream.split("@")[0]
            self._process_binance_orderbook(symbol, data)
        elif stream.endswith("@trade"):
            symbol = stream.split("@")[0]
            self._process_binance_trades(symbol, data)

    def _on_message_indodax(self, ws, msg: dict):
        #  Auth response 
        if "result" in msg and "client" in msg.get("result", {}):
            logger.info(f"[MarketWS] Auth OK — client: {msg['result']['client']}")
            req_id = self._next_id()
            sub_msg = {
                "method": 1,
                "params": {"channel": CHANNEL_SUMMARY},
                "id": req_id,
            }
            self._pending_subs[req_id] = CHANNEL_SUMMARY
            threading.Timer(1.0, lambda: ws.send(json.dumps(sub_msg))).start()
            return

        #  Subscribe ACK 
        if "id" in msg and "result" in msg and "recoverable" in msg.get("result", {}):
            req_id = msg["id"]
            channel_name = self._pending_subs.pop(req_id, f"unknown (id={req_id})")
            logger.info(f"[MarketWS] Subscribed to {channel_name}")
            return

        #  Streaming data 
        result = msg.get("result", {})
        channel = result.get("channel", "")
        data_wrap = result.get("data", {})

        if channel == CHANNEL_SUMMARY:
            rows = data_wrap.get("data", [])
            self._process_summary(rows)

        elif channel.startswith(CHANNEL_OB_PFX):
            pair = channel[len(CHANNEL_OB_PFX):]
            ob_data = data_wrap.get("data", {})
            self._process_orderbook(pair, ob_data)

        elif channel.startswith(CHANNEL_TRADE_PFX):
            pair = channel[len(CHANNEL_TRADE_PFX):]
            trade_data = data_wrap.get("data", [])
            self._process_trades(pair, trade_data)

    #  Binance Stream Processors 

    def _process_binance_summary(self, data: list):
        if not data: return
        updated = {}
        for t in data:
            sym = t.get("s", "").lower()
            if not sym.endswith("usdt"):
                continue
            
            last = float(t.get("c", 0.0))
            low = float(t.get("l", 0.0))
            high = float(t.get("h", 0.0))
            open_p = float(t.get("o", 0.0))
            vol_usdt = float(t.get("q", 0.0))  # quote volume in USDT (concept vol_idr)
            vol_coin = t.get("v", "0")
            
            if open_p > 0:
                change_24h = ((last - open_p) / open_p) * 100.0
            else:
                change_24h = float(t.get("P", 0.0))
                
            existing = self._tickers.get(sym, {})
            
            updated[sym] = {
                "pair":      sym,
                "last":      last,
                "change_24h": change_24h,
                "buy":       existing.get("buy", last) if existing.get("buy", last) != existing.get("last", last) else last,
                "sell":      existing.get("sell", last) if existing.get("sell", last) != existing.get("last", last) else last,
                "high":      high,
                "low":       low,
                "open":      open_p,
                "vol_idr":   vol_usdt,
                "vol_coin":  vol_coin,
                "bid_depth": existing.get("bid_depth", 0),
                "ask_depth": existing.get("ask_depth", 0),
            }

        if self.on_tick:
            for pair, d in updated.items():
                self.on_tick(pair, d)

        need_ob_subscribe = False
        with self._lock:
            self._tickers.update(updated)
            if not self._connected.is_set():
                self._connected.set()
                logger.info(f"[MarketWS] First Binance data received — {len(self._tickers)} symbols.")
            
            if len(self._ob_subscribed) < OB_TOP_PAIRS:
                need_ob_subscribe = True

        # Dynamic Watch Pair
        try:
            from app.infrastructure.database import db
            watch_pair = db.get_state('current_watch_pair')
            if watch_pair:
                if watch_pair not in self._ob_subscribed:
                    self._subscribe_specific_ob(watch_pair)
                if watch_pair not in self._trade_subscribed:
                    self.subscribe_market_trades(watch_pair)
        except Exception: pass

        if need_ob_subscribe and self._ws_ref:
            with self._lock:
                snapshot = dict(self._tickers)
            self._subscribe_top_orderbooks(snapshot)

    def _process_binance_orderbook(self, symbol: str, data: dict):
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids and not asks:
            return

        bid_depth = 0.0
        ask_depth = 0.0
        best_bid = 0.0
        best_ask = 0.0

        bids_list = []
        for i, b in enumerate(bids[:10]):
            p = float(b[0])
            v = float(b[1]) * p
            if i == 0:
                best_bid = p
            bid_depth += v
            bids_list.append({"price": p, "vol": v})

        asks_list = []
        for i, a in enumerate(asks[:10]):
            p = float(a[0])
            v = float(a[1]) * p
            if i == 0:
                best_ask = p
            ask_depth += v
            asks_list.append({"price": p, "vol": v})

        with self._lock:
            if symbol not in self._tickers:
                self._tickers[symbol] = {
                    "last": best_bid, "buy": best_bid, "sell": best_ask,
                    "high": 0, "low": 0, "open": 0,
                    "vol_idr": 0, "vol_coin": "0",
                    "bid_depth": 0, "ask_depth": 0,
                    "bids_list": [], "asks_list": []
                }
            t = self._tickers[symbol]
            if best_bid > 0: t["buy"] = best_bid
            if best_ask > 0: t["sell"] = best_ask
            t["bid_depth"] = bid_depth
            t["ask_depth"] = ask_depth
            t["bids_list"] = bids_list
            t["asks_list"] = asks_list

            if self.write_to_db:
                try:
                    from app.infrastructure.database import db
                    db.save_market_depth(symbol, bids_list, asks_list)
                except Exception as _e:
                    logger.debug(f"[MarketWS] DB Save error: {_e}")

    def _process_binance_trades(self, symbol: str, data: dict):
        price = float(data.get("p", 0.0))
        amount = float(data.get("q", 0.0))
        vol_usdt = price * amount
        side = "sell" if data.get("m") else "buy"
        t_seq = str(data.get("t", ""))
        t_time = int(data.get("T", time.time() * 1000))

        trade_item = [
            symbol,
            t_time,
            t_seq,
            side,
            price,
            vol_usdt,
            amount
        ]

        self.volume_tracker.add_trade(symbol, vol_usdt, bool(data.get("m", False)))

        with self._lock:
            if symbol not in self._trades:
                self._trades[symbol] = []
            self._trades[symbol].insert(0, trade_item)
            self._trades[symbol] = self._trades[symbol][:50]

            if self.on_trade:
                self.on_trade(symbol, trade_item)

    #  Indodax Stream Processors 

    def _process_summary(self, rows: list):
        if not rows: return
        updated = {}
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            pair     = str(row[IDX_PAIR])
            last     = self._safe_float(row[IDX_LAST])
            low      = self._safe_float(row[IDX_LOW])
            high     = self._safe_float(row[IDX_HIGH])
            open_p   = self._safe_float(row[IDX_OPEN])
            vol_idr  = self._safe_float(row[IDX_VOL_IDR])
            vol_coin = str(row[IDX_VOL_COIN] or "0")

            change_24h = ((last - open_p) / open_p * 100) if open_p > 0 else 0
            
            existing = self._tickers.get(pair, {})
            updated[pair] = {
                "pair":      pair,
                "last":      last,
                "change_24h": change_24h,
                "buy":       existing.get("buy", last) if existing.get("buy", last) != existing.get("last", last) else last,
                "sell":      existing.get("sell", last) if existing.get("sell", last) != existing.get("last", last) else last,
                "high":      high,
                "low":       low,
                "open":      open_p,
                "vol_idr":   vol_idr,
                "vol_coin":  vol_coin,
                "bid_depth": existing.get("bid_depth", 0),
                "ask_depth": existing.get("ask_depth", 0),
            }
        
        if self.on_tick:
            for pair, data in updated.items():
                self.on_tick(pair, data)

        need_ob_subscribe = False
        with self._lock:
            self._tickers.update(updated)
            if not self._connected.is_set():
                self._connected.set()
                logger.info(f"[MarketWS] First Indodax summary received — {len(self._tickers)} pairs.")
            
            if len(self._ob_subscribed) < OB_TOP_PAIRS:
                need_ob_subscribe = True
                
        try:
            from app.infrastructure.database import db
            watch_pair = db.get_state('current_watch_pair')
            if watch_pair:
                if watch_pair not in self._ob_subscribed:
                    self._subscribe_specific_ob(watch_pair)
                if watch_pair not in self._trade_subscribed:
                    self.subscribe_market_trades(watch_pair)
        except Exception: pass
                
        if need_ob_subscribe and self._ws_ref:
            with self._lock:
                snapshot = dict(self._tickers)
            self._subscribe_top_orderbooks(snapshot)

    def _process_orderbook(self, pair: str, data: dict):
        bids = data.get("bid", [])
        asks = data.get("ask", [])
        if not bids and not asks:
            return

        try:
            bid_depth = 0.0
            ask_depth = 0.0
            best_bid  = 0.0
            best_ask  = 0.0

            for i, b in enumerate(bids[:10]):
                price = self._safe_float(b.get("price"))
                vol   = self._safe_float(b.get("idr_volume"))
                if i == 0:
                    best_bid = price
                bid_depth += vol

            for i, a in enumerate(asks[:10]):
                price = self._safe_float(a.get("price"))
                vol   = self._safe_float(a.get("idr_volume"))
                if i == 0:
                    best_ask = price
                ask_depth += vol
        except Exception:
            return

        with self._lock:
            if pair not in self._tickers:
                self._tickers[pair] = {
                    "last": best_bid, "buy": best_bid, "sell": best_ask,
                    "high": 0, "low": 0, "open": 0,
                    "vol_idr": 0, "vol_coin": "0",
                    "bid_depth": 0, "ask_depth": 0,
                    "bids_list": [], "asks_list": []
                }
            t = self._tickers[pair]
            if best_bid > 0: t["buy"] = best_bid
            if best_ask > 0: t["sell"] = best_ask
            t["bid_depth"] = bid_depth
            t["ask_depth"] = ask_depth
            t["bids_list"] = [{"price": self._safe_float(b.get("price")), "vol": self._safe_float(b.get("idr_volume"))} for b in bids[:10]]
            t["asks_list"] = [{"price": self._safe_float(a.get("price")), "vol": self._safe_float(a.get("idr_volume"))} for a in asks[:10]]
            
            if self.write_to_db:
                try:
                    from app.infrastructure.database import db
                    db.save_market_depth(pair, t["bids_list"], t["asks_list"])
                except Exception as _e:
                    logger.debug(f"[MarketWS] DB Save error: {_e}")

    def _process_trades(self, pair: str, data: list):
        if not data: return
        with self._lock:
            if pair not in self._trades: self._trades[pair] = []
            for t in data:
                self._trades[pair].insert(0, t)
                try:
                    is_buyer_maker = (t[3] == "sell")
                    vol_val = float(t[5])
                    self.volume_tracker.add_trade(pair, vol_val, is_buyer_maker)
                except Exception:
                    pass
            self._trades[pair] = self._trades[pair][:50]
            
            if self.on_trade:
                for t in data:
                    self.on_trade(pair, t)

    #  Subscribing & Fetching helper methods 

    def _subscribe_specific_ob(self, pair: str):
        if not self._ws_ref: return
        eid = os.getenv("EXCHANGE", "binance").lower()
        try:
            req_id = self._next_id()
            if eid == "binance":
                stream = f"{pair.lower()}@depth10"
                sub_msg = {"method": "SUBSCRIBE", "params": [stream], "id": req_id}
            else:
                channel = f"{CHANNEL_OB_PFX}{pair}"
                sub_msg = {"method": 1, "params": {"channel": channel}, "id": req_id}
            self._ws_ref.send(json.dumps(sub_msg))
            self._ob_subscribed.add(pair)
            logger.info(f"[MarketWS] Dynamic OB Subscribed: {pair}")
        except Exception: pass

    def subscribe_market_trades(self, pair: str):
        threading.Thread(target=self._fetch_initial_trades, args=(pair,), daemon=True).start()
        with self._lock:
            self._trade_subscribed.add(pair)
            if not self._ws_ref:
                logger.warning(f"[MarketWS] WebSocket not connected yet. Queued trade subscription for: {pair}")
                return
        eid = os.getenv("EXCHANGE", "binance").lower()
        try:
            req_id = self._next_id()
            if eid == "binance":
                stream = f"{pair.lower()}@trade"
                sub_msg = {"method": "SUBSCRIBE", "params": [stream], "id": req_id}
            else:
                channel = f"{CHANNEL_TRADE_PFX}{pair}"
                sub_msg = {"method": 1, "params": {"channel": channel}, "id": req_id}
            self._ws_ref.send(json.dumps(sub_msg))
            logger.warning(f"[MarketWS] Dynamic Trade Subscribed: {pair}")
        except Exception as e:
            logger.error(f"[MarketWS] Failed to subscribe trades for {pair}: {e}")

    def subscribe_market_trades_bulk(self, pairs: list):
        if not pairs: return
        for p in pairs:
            threading.Thread(target=self._fetch_initial_trades, args=(p,), daemon=True).start()
            
        with self._lock:
            for p in pairs:
                self._trade_subscribed.add(p)
            if not self._ws_ref:
                logger.warning(f"[MarketWS] WebSocket not connected yet. Queued bulk trade subscriptions for: {pairs}")
                return
        eid = os.getenv("EXCHANGE", "binance").lower()
        try:
            if eid == "binance":
                # Batch streams in groups of 20 to avoid any frame size or connection limits
                chunk_size = 20
                for i in range(0, len(pairs), chunk_size):
                    chunk = pairs[i:i+chunk_size]
                    streams = [f"{p.lower()}@trade" for p in chunk]
                    sub_msg = {"method": "SUBSCRIBE", "params": streams, "id": self._next_id()}
                    self._ws_ref.send(json.dumps(sub_msg))
                    logger.warning(f"[MarketWS] Dynamic Trades Bulk Subscribed: {chunk}")
                    time.sleep(0.5)  # Nice safe delay between bulk messages
            else:
                for p in pairs:
                    req_id = self._next_id()
                    channel = f"{CHANNEL_TRADE_PFX}{p}"
                    sub_msg = {"method": 1, "params": {"channel": channel}, "id": req_id}
                    self._ws_ref.send(json.dumps(sub_msg))
                    logger.warning(f"[MarketWS] Dynamic Trade Subscribed: {p}")
                    time.sleep(0.05)
        except Exception as e:
            logger.error(f"[MarketWS] Failed bulk trade subscriptions: {e}")

    def _fetch_initial_trades(self, pair: str):
        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            # Skip REST fetching for historical trades to avoid 429 Too many requests
            return

        # Indodax REST Fallback
        try:
            import requests
            url = f"https://indodax.com/api/trades/{p_rest}"
            resp = requests.get(url, timeout=5).json()
            converted = []
            for t in resp:
                price = float(t['price'])
                amount = float(t['amount'])
                converted.append([
                    p_rest,
                    int(t['date']),
                    t['tid'],
                    t['type'],
                    price,
                    price * amount,
                    amount
                ])
            with self._lock:
                existing = self._trades.get(p_rest, [])
                if not existing:
                    self._trades[p_rest] = converted
                    logger.info(f"[MarketWS] Pre-filled {len(converted)} trades for {p_rest}")
        except Exception as e:
            logger.warning(f"[MarketWS] Failed initial trades for Indodax {p_rest}: {e}")

    def _subscribe_top_orderbooks(self, summary_snapshot: dict):
        candidates = [
            (pair, d["vol_idr"])
            for pair, d in summary_snapshot.items()
            if (d.get("vol_idr") or 0) >= OB_MIN_VOL_IDR
        ]
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_pairs = [p for p, _ in candidates[:OB_TOP_PAIRS]]
        
        hp_pairs = _get_high_priority_pairs()
        for hp in hp_pairs:
            if hp not in top_pairs:
                top_pairs.insert(0, hp)

        eid = os.getenv("EXCHANGE", "binance").lower()
        if eid == "binance":
            to_sub = [p for p in top_pairs if p not in self._ob_subscribed]
            if not to_sub: return
            try:
                # Batch streams in groups of 20
                chunk_size = 20
                for i in range(0, len(to_sub), chunk_size):
                    chunk = to_sub[i:i+chunk_size]
                    streams = [f"{p.lower()}@depth10" for p in chunk]
                    sub_msg = {"method": "SUBSCRIBE", "params": streams, "id": self._next_id()}
                    self._ws_ref.send(json.dumps(sub_msg))
                    for p in chunk:
                        self._ob_subscribed.add(p)
                    logger.info(f"[MarketWS] Bulk Subscribed order-books: {chunk}")
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"[MarketWS] Failed bulk OB subscription: {e}")
        else:
            for pair in top_pairs:
                if pair in self._ob_subscribed:
                    continue
                try:
                    req_id = self._next_id()
                    channel = f"{CHANNEL_OB_PFX}{pair}"
                    sub_msg = {"method": 1, "params": {"channel": channel}, "id": req_id}
                    self._ws_ref.send(json.dumps(sub_msg))
                    self._ob_subscribed.add(pair)
                    logger.info(f"[MarketWS] Subscribed order-book: {pair}")
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning(f"[MarketWS] Failed to subscribe OB for {pair}: {e}")

    def _on_error(self, ws, error):
        logger.error(f"[MarketWS] Error: {error}")
        ws.close()

    def _on_close(self, ws, code, reason):
        self._connected.clear()
        logger.warning(f"[MarketWS] Connection closed (code={code}, reason={reason})")
