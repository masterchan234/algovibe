"""
Fetch 30-hari OHLCV 1m dari Binance dan insert ke tabel ohlcv_1m.
Dijalankan di HOST (bukan Docker), menghubungi timescaledb di localhost:5432.

Usage:
    python3 scripts/fetch_binance_ohlcv.py
    python3 scripts/fetch_binance_ohlcv.py --days 30 --pairs btcusdt,ethusdt
    python3 scripts/fetch_binance_ohlcv.py --top 20   # ambil top 20 liquid pairs otomatis
"""
import os
import sys
import time
import argparse
import logging
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

# Pastikan .venv aktif atau ccxt sudah terinstall
try:
    import ccxt
except ImportError:
    sys.exit("[ERROR] ccxt tidak ditemukan. Jalankan: pip install ccxt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fetch_binance_ohlcv")

# ── Konfigurasi ──────────────────────────────────────────────────────────────
DB_URL = os.getenv(
    "DATABASE_URL_BACKTEST",
    "postgresql://algovibe:secretpassword@localhost:5432/algovibe",
)
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# Top liquid USDT pairs default (bisa di-override dengan --pairs)
DEFAULT_PAIRS = [
    "btcusdt", "ethusdt", "bnbusdt", "solusdt", "xrpusdt",
    "dogeusdt", "adausdt", "avaxusdt", "linkusdt", "dotusdt",
    "shibusdt", "ltcusdt", "uniusdt", "trxusdt", "nearusdt",
    "aptusdt", "arbusdt", "opusdt", "maticusdt", "atomusdt",
]

BATCH_SIZE   = 1500   # max candles per request Binance
RATE_DELAY   = 0.25   # detik antar request (aman < 1200 req/min)


def get_db_conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def get_existing_range(conn, pair: str):
    """Cek range data yang sudah ada agar tidak re-fetch."""
    cur = conn.cursor()
    cur.execute(
        "SELECT MIN(ts) AS oldest, MAX(ts) AS newest FROM ohlcv_1m WHERE pair = %s",
        (pair,),
    )
    row = cur.fetchone()
    return row["oldest"], row["newest"]


def insert_candles(conn, pair: str, candles: list):
    """Upsert candles ke ohlcv_1m."""
    if not candles:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume)
        VALUES %s
        ON CONFLICT (ts, pair) DO NOTHING
    """
    rows = [
        (
            datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
            pair,
            float(c[1]),
            float(c[2]),
            float(c[3]),
            float(c[4]),
            float(c[5]),
        )
        for c in candles
        if c[4] is not None  # skip incomplete candles
    ]
    psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
    conn.commit()
    return len(rows)


def fetch_pair(exchange, pair: str, days: int, conn):
    """Fetch semua candles untuk satu pair selama N hari terakhir."""
    symbol_ccxt = pair.upper().replace("USDT", "/USDT")  # btcusdt -> BTC/USDT

    # Hitung window waktu
    end_ts   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # Cek data existing — skip jika sudah ada
    oldest, newest = get_existing_range(conn, pair)
    if oldest and newest:
        existing_start = int(oldest.timestamp() * 1000)
        existing_end   = int(newest.timestamp() * 1000)
        if existing_start <= start_ts and existing_end >= end_ts - 120_000:
            log.info(f"[SKIP] {pair} — data sudah ada ({oldest} s.d. {newest})")
            return 0

    total_inserted = 0
    cursor = start_ts

    while cursor < end_ts:
        try:
            candles = exchange.fetch_ohlcv(
                symbol_ccxt,
                timeframe="1m",
                since=cursor,
                limit=BATCH_SIZE,
            )
        except ccxt.RateLimitExceeded:
            log.warning(f"[RATELIMIT] {pair} — tunggu 10 detik...")
            time.sleep(10)
            continue
        except ccxt.NetworkError as e:
            log.error(f"[NETWORK] {pair} — {e}")
            time.sleep(5)
            continue
        except Exception as e:
            log.error(f"[ERROR] {pair} — {e}")
            break

        if not candles:
            break

        inserted = insert_candles(conn, pair, candles)
        total_inserted += inserted
        cursor = candles[-1][0] + 60_000  # lanjut dari candle terakhir + 1 menit

        log.info(
            f"  {pair}: {inserted} candles disimpan "
            f"(s.d. {datetime.fromtimestamp(candles[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})"
        )
        time.sleep(RATE_DELAY)

    return total_inserted


def get_top_liquid_pairs(exchange, top_n: int) -> list:
    """Ambil top N USDT pairs berdasarkan 24h quoteVolume dari Binance."""
    log.info(f"[AUTO] Mengambil top {top_n} liquid pairs dari Binance...")
    tickers = exchange.fetch_tickers()
    usdt_pairs = [
        (k, v.get("quoteVolume", 0) or 0)
        for k, v in tickers.items()
        if k.endswith("/USDT") and v.get("quoteVolume", 0)
    ]
    usdt_pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0].replace("/USDT", "usdt").lower() for p in usdt_pairs[:top_n]]


def main():
    parser = argparse.ArgumentParser(description="Fetch Binance OHLCV 1m ke DB backtest")
    parser.add_argument("--days",  type=int, default=30,  help="Jumlah hari ke belakang (default: 30)")
    parser.add_argument("--pairs", type=str, default="",  help="Comma-separated pairs, e.g. btcusdt,ethusdt")
    parser.add_argument("--top",   type=int, default=0,   help="Ambil top N liquid pairs otomatis dari Binance")
    args = parser.parse_args()

    # Init ccxt Binance
    exchange = ccxt.binance({
        "apiKey":  BINANCE_API_KEY,
        "secret":  BINANCE_API_SECRET,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })

    # Tentukan pairs
    if args.pairs:
        pairs = [p.strip().lower() for p in args.pairs.split(",") if p.strip()]
    elif args.top > 0:
        pairs = get_top_liquid_pairs(exchange, args.top)
    else:
        pairs = DEFAULT_PAIRS

    log.info(f"[START] Fetch {args.days} hari OHLCV untuk {len(pairs)} pairs: {pairs}")

    conn = get_db_conn()
    grand_total = 0

    for i, pair in enumerate(pairs, 1):
        log.info(f"[{i}/{len(pairs)}] Fetching {pair}...")
        try:
            n = fetch_pair(exchange, pair, args.days, conn)
            grand_total += n
            log.info(f"[{i}/{len(pairs)}] {pair}: total {n} candles baru")
        except Exception as e:
            log.error(f"[{i}/{len(pairs)}] {pair} GAGAL: {e}")

    conn.close()
    log.info(f"[DONE] Total {grand_total} candles diinsert untuk {len(pairs)} pairs.")
    log.info("Sekarang jalankan backtest:")
    log.info("  docker compose run --rm backtester run --allow-global-fallback")


if __name__ == "__main__":
    main()
