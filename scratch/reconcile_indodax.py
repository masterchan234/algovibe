"""
Reconcile: Bandingkan data order/trade di database lokal vs Indodax API.

Checks:
  1. Open positions — apakah entry_order_id terkonfirmasi FILLED di Indodax?
  2. ADOPT- positions — apakah pair masih ada di saldo Indodax?
  3. trade_history — apakah exit_order_id ter-record di Indodax sebagai FILLED?
  4. Orphan trades — ada trade di Indodax yang tidak tercatat di DB lokal?
  5. PnL accuracy — selisih antara pnl_idr lokal vs hitung ulang dari data Indodax.

Jalankan: python3 scratch/reconcile_indodax.py
"""

import os, sys, time, hmac, hashlib, sqlite3, json
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

API_KEY    = os.getenv('INDODAX_API_KEY', '')
API_SECRET = os.getenv('INDODAX_API_SECRET', '')
DB_PATH    = 'data/algotrade.db'
BASE_URL   = 'https://tapi.indodax.com'

# ─── Auth Helper ─────────────────────────────────────────────────────────────

def _signed_get(endpoint: str, params: dict) -> dict:
    """Kirim GET request dengan HMAC-SHA512 signature ke Indodax v2 API."""
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 10000
    qs = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    sign = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha512).hexdigest()
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-APIKEY': API_KEY,
        'Sign': sign,
    }
    url = f"{BASE_URL}{endpoint}?{qs}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {'error': str(e), 'data': []}


