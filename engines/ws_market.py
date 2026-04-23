"""
Market Data WebSocket Client — Indodax
======================================
Menggantikan REST polling (requests.get /api/tickers) dengan
WebSocket real-time stream dari wss://ws3.indodax.com/ws/

Channel yang digunakan:
  - market:summary-24h  → semua ticker (harga, volume, spread)
  - market:order-book-<pair>  → orderbook per pair (opsional)
  - market:trade-activity-<pair> → trade activity per pair

Referensi: Marketdata-websocket.md
"""

import json
import time
import logging
import threading
import websocket   # websocket-client

logger = logging.getLogger(__name__)

# ─── Konstanta Indodax Market WS ───────────────────────────────────────────
WS_MARKET_URL   = "wss://ws3.indodax.com/ws/"
WS_MARKET_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJleHAiOjE5NDY2MTg0MTV9"
    ".UR1lBM6Eqh0yWz-PVirw1uPCxe60FdchR8eNVdsskeo"
)

CHANNEL_SUMMARY = "market:summary-24h"
CHANNEL_OB_PFX  = "market:order-book-"   # + pair, e.g. market:order-book-btc_idr

# Berapa banyak pair teratas (by volume) yang akan di-subscribe order book-nya
OB_TOP_PAIRS     = 50
# Filter volume minimum sebelum subscribe order book (hindari pair mati)
OB_MIN_VOL_IDR   = 50_000_000  # 50 Juta IDR (diturunkan agar lebih banyak pair tercover)

