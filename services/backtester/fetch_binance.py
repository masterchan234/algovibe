"""
Fetch OHLCV 1m dari Binance dan insert ke tabel ohlcv_1m (backtest DB).
Dipanggil via:  docker compose run --rm backtester fetch-binance [--days 30] [--pairs btcusdt,ethusdt] [--top 20]
"""
import os
import time
from datetime import datetime, timedelta, timezone

import ccxt
import psycopg2.extras

from services.backtester.db_backtest import get_connection

# ── Default pairs (top liquid USDT) ──────────────────────────────────────────
DEFAULT_PAIRS = [
    "btcusdt", "ethusdt", "bnbusdt", "solusdt", "xrpusdt",
    "dogeusdt", "adausdt", "avaxusdt", "linkusdt", "dotusdt",
    "shibusdt", "ltcusdt", "uniusdt", "trxusdt", "nearusdt",
    "aptusdt", "arbusdt", "opusdt", "polusdt", "atomusdt",
    "injusdt", "suiusdt", "tiausdt", "seiusdt", "jupusdt",
    "wifusdt", "bonkusdt", "pepeusdt", "flokiusdt", "ondousdt",
    "fetusdt", "renderusdt", "grtusdt", "1000satsusdt", "notusdt",
    "taousdt", "eigenusdt", "enausdt", "wusdt", "strkusdt",
    "dymusdt", "altusdt", "pythusdt", "jtousdt", "rezusdt",
    "memeusdt", "arusdt", "turbousdt", "actusdt", "penguusdt",
]

BATCH_LIMIT = 1500
RATE_DELAY  = 0.25   # detik antar request


# ── Helpers ───────────────────────────────────────────────────────────────────
def _make_exchange() -> ccxt.binance:
    return ccxt.binance({
        "apiKey":  os.getenv("BINANCE_API_KEY", ""),
        "secret":  os.getenv("BINANCE_API_SECRET", ""),
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })


def _get_newest_ts(conn, pair: str):
    """Kembalikan timestamp candle terbaru untuk pair, atau None."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(ts) FROM ohlcv_1m WHERE pair = %s", (pair,))
    row = cur.fetchone()
    if not row:
        return None
    # get_connection() pakai RealDictCursor — akses by column name
    val = row.get("max") if hasattr(row, "get") else row[0]
    return val


def _insert_candles(conn, pair: str, candles: list) -> int:
    if not candles:
        return 0
    sql = """
        INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume)
        VALUES %s
        ON CONFLICT (ts, pair) DO NOTHING
    """
    rows = [
        (
            datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
            pair,
            float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]),
        )
        for c in candles
        if c[4] is not None
    ]
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
    conn.commit()
    return len(rows)


def _fetch_pair(exchange: ccxt.binance, conn, pair: str, days: int) -> int:
    symbol_ccxt = pair.upper().replace("USDT", "/USDT")  # btcusdt -> BTC/USDT
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # Resume dari candle terakhir jika ada
    newest = _get_newest_ts(conn, pair)
    if newest:
        newest_ms = int(newest.timestamp() * 1000)
        if newest_ms >= end_ms - 120_000:
            print(f"[SKIP] {pair} — sudah up-to-date ({newest})", flush=True)
            return 0
        if newest_ms > start_ms:
            start_ms = newest_ms + 60_000
            print(f"[RESUME] {pair} — mulai dari {newest}", flush=True)

    total = 0
    cursor = start_ms

    while cursor < end_ms:
        try:
            candles = exchange.fetch_ohlcv(symbol_ccxt, timeframe="1m", since=cursor, limit=BATCH_LIMIT)
        except ccxt.RateLimitExceeded:
            print(f"[RATELIMIT] {pair} — tunggu 10 detik...", flush=True)
            time.sleep(10)
            continue
        except Exception as e:
            print(f"[ERROR] {pair} — {e}", flush=True)
            break

        if not candles:
            break

        n = _insert_candles(conn, pair, candles)
        total += n
        cursor = candles[-1][0] + 60_000
        last_dt = datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"  {pair}: +{n} candles (s.d. {last_dt})", flush=True)
        time.sleep(RATE_DELAY)

    return total


def _get_top_liquid(exchange: ccxt.binance, top_n: int) -> list:
    """Kembalikan top N pair USDT by quoteVolume. top_n=0 berarti semua pair aktif."""
    label = f"top {top_n}" if top_n > 0 else "semua"
    print(f"[AUTO] Mengambil {label} liquid USDT pairs dari Binance...", flush=True)
    tickers = exchange.fetch_tickers()
    usdt = [
        (k, v.get("quoteVolume") or 0)
        for k, v in tickers.items()
        if k.endswith("/USDT")
    ]
    usdt.sort(key=lambda x: x[1], reverse=True)
    result = usdt if top_n == 0 else usdt[:top_n]
    return [p[0].replace("/USDT", "usdt").lower() for p in result]


# ── Entry point ───────────────────────────────────────────────────────────────
def run_fetch(days: int = 30, pairs_arg: str = "", top: int = 0):
    exchange = _make_exchange()

    if pairs_arg:
        pairs = [p.strip().lower() for p in pairs_arg.split(",") if p.strip()]
    elif top > 0:
        pairs = _get_top_liquid(exchange, top)
    else:
        pairs = _get_top_liquid(exchange, 0)  # semua pair aktif di Binance Spot

    print(f"[START] Fetch {days} hari OHLCV 1m untuk {len(pairs)} pairs", flush=True)

    conn = get_connection()
    grand_total = 0

    for i, pair in enumerate(pairs, 1):
        print(f"[{i}/{len(pairs)}] {pair}", flush=True)
        try:
            n = _fetch_pair(exchange, conn, pair, days)
            grand_total += n
            print(f"[{i}/{len(pairs)}] {pair}: {n} candles baru", flush=True)
        except Exception as e:
            print(f"[{i}/{len(pairs)}] {pair} GAGAL: {e}", flush=True)

    conn.close()
    print(f"[DONE] Total {grand_total} candles diinsert.", flush=True)
    print("Jalankan backtest: docker compose run --rm backtester run --allow-global-fallback", flush=True)