def _tapi_post(method: str, extra_params: dict = None) -> dict:
    """Kirim POST ke /tapi (legacy endpoint) — digunakan untuk getInfo / balance."""
    import urllib.parse
    nonce = int(time.time() * 1000)
    params = {'method': method, 'nonce': nonce}
    if extra_params:
        params.update(extra_params)
    qs = urllib.parse.urlencode(params)
    sign = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha512).hexdigest()
    headers = {
        'Key': API_KEY,
        'Sign': sign,
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    try:
        resp = requests.post('https://indodax.com/tapi', data=params, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {'error': str(e)}


# ─── DB Helper ───────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Fetch Indodax Data ───────────────────────────────────────────────────────

def fetch_order_history(symbol: str, days_back: int = 7) -> list:
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days_back * 86400 * 1000
    result = _signed_get('/api/v2/order/histories', {
        'symbol':    symbol,
        'startTime': start_ms,
        'endTime':   end_ms,
        'limit':     500,
        'sort':      'desc',
    })
    return result.get('data', [])


def fetch_trade_history(symbol: str, days_back: int = 7) -> list:
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days_back * 86400 * 1000
    result = _signed_get('/api/v2/myTrades', {
        'symbol':    symbol,
        'startTime': start_ms,
        'endTime':   end_ms,
        'limit':     500,
        'sort':      'desc',
    })
    return result.get('data', [])


def fetch_balance() -> dict:
    """Ambil saldo akun dari Indodax."""
    resp = _tapi_post('getInfo')
    if 'return' in resp:
        return resp['return'].get('balance', {})
    return {}


# ─── Main Reconcile ───────────────────────────────────────────────────────────

def main():
    if not API_KEY or not API_SECRET:
        print("ERROR: INDODAX_API_KEY / INDODAX_API_SECRET tidak ditemukan di .env")
        sys.exit(1)

    conn = get_db()
    c    = conn.cursor()

    # Ambil semua open positions
    c.execute("SELECT pair, entry_price, amount_crypto, amount_idr, status, entry_order_id, exit_order_id, dry_run FROM positions")
    positions = [dict(r) for r in c.fetchall()]

    # Ambil trade history lokal (7 hari terakhir)
    c.execute("""
        SELECT id, pair, entry_price, exit_price, amount_crypto, pnl_idr, reason, exec_type, created_at
        FROM trade_history
        WHERE created_at >= datetime('now', '-7 days')
        ORDER BY created_at DESC
    """)
    local_history = [dict(r) for r in c.fetchall()]
    conn.close()

    # Kumpulkan semua pair unik
    all_pairs = set(p['pair'] for p in positions) | set(t['pair'] for t in local_history)

    print(f"\n{'='*70}")
    print(f"  REKONSILIASI DATABASE vs INDODAX  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"{'='*70}")
    print(f"  Pair yang akan dicek: {sorted(all_pairs)}\n")

    # Ambil saldo sekarang dari Indodax
    print(">>> Mengambil saldo akun dari Indodax...")
    balance = fetch_balance()
    if not balance:
        print("    [WARN] Gagal ambil saldo. Pastikan API key aktif.\n")

    # Fetch semua order + trade dari Indodax per pair
    indodax_orders = {}   # pair -> list of order history
    indodax_trades = {}   # pair -> list of trade fills

    for pair in sorted(all_pairs):
        print(f">>> Fetching Indodax history untuk [{pair}]...")
        orders = fetch_order_history(pair, days_back=7)
        trades = fetch_trade_history(pair, days_back=7)
        indodax_orders[pair] = orders
        indodax_trades[pair] = trades
        print(f"    Orders: {len(orders)}, Trades: {len(trades)}")
        time.sleep(0.3)  # rate-limit friendly

    print()

    # ── CHECK 1: Open Positions vs Indodax Order Status ───────────────────────
    print(f"{'─'*70}")
    print("CHECK 1: Status Open Positions vs Indodax")
    print(f"{'─'*70}")

    for pos in positions:
        pair        = pos['pair']
        order_id    = pos['entry_order_id'] or ''
        entry_price = pos['entry_price']
        amount      = pos['amount_crypto']

        is_adopt    = order_id.startswith('ADOPT-')
        status_tag  = '[ADOPT]' if is_adopt else '[REAL ORDER]'

        print(f"\n  Pair    : {pair}  {status_tag}")
        print(f"  OrderID : {order_id}")
        print(f"  Entry   : {entry_price:,.2f}  |  Amount: {amount}")

        if is_adopt:
            # Untuk ADOPT, cek apakah saldo pair masih ada di Indodax
            coin = pair.replace('idr', '').replace('usdt', '')
            coin_bal = float(balance.get(coin, 0))
            print(f"  Saldo {coin.upper()} di Indodax: {coin_bal}")
            if coin_bal >= amount * 0.95:
                print(f"  [OK] Saldo cocok (DB: {amount}, Indodax: {coin_bal})")
            elif coin_bal > 0:
                print(f"  [WARN] Saldo KURANG dari expected (DB: {amount}, Indodax: {coin_bal})")
            else:
                print(f"  [MISMATCH!] Koin {coin.upper()} tidak ada di saldo Indodax — posisi mungkin sudah sold!")
        else:
            # Cari order_id ini di Indodax order history
            orders = indodax_orders.get(pair, [])
            match = next((o for o in orders if o.get('orderId') == order_id), None)
            if match:
                status = match.get('status', 'UNKNOWN')
                exec_qty = float(match.get('executedQty', 0))
                orig_qty = float(match.get('oriQty', 0))
                print(f"  Status Indodax: {status}  |  Qty: {exec_qty}/{orig_qty}")
                if status == 'FILLED':
                    print(f"  [OK] Order FILLED di Indodax — posisi valid")
                elif status == 'CANCELLED':
                    print(f"  [WARN!] Order CANCELLED di Indodax — posisi di DB mungkin ghost!")
                else:
                    print(f"  [INFO] Order status: {status}")
            else:
                # Cari di trade fills
                trades = indodax_trades.get(pair, [])
                trade_match = next((t for t in trades if t.get('orderId') == order_id), None)
                if trade_match:
                    print(f"  [OK] Ditemukan di trade fills (tidak ada di order history 7d, mungkin lebih lama)")
                else:
                    print(f"  [INFO] Order {order_id} tidak ditemukan di history 7 hari terakhir (mungkin lebih lama dari 7 hari)")

    # ── CHECK 2: trade_history lokal vs Indodax ────────────────────────────────
    print(f"\n{'─'*70}")
    print("CHECK 2: trade_history Lokal (7 hari) vs Indodax Trade Fills")
    print(f"{'─'*70}")

    total_ok   = 0
    total_miss = 0
    total_diff = 0

    for trade in local_history:
        pair        = trade['pair']
        local_exit  = float(trade['exit_price'] or 0)
        local_pnl   = float(trade['pnl_idr'] or 0)
        local_amt   = float(trade['amount_crypto'] or 0)
        reason      = trade['reason']
        created_at  = trade['created_at']

        trades = indodax_trades.get(pair, [])
        # Cari trade fill yang mendekati waktu exit lokal dan harga cocok
        local_ts_ms = int(datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc).timestamp() * 1000)
        # Toleransi ±10 menit = 600.000 ms
        candidates = [
            t for t in trades
            if abs(int(t.get('time', 0)) - local_ts_ms) < 600_000
        ]

        if not candidates:
            # Cari tanpa filter waktu (bisa jadi 7 hari window tidak mencakup)
            print(f"\n  [{trade['id']}] {pair} | {reason} | exit={local_exit:,.2f} | pnl={local_pnl:+,.0f} IDR | {created_at}")
            print(f"  [NOT FOUND] Trade tidak ada di Indodax history 7 hari (mungkin lebih lama)")
            total_miss += 1
            continue

        # Ambil kandidat terdekat berdasarkan harga
        best = min(candidates, key=lambda t: abs(float(t.get('price', 0)) - local_exit))
        indodax_price  = float(best.get('price', 0))
        indodax_qty    = float(best.get('qty', 0))
        indodax_fee    = float(best.get('commission', 0))
        is_maker       = best.get('isMaker', False)
        is_buyer       = best.get('isBuyer', False)
        side           = 'BUY' if is_buyer else 'SELL'

        price_diff_pct = abs(indodax_price - local_exit) / local_exit * 100 if local_exit else 0
        qty_diff_pct   = abs(indodax_qty - local_amt) / local_amt * 100 if local_amt else 0

        print(f"\n  [{trade['id']}] {pair} | {reason} | {created_at}")
        print(f"  DB exit={local_exit:,.2f}  Indodax exit={indodax_price:,.2f}  diff={price_diff_pct:.3f}%")
        print(f"  DB qty={local_amt}  Indodax qty={indodax_qty}  diff={qty_diff_pct:.3f}%")
        print(f"  DB pnl={local_pnl:+,.0f} IDR  |  Indodax fee={indodax_fee:,.0f} IDR  |  side={side}  maker={is_maker}")

        if price_diff_pct < 0.5 and qty_diff_pct < 2.0:
            print(f"  [OK] Data cocok")
            total_ok += 1
        else:
            print(f"  [DIFF] Ada perbedaan signifikan!")
            total_diff += 1

    # ── CHECK 3: Orphan Trades — ada di Indodax tapi tidak di DB ─────────────
    print(f"\n{'─'*70}")
    print("CHECK 3: Orphan Trades di Indodax (tidak ada di DB lokal)")
    print(f"{'─'*70}")

    # Kumpulkan semua waktu trade lokal per pair
    local_times_by_pair = {}
    for t in local_history:
        p = t['pair']
        ts_ms = int(datetime.fromisoformat(t['created_at']).replace(tzinfo=timezone.utc).timestamp() * 1000)
        local_times_by_pair.setdefault(p, []).append(ts_ms)

    orphan_count = 0
    for pair, fills in indodax_trades.items():
        local_times = local_times_by_pair.get(pair, [])
        for fill in fills:
            fill_ts  = int(fill.get('time', 0))
            is_buyer = fill.get('isBuyer', False)
            side     = 'BUY' if is_buyer else 'SELL'
            # Hanya SELL yang perlu direkonsiliasi (BUY = entry, SELL = exit)
            if side != 'SELL':
                continue
            # Cek apakah ada trade lokal dalam ±10 menit
            matched = any(abs(fill_ts - lt) < 600_000 for lt in local_times)
            if not matched:
                price   = fill.get('price', '?')
                qty     = fill.get('qty', '?')
                fee     = fill.get('commission', '?')
                fill_dt = datetime.fromtimestamp(fill_ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
                print(f"  [ORPHAN SELL] {pair} | price={price} qty={qty} fee={fee} IDR | {fill_dt}")
                orphan_count += 1

    if orphan_count == 0:
        print("  [OK] Tidak ada orphan SELL trade — semua sudah tercatat di DB")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Open positions       : {len(positions)}")
    print(f"  Trade history (7d)   : {len(local_history)}")
    print(f"  Trade history match  : {total_ok} OK, {total_miss} not found (>7d), {total_diff} DIFF")
    print(f"  Orphan SELL trades   : {orphan_count}")
    if total_diff > 0 or orphan_count > 0:
        print(f"\n  [!] Ada ketidakcocokan yang perlu ditinjau.")
    else:
        print(f"\n  [ALL CLEAR] Data DB lokal konsisten dengan Indodax.")
    print()


if __name__ == '__main__':
    main()