# Indeks kolom pada payload market:summary-24h
# [pair, epoch, last, low, high, open, vol_idr, vol_coin]
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
    WebSocket client untuk Market Data Indodax.

    Penggunaan:
        mws = MarketDataWS()
        mws.start()          # non-blocking, jalan di thread terpisah
        ...
        tickers = mws.get_tickers()   # ambil snapshot terbaru
        mws.stop()
    """

    def __init__(self):
        self._tickers: dict = {}          # {pair: {last, buy, sell, high, low, vol_idr, ...}}
        self._lock    = threading.Lock()
        self._ws      = None
        self._thread  = None
        self._running = False
        self._req_id  = 0
        self._connected = threading.Event()  # set saat autentikasi berhasil
        self._ob_subscribed: set = set()     # pair yang sudah di-subscribe order book
        self._ws_ref = None                  # ref ke ws instance untuk subscribe dinamis
        self._ob_done = False                # True setelah OB subscribe pertama selesai
        self._pending_subs: dict = {}        # {req_id: channel_name} untuk ACK tracking

    # ── Public API ───────────────────────────────────────────────────────────

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
        """Blok hingga data pertama diterima (atau timeout detik)."""
        return self._connected.wait(timeout=timeout)

    def get_tickers(self) -> dict:
        """
        Kembalikan snapshot ticker saat ini.
        Format kompatibel dengan /api/tickers Indodax REST:
          {pair: {last, buy, sell, high, low, vol_idr, vol_<coin>}}
        """
        with self._lock:
            return dict(self._tickers)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _run(self):
        """Loop utama dengan auto-reconnect."""
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    WS_MARKET_URL,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                # suppress_origin=True — hindari 403 Forbidden (lihat Troubleshooting docs)
                self._ws.run_forever(suppress_origin=True, ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"[MarketWS] run_forever exception: {e}")

            if self._running:
                self._conn_token = None
                self._ob_subscribed.clear()   # reset agar re-subscribe setelah reconnect
                self._ob_done = False         # reset flag agar trigger ulang
                logger.warning("[MarketWS] Disconnected, reconnecting in 5s…")
                time.sleep(5)

    def _on_open(self, ws):
        self._ws_ref = ws
        logger.info("[MarketWS] Connected. Authenticating…")
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
        import traceback
        try:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                return

            # ── Auth response ──────────────────────────────────────────────────
            if "result" in msg and "client" in msg.get("result", {}):
                logger.info(f"[MarketWS] Auth OK — client: {msg['result']['client']}")
                # Step 2: Subscribe ke channel summary 24h
                req_id = self._next_id()
                sub_msg = {
                    "method": 1,
                    "params": {"channel": CHANNEL_SUMMARY},
                    "id": req_id,
                }
                self._pending_subs[req_id] = CHANNEL_SUMMARY
                ws.send(json.dumps(sub_msg))
                return

            # ── Subscribe ACK ──────────────────────────────────────────────────
            if "id" in msg and "result" in msg and "recoverable" in msg.get("result", {}):
                req_id = msg["id"]
                channel_name = self._pending_subs.pop(req_id, f"unknown (id={req_id})")
                logger.info(f"[MarketWS] Subscribed to {channel_name} | "
                            f"offset={msg['result'].get('offset')}")
                return

            # ── Streaming data ─────────────────────────────────────────────────
            result = msg.get("result", {})
            channel = result.get("channel", "")
            data_wrap = result.get("data", {})

            if channel == CHANNEL_SUMMARY:
                rows = data_wrap.get("data", [])
                self._process_summary(rows)

            elif channel.startswith(CHANNEL_OB_PFX):
                pair = channel[len(CHANNEL_OB_PFX):]
                ob_data = data_wrap.get("data", {})
                # DEBUG: Log format OB pertama kali per pair (hapus setelah format diketahui)
                if not hasattr(self, '_ob_format_logged'):
                    self._ob_format_logged = set()
                if pair not in self._ob_format_logged:
                    logger.info(f"[MarketWS] OB FORMAT DEBUG ({pair}): keys={list(ob_data.keys()) if isinstance(ob_data, dict) else type(ob_data).__name__} | sample={str(ob_data)[:300]}")
                    self._ob_format_logged.add(pair)
                self._process_orderbook(pair, ob_data)
        except Exception as e:
            logger.error(f"[MarketWS] Critical Error in _on_message: {e}\n{traceback.format_exc()}")

    def _process_summary(self, rows: list):
        """Parse baris market:summary-24h dan update internal ticker dict."""
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

            # Estimasi buy/sell dari last (WS summary tidak kirim bid/ask).
            # Jika data Order Book yang lebih akurat sudah ada, pertahankan nilainya.
            existing = self._tickers.get(pair, {})
            updated[pair] = {
                "last":      last,
                # Gunakan harga real dari OB jika sudah ada, jangan timpa dengan estimasi
                "buy":       existing.get("buy", last) if existing.get("buy", last) != existing.get("last", last) else last,
                "sell":      existing.get("sell", last) if existing.get("sell", last) != existing.get("last", last) else last,
                "high":      high,
                "low":       low,
                "open":      open_p,
                "vol_idr":   vol_idr,
                "vol_coin":  vol_coin,
                # Pertahankan kedalaman order book jika sudah ada
                "bid_depth": existing.get("bid_depth", 0),
                "ask_depth": existing.get("ask_depth", 0),
            }

        # Trigger OB subscribe dinamis, seiring bertambahnya pair bervolume tinggi
        # (Karena summary-24h payload-nya parsial, tidak datang 200 pair sekaligus)
        need_ob_subscribe = False
        with self._lock:
            self._tickers.update(updated)
            if not self._connected.is_set():
                self._connected.set()
                logger.info(f"[MarketWS] First data received — {len(self._tickers)} pairs loaded.")
            
            # Jika belum mencapai kuota maksimal langganan OB
            if len(self._ob_subscribed) < OB_TOP_PAIRS:
                need_ob_subscribe = True
                
        if need_ob_subscribe and self._ws_ref:
            with self._lock:
                snapshot = dict(self._tickers)
            self._subscribe_top_orderbooks(snapshot)

    def _subscribe_top_orderbooks(self, summary_snapshot: dict):
        """Subscribe ke order-book channel untuk top N pairs bervolume tinggi."""
        candidates = [
            (pair, d["vol_idr"])
            for pair, d in summary_snapshot.items()
            if (d.get("vol_idr") or 0) >= OB_MIN_VOL_IDR
        ]
        # Urutkan descending by volume
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # Ambil sampai batas OB_TOP_PAIRS
        top_pairs = [p for p, _ in candidates[:OB_TOP_PAIRS]]

        for pair in top_pairs:
            if pair in self._ob_subscribed:
                continue
            try:
                req_id = self._next_id()
                channel = f"{CHANNEL_OB_PFX}{pair}"
                sub_msg = {
                    "method": 1,
                    "params": {"channel": channel},
                    "id": req_id,
                }
                self._pending_subs[req_id] = channel
                self._ws_ref.send(json.dumps(sub_msg))
                self._ob_subscribed.add(pair)
                logger.info(f"[MarketWS] Subscribed order-book: {pair}")
            except Exception as e:
                logger.warning(f"[MarketWS] Failed to subscribe OB for {pair}: {e}")

    def _process_orderbook(self, pair: str, data: dict):
        """
        Update bid/ask terbaik dari payload order book.
        Format Indodax yang sudah dikonfirmasi:
        {
          'pair': 'usdtidr',
          'ask': [{'price': '17291', 'idr_volume': '295908835', 'usdt_volume': '...'}, ...],
          'bid': [{'price': '17290', 'idr_volume': '...', 'usdt_volume': '...'}, ...]
        }
        """
        bids = data.get("bid", [])
        asks = data.get("ask", [])

        if not bids and not asks:
            logger.warning(f"[MarketWS] OB ({pair}): both bid and ask are empty — data keys: {list(data.keys())}")
            return

        try:
            bid_depth = 0.0
            ask_depth = 0.0
            best_bid  = 0.0
            best_ask  = 0.0

            # ── Parse BIDs ────────────────────────────────────────────────
            for i, b in enumerate(bids[:10]):
                price = self._safe_float(b.get("price"))
                vol   = self._safe_float(b.get("idr_volume"))
                if i == 0:
                    best_bid = price
                bid_depth += vol

            # ── Parse ASKs ────────────────────────────────────────────────
            for i, a in enumerate(asks[:10]):
                price = self._safe_float(a.get("price"))
                vol   = self._safe_float(a.get("idr_volume"))
                if i == 0:
                    best_ask = price
                ask_depth += vol

            if best_bid == 0 and best_ask == 0:
                logger.warning(f"[MarketWS] OB ({pair}): best_bid=0 best_ask=0 — bids={len(bids)} asks={len(asks)}")
                return
        except (IndexError, ValueError, TypeError):
            return

        with self._lock:
            if pair in self._tickers:
                if best_bid > 0:
                    self._tickers[pair]["buy"]       = best_bid
                if best_ask > 0:
                    self._tickers[pair]["sell"]      = best_ask
                self._tickers[pair]["bid_depth"]     = bid_depth
                self._tickers[pair]["ask_depth"]     = ask_depth
            else:
                # Pair belum ada di summary ticker, simpan sementara
                self._tickers[pair] = {
                    "last": best_bid, "buy": best_bid, "sell": best_ask,
                    "high": 0, "low": 0, "open": 0,
                    "vol_idr": 0, "vol_coin": "0",
                    "bid_depth": bid_depth, "ask_depth": ask_depth
                }

    def _on_error(self, ws, error):
        logger.error(f"[MarketWS] Error: {error}")
        ws.close()

    def _on_close(self, ws, code, reason):
        self._connected.clear()
        logger.warning(f"[MarketWS] Connection closed (code={code}, reason={reason})")
