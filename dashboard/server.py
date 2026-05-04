import eventlet
eventlet.monkey_patch()
import os
import sys
import time
import datetime
import logging
import urllib.request
import xml.etree.ElementTree as ET
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import sqlite3
import json
from flask import Flask, jsonify, send_from_directory, request, session, redirect, url_for, abort, make_response, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import secrets
from functools import wraps
from app.infrastructure.database import db
from app.infrastructure.ws_market import MarketDataWS
from app.domain.technicals import get_technical_summary, derive_trade_signal
from app.infrastructure.llm_worker import LLMWorker
import ccxt
import config as cfg
import utils.logger # Ensure central logging config is applied

# Exchange instance untuk public data (OHLCV)
exchange = ccxt.indodax()

app = Flask(__name__, static_folder='static/dist', static_url_path='')

# ── Pentest Fix: Secure Session Configuration ──
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", secrets.token_hex(32))
WS_SALT = os.getenv("WS_SALT", "algovibe_salt_2024") # Salt rahasia untuk handshake

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False, # Set to True if using HTTPS
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=2)
)

# ── Pentest Fix: Global CSRF Protection ──
# Exclude SocketIO from CSRF as it has its own mechanism
# but keep it for normal API routes
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=10, ping_interval=5)

# --- [Manual CSRF Protection] ---
def generate_csrf():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

@app.before_request
def validate_csrf():
    # Only validate CSRF on POST/PUT/DELETE
    if request.method in ["POST", "PUT", "DELETE"]:
        # Skip CSRF for login if needed, but better keep it
        # Special case for socket.io path (handled by socketio)
        if request.path.startswith('/socket.io'):
            return
            
        token = request.headers.get("X-CSRFToken")
        if not token or token != session.get("_csrf_token"):
            logging.warning(f"🚫 CSRF Validation Failed for {request.path}")
            abort(403)

# Provide CSRF token for the frontend
def generate_view_token():
    """Generate token handshake untuk view-only mode."""
    import hashlib
    # Token berbasis jam (berlaku sekitar 1 jam)
    ts = int(time.time() / 3600)
    token = hashlib.sha256(f"{WS_SALT}_{ts}".encode()).hexdigest()
    return token

@app.after_request
def set_csrf_cookie(response):
    response.set_cookie('csrf_token', generate_csrf(), samesite='Lax', httponly=False) # Frontend needs to read this
    return response

@socketio.on('connect')
def handle_connect(auth=None):
    # Handshake keamanan: Cek session atau view-token
    client_token = auth.get('token') if auth else None
    valid_token = generate_view_token()
    
    is_authenticated = 'user_id' in session
    is_token_valid = (client_token == valid_token)

    if not is_authenticated and not is_token_valid:
        logging.warning(f"🚫 Unauthorized WS connection (Token: {client_token[:8] if client_token else 'None'})")
        return False # Tolak koneksi
    
    # Semua orang yang lolos masuk ke room public
    join_room('public')
    
    if is_authenticated:
        user_id = session.get('user_id')
        username = session.get('username')
        join_room(f"user_{user_id}")
        logging.info(f"🔌 Authenticated User {username} connected")
    else:
        logging.info("🔌 Guest User connected via Valid Token (Public Mode)")
    return True

@socketio.on('disconnect')
def handle_disconnect():
    username = session.get('username', 'Unknown')
    logging.info(f"📡 User {username} disconnected from WS")
# ── Background Throttler for UI Performance ──
def ui_emitter_thread():
    """Mengirim update ke UI secara berkala (Throttling) agar HP user tidak panas."""
    while True:
        socketio.sleep(0.5) # Emit setiap 500ms
        pass

# socketio.start_background_task(ui_emitter_thread)
# ── Authentication Middleware ──
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized", "login_required": True}), 401
        return f(*args, **kwargs)
    return decorated_function

# ── Default User Initialization ──
try:
    if not db.get_user('admin'):
        db.create_user('admin', 'admin123', os.getenv('INDODAX_API_KEY', ''), os.getenv('INDODAX_API_SECRET', ''))
        logging.info("✅ Default user 'admin' created (Pass: admin123). PLEASE CHANGE THIS!")
except Exception as e:
    logging.error(f"Error initializing default user: {e}")

from config import DB_PATH_LIVE, DB_PATH_DRYRUN
# NOTE: cfg.DRY_RUN is imported dynamically inside functions to always read current value

# Default pair — akan diperbarui saat user memilih koin di dashboard
CURRENT_WATCH_PAIR = "btcidr"

# Inisialisasi Market WS untuk dashboard agar data harga realtime (detik)
mws = MarketDataWS()
mws.start()
mws.subscribe_market_trades(CURRENT_WATCH_PAIR)

_start_time = time.time()

from app.infrastructure.migrations import run_migrations

# Set untuk track DB path yang sudah dimigrasi di proses ini
# Prevents redundant migration runs on every request
_MIGRATED_DBS: set = set()

def get_db_connection():
    """Membuka koneksi SQLite ke DB aktif (live/dryrun).
    
    Memastikan migrasi skema sudah dijalankan untuk path ini sebelum
    koneksi diberikan ke caller — mencegah 'no such table' secara permanen
    meskipun dashboard start sebelum bot selesai inisialisasi.
    """
    path = DB_PATH_DRYRUN if cfg.DRY_RUN else DB_PATH_LIVE
    if path not in _MIGRATED_DBS:
        run_migrations(path)
        _MIGRATED_DBS.add(path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

# [Project Predator] Sync Trade Streams for Open Positions on Startup
try:
    _conn = get_db_connection()
    _open_pos = _conn.execute("SELECT pair FROM positions").fetchall()
    for _p in _open_pos:
        mws.subscribe_market_trades(_p['pair'])
    _conn.close()
    print(f"✅ Subscribed to {len(_open_pos)} open position trade streams.")
except Exception as _e:
    print(f"⚠️ Failed to sync initial trade streams: {_e}")

_REAL_BALANCE_CACHE = {"free": None, "used": None, "total": None, "last_check": 0, "coins": {}}
_REST_TICKER_CACHE = {"data": {}, "last_check": 0}

def _fetch_all_tickers_rest():
    """Fetch all tickers from Indodax REST API as a fallback."""
    global _REST_TICKER_CACHE
    now = time.time()
    if (now - _REST_TICKER_CACHE["last_check"]) < 60:
        return _REST_TICKER_CACHE["data"]
    
    try:
        # Gunakan public API summaries via CCXT
        tickers = exchange.fetch_tickers()
        # Transform ke format yang mudah dibaca: {'axlidr': 2500, ...}
        transformed = {}
        for symbol, data in tickers.items():
            if '/IDR' in symbol:
                key = symbol.replace('/IDR', '').lower() + 'idr'
                transformed[key] = float(data.get('last') or 0)
        
        _REST_TICKER_CACHE["data"] = transformed
        _REST_TICKER_CACHE["last_check"] = now
        return transformed
    except Exception as e:
        logging.error(f"⚠️ Error fetching bulk tickers: {e}")
        return _REST_TICKER_CACHE["data"]

def _fetch_real_idr_balance_bundle():
    """
    Fetch real IDR balance bundle (free/used/total) + all non-IDR coins.
    [Senior Logic] PRIORITASKAN DATA DATABASE (yang didorong oleh bot via WebSocket).
    Hanya lakukan REST pull jika data DB kosong.
    """
    # 1. Coba baca dari Database (Realtime Sync dari Engine)
    try:
        db_bal = db.get_balances()
        if db_bal and 'IDR' in db_bal:
            idr_data = db_bal['IDR']
            coins = {}
            for asset, data in db_bal.items():
                if asset == 'IDR': continue
                total_val = float(data.get('total', 0))
                if total_val > 0: # Tampilkan semua koin > 0
                    coins[asset] = total_val
            
            return {
                "free": float(idr_data.get('free', 0)),
                "used": float(idr_data.get('used', 0)),
                "total": float(idr_data.get('total', 0)),
                "coins": coins,
                "source": "DATABASE_SYNC"
            }
    except Exception as e:
        logging.warning(f"⚠️ Failed to read balances from DB: {e}")

    # 2. Fallback ke REST API (Jika DB kosong atau gagal)
    try:
        user = db.get_user('admin')
        if not user or not user['api_key'] or not user['api_secret']:
            return None

        import ccxt
        ex = ccxt.indodax({
            'apiKey': user['api_key'],
            'secret': user['api_secret'],
            'enableRateLimit': True
        })
        balance = ex.fetch_balance()
        idr = balance.get('IDR', {})
        total_data = balance.get('total', {})
        
        coins = {}
        for coin, val in total_data.items():
            if coin == 'IDR' or coin == 'info': continue
            amount = float(val or 0)
            if amount > 0: # Tampilkan semua koin > 0
                coins[coin] = amount
                
        return {
            "free": float(idr.get('free', 0) or 0),
            "used": float(idr.get('used', 0) or 0),
            "total": float(idr.get('total', 0) or 0),
            "coins": coins,
        }
    except Exception as e:
        logging.error(f"❌ Error fetching real balance via REST fallback: {e}")
        return None

def _get_cached_real_balance_bundle():
    """
    [Senior No-Cache Policy] Selalu ambil data terbaru di mode Live.
    Membaca dari Database (sync dari bot) atau REST API secara langsung.
    """
    val = _fetch_real_idr_balance_bundle()
    if val is not None:
        return {
            "free": val.get("free", 0),
            "used": val.get("used", 0),
            "total": val.get("total", 0),
            "coins": val.get("coins", {}),
        }
    return None

def _get_cached_real_balance():
    """Backward-compatible helper returning only free IDR."""
    val = _get_cached_real_balance_bundle()
    return val.get("free") if val else None

def _get_coin_equity_idr(coins: dict, ws_tickers: dict) -> float:
    """
    Hitung total nilai IDR dari semua koin non-IDR yang dipegang di exchange.
    coins: {"AXL": 7.94, "DRX": 98.0, ...}
    ws_tickers: hasil mws.get_tickers()
    """
    total = 0.0
    rest_tickers = None
    
    for coin, amount in coins.items():
        pair_key = f"{coin.lower()}idr"
        
        # 1. Coba ambil dari WebSocket (Paling cepat)
        price = 0
        ticker = ws_tickers.get(pair_key, {})
        if ticker:
            price = float(ticker.get('last') or ticker.get('buy') or 0)
        
        # 2. Fallback ke Bulk REST Tickers (Jika koin tidak aktif di WS)
        if price <= 0:
            if rest_tickers is None:
                rest_tickers = _fetch_all_tickers_rest()
            price = rest_tickers.get(pair_key, 0)
                
        if price > 0:
            total += amount * price
            
    return total

def _get_pair_technicals(pair):
    """Ambil histori harga dari JSON blob dan hitung technical summary."""
    conn = get_db_connection()
    try:
        # 1. Price-based technicals (Diberikan sebagai JSON list di tabel ini)
        row = conn.execute(
            "SELECT history_json FROM ticker_history WHERE pair = ?",
            (pair,)
        ).fetchone()
        
        prices = []
        if row and row['history_json']:
            prices = json.loads(row['history_json'])
        
        tech = get_technical_summary(prices)
        
        # 2. Orderbook-based technicals (Imbalance & Spread)
        depth = conn.execute("SELECT price, vol, is_bid FROM market_depth WHERE pair = ?", (pair,)).fetchall()
        bids = [r for r in depth if r['is_bid'] == 1]
        asks = [r for r in depth if r['is_bid'] == 0]
        
        total_bid_vol = sum([r['vol'] for r in bids])
        total_ask_vol = sum([r['vol'] for r in asks])
        total_vol = total_bid_vol + total_ask_vol
        
        tech['buy_pressure'] = round((total_bid_vol / total_vol * 100), 1) if total_vol > 0 else 50.0
        
        if bids and asks:
            best_bid = max(r['price'] for r in bids)
            best_ask = min(r['price'] for r in asks)
            tech['spread'] = round(((best_ask - best_bid) / best_bid) * 100, 4)
        else:
            tech['spread'] = 0.0

        # Simpan last_price agar tidak ada query DB ulang di caller
        if prices:
            tech['last_price'] = float(prices[-1])
            
        return tech
    except Exception:
        return {"rsi": 50, "volatility": 0, "signal": "NEUTRAL", "slope": 0, "buy_pressure": 50, "spread": 0}
    finally:
        conn.close()

def _get_trade_history_summary():
    """Ambil ringkasan statistik seluruh riwayat transaksi untuk Executive Report."""
    import config as cfg
    try:
        conn = get_db_connection()
        dry_val = 1 if cfg.DRY_RUN else 0
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_idr <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(CASE WHEN pnl_idr > 0 THEN pnl_pct END), 3) as avg_win_pct,
                ROUND(AVG(CASE WHEN pnl_idr <= 0 THEN pnl_pct END), 3) as avg_loss_pct,
                ROUND(MAX(pnl_idr), 0) as best_trade,
                ROUND(MIN(pnl_idr), 0) as worst_trade,
                ROUND(SUM(pnl_idr), 0) as total_pnl,
                ROUND(AVG(duration_seconds)/60.0, 1) as avg_hold_min
            FROM trade_history WHERE dry_run = ? AND reason != 'BACKTEST'
        """, (dry_val,)).fetchone()

        exits = conn.execute("""
            SELECT reason, COUNT(*) as cnt, ROUND(AVG(pnl_pct), 2) as avg_pct
            FROM trade_history WHERE dry_run = ? AND reason != 'BACKTEST'
            GROUP BY reason ORDER BY cnt DESC LIMIT 6
        """, (dry_val,)).fetchall()

        top_pairs = conn.execute("""
            SELECT pair, COUNT(*) as trades, ROUND(SUM(pnl_idr), 0) as total_pnl
            FROM trade_history WHERE dry_run = ? AND reason != 'BACKTEST'
            GROUP BY pair ORDER BY total_pnl DESC LIMIT 5
        """, (dry_val,)).fetchall()

        conn.close()

        total = row['total'] or 0
        wins  = row['wins'] or 0
        losses = row['losses'] or 0
        win_rate = round(wins / total * 100, 1) if total else 0
        avg_win  = row['avg_win_pct'] or 0
        avg_loss = row['avg_loss_pct'] or 0
        rr = round(abs(avg_win / avg_loss), 2) if avg_loss else 0

        exit_lines = " | ".join([
            f"{e['reason']}({e['cnt']}x, avg {e['avg_pct']:+.2f}%)" for e in exits
        ])
        pair_lines = " | ".join([
            f"{p['pair'].replace('idr','').upper()}({p['trades']}tx, {p['total_pnl']:+,.0f}IDR)" for p in top_pairs
        ])

        return {
            "total": total, "wins": wins, "losses": losses, "win_rate": win_rate,
            "avg_win_pct": avg_win, "avg_loss_pct": avg_loss, "rr": rr,
            "best_trade": row['best_trade'] or 0, "worst_trade": row['worst_trade'] or 0,
            "total_pnl": row['total_pnl'] or 0, "avg_hold_min": row['avg_hold_min'] or 0,
            "exit_breakdown": exit_lines, "top_pairs": pair_lines,
        }
    except Exception as e:
        logging.warning(f"[Equity Report] Trade history summary error: {e}")
        return {}


_last_equity_llm_ts = 0.0  # In-process cooldown — cegah flooding LLM dari push_data_loop

def _generate_equity_report_llm(stats):
    """Kirim prompt ke LLM untuk membuat Executive Report berdasarkan seluruh riwayat transaksi."""
    global _last_equity_llm_ts
    if time.time() - _last_equity_llm_ts < 600:  # 10-menit cooldown in-process
        return
    worker = LLMWorker.get_instance()
    if not worker.is_active:
        return

    hist = _get_trade_history_summary()
    if not hist or hist.get('total', 0) < 3:
        return  # Belum cukup data untuk laporan bermakna

    rr_note = "BAIK (>1)" if hist['rr'] >= 1.0 else f"BURUK (<1, perlu perbaikan)"

    prompt = (
        "Kamu adalah Quant Analyst senior. Buat 'Executive Performance Report' berdasarkan data historis trading bot berikut.\n\n"
        f"=== RINGKASAN SELURUH RIWAYAT TRANSAKSI ===\n"
        f"Total Trade     : {hist['total']} (W:{hist['wins']} / L:{hist['losses']})\n"
        f"Win Rate        : {hist['win_rate']}%\n"
        f"Total PnL       : {hist['total_pnl']:+,.0f} IDR\n"
        f"Avg Win         : +{hist['avg_win_pct']:.2f}%  |  Avg Loss: {hist['avg_loss_pct']:.2f}%\n"
        f"Risk/Reward     : {hist['rr']:.2f}x — {rr_note}\n"
        f"Best Trade      : +{hist['best_trade']:,.0f} IDR\n"
        f"Worst Trade     : {hist['worst_trade']:,.0f} IDR\n"
        f"Avg Hold Time   : {hist['avg_hold_min']} menit\n"
        f"Exit Breakdown  : {hist['exit_breakdown']}\n"
        f"Top Pairs       : {hist['top_pairs']}\n\n"
        f"=== STATUS SESI SAAT INI ===\n"
        f"PnL Sesi        : {stats['pnl']:+,.0f} IDR\n"
        f"Unrealized      : {stats['unrealized']:+,.0f} IDR\n"
        f"Posisi Aktif    : {stats['open_count']}\n"
        f"Regime Pasar    : {stats['regime']}\n"
        f"Intensitas      : {stats['intensity']:.1f} tx/jam\n\n"
        "Tulis laporan dalam 4 section singkat: "
        "[VERDICT] status overall profit/loss dan win rate, "
        "[DIAGNOSIS] apa penyebab utama performa ini (RR ratio, fee drag, exit pattern), "
        "[EDGE] kekuatan bot yang harus dipertahankan, "
        "[AKSI] satu rekomendasi taktis spesifik yang harus dilakukan sekarang. "
        "Gunakan bahasa Indonesia profesional, padat, dan tajam. Tidak perlu panjang."
    )
    logging.info(f"🧠 [ORACLE EQUITY REPORT PROMPT]\n{prompt}")
    _last_equity_llm_ts = time.time()
    worker.query_async("equity_report", prompt)

def _generate_equity_reasoning(pnl, unrealized, win_rate, trades, top_pair=None, best_trade=0, regime='CHOPPY'):
    """Memberikan Executive Report performa portofolio secara terstruktur."""
    total_equity = pnl + unrealized
    status = "OPTIMAL" if total_equity >= 0 else "CAUTION"
    
    # Action Plan Logic
    action_plan = "NEUTRAL"
    if regime == 'BULL' and win_rate > 50:
        action_plan = "EXPANSION: AI akan meningkatkan alokasi pada sinyal Oracle > 85%."
    elif regime == 'BEAR' or win_rate < 40:
        action_plan = "CONTRACTION: Membatasi posisi baru dan memperketat jaring pengaman (SL)."
    elif regime == 'CHOPPY':
        action_plan = "SCALPING ONLY: Eksekusi jangka pendek, hindari risiko holding semalam."
    else:
        action_plan = "MAINTENANCE: Menunggu konfirmasi volume untuk langkah selanjutnya."

    # 1. Market Context
    report = f"REPORT STATUS: {status}\n"
    report += f"NEXT ACTION: {action_plan}\n\n"
    report += f"CONTEXT: Market saat ini berada dalam rezim {regime}. "
    if regime == 'BULL':
        report += "Kondisi mendukung ekspansi aset. "
    elif regime == 'BEAR':
        report += "Volatilitas tinggi, tekanan jual mendominasi. "
    else:
        report += "Konsolidasi harga terdeteksi, minim tren kuat. "
        
    # 2. Performance Audit (Overall)
    if trades > 0:
        report += f"\nAUDIT KESELURUHAN: Terdeteksi {trades} keterlibatan pasar (termasuk posisi aktif) dengan akurasi selesai {win_rate}%. "
        if top_pair:
            report += f"Aset {top_pair.replace('idr','').upper()} merupakan kontributor profit tertinggi saat ini. "
    else:
        report += f"\nAUDIT: Belum ada transaksi riwayat yang tercatat dalam database (0 eksekusi). "
        report += "Oracle sedang menunggu data transaksi pertama untuk kalibrasi performa. "
    
    if unrealized < 0 and abs(unrealized) > 150000:
        report += "Terdapat floating loss signifikan yang memerlukan pengawasan ketat. "

    # 3. Strategy Narrative
    if trades == 0:
        report += "Saat ini bot memprioritaskan keamanan modal dengan filter likuiditas dan probabilitas yang sangat ketat."
    elif total_equity > 0:
        report += "Strategi saat ini mempertahankan profit buffer untuk mitigasi risiko mendadak."
    else:
        report += "Bot berfokus pada efisiensi keluar posisi untuk meminimalkan degradasi ekuitas."

    return report, action_plan

def _generate_technical_reasoning(pair, tech, last_price=None):
    """Buat narasi nalar kuantitatif. last_price diteruskan agar tidak ada query DB ulang."""
    rsi   = tech.get('rsi', 50)
    vol   = tech.get('volatility', 0)
    bp    = tech.get('buy_pressure', 50)
    slope = tech.get('slope', 0)
    spread = tech.get('spread', 0)

    analysis = []

    # 1. Analisa Momentum & RSI
    if rsi < 30:
        if slope > -0.001:
            analysis.append(f"Terdeteksi 'Bullish Divergence' minor; RSI {rsi} menunjukkan jenuh jual sementara momentum penurunan mulai melandai.")
        else:
            analysis.append(f"Harga berada dalam tekanan 'Oversold' yang kuat (RSI {rsi}). Penurunan masih memiliki momentum negatif yang tajam.")
    elif rsi > 70:
        analysis.append(f"Kondisi 'Overbought' (RSI {rsi}) terdeteksi. Risiko 'Trend Exhaustion' meningkat, waspada pembalikan arah mendadak.")
    else:
        analysis.append(f"RSI berada di zona netral ({rsi}). Tidak ada indikasi deviasi harga yang ekstrim saat ini.")

    # 2. Analisa Liquidity & Orderbook (Imbalance)
    if bp < 40:
        if slope < 0:
            analysis.append(f"Dominasi 'Sell-Side' ({100-bp:.1f}%) memperkuat tren turun. Likuiditas pembeli terlihat menipis di area ini.")
        else:
            analysis.append(f"Divergence terdeteksi: Harga mencoba naik namun tertahan 'Wall' penjual yang dominan ({100-bp:.1f}%).")
    elif bp > 60:
        analysis.append(f"Terjadi 'Absorption' oleh pembeli; akumulasi di sisi Bid ({bp:.1f}%) memberikan bantalan harga yang kuat.")

    # 3. Analisa Volatilitas & Estimasi Target (gunakan last_price yang sudah ada)
    if last_price and last_price > 0:
        target_pct = max(0.2, vol * 0.5)
        up_target  = last_price * (1 + target_pct / 100)
        down_target = last_price * (1 - target_pct / 100)
        # ETA: jarak target / kecepatan per menit (vol per 5 menit ÷ 5)
        eta_mins = max(2, int(target_pct / (max(0.01, vol) / 5)))
        if slope > 0:
            analysis.append(f"Target kenaikan teknikal terdekat di Rp {up_target:,.2f} (+{target_pct:.2f}%). Estimasi waktu capai: ~{eta_mins} menit.")
        else:
            analysis.append(f"Waspada koreksi teknikal ke area Rp {down_target:,.2f} (-{target_pct:.2f}%). Estimasi waktu capai: ~{eta_mins} menit.")

    if vol > 1.2:
        analysis.append(f"Volatilitas tinggi ({vol:.2f}%) menyarankan penggunaan Risk/Reward ratio 1:1.5.")
    if spread > 0.08:
        analysis.append(f"Spread melebar ({spread:.3f}%); pertimbangkan harga limit untuk eksekusi.")

    return " ".join(analysis) if analysis else "Fase konsolidasi; belum ada target pergerakan signifikan terdeteksi."

@app.route('/mobile.html')
def mobile_dashboard():
    token = generate_view_token()
    return render_template('mobile.html', view_token=token)

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")


@app.route('/api/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return jsonify({"status": "Login endpoint ready. Use POST to authenticate."})
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = db.verify_user(username, password)
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        return jsonify({"success": True, "user": {"username": user['username'], "role": user['role']}})
    
    return jsonify({"success": False, "message": "Invalid username or password"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/api/check-auth')
def check_auth():
    if 'user_id' in session:
        return jsonify({"authenticated": True, "username": session.get('username')})
    return jsonify({"authenticated": False})

@app.route('/api/view-token')
def get_view_token():
    """Expose short-lived token for guest Socket.IO handshake."""
    return jsonify({"token": generate_view_token()})

@app.route('/api/user/settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    username = session.get('username')
    if request.method == 'GET':
        user = db.get_user(username)
        if not user: return jsonify({"error": "User not found"}), 404
        # Mask keys for security
        return jsonify({
            "api_key": user['api_key'][:4] + "..." if user['api_key'] else "",
            "has_secret": True if user['api_secret'] else False
        })
    
    data = request.get_json()
    api_key = data.get('api_key')
    api_secret = data.get('api_secret')
    
    if db.update_user_keys(username, api_key, api_secret):
        return jsonify({"success": True, "message": "Settings updated. Restarting engines..."})
    return jsonify({"success": False, "message": "Failed to update settings"}), 500

@app.route('/api/stats')
def get_stats():
    conn = get_db_connection()
    try:
        import config as cfg
        # PnL & Daily Trades
        stats = conn.execute("SELECT * FROM bot_state").fetchall()
        stats_dict = {row['key']: row['value'] for row in stats}

        # Open Positions
        positions = conn.execute("SELECT * FROM positions").fetchall()

        # 1. LIVE STATS (Real Money)
        live_stats = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE dry_run = 0"
        ).fetchone()
        
        # 2. DRY RUN STATS (Live Simulation)
        dry_stats = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE dry_run = 1 AND reason != 'BACKTEST'"
        ).fetchone()

        # 3. BACKTEST STATS (AI Knowledge / DNA)
        dna_stats = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE reason = 'BACKTEST'"
        ).fetchone()

        win_rate = (live_stats['wins'] / live_stats['trades'] * 100) if (live_stats and live_stats['trades'] > 0) else 0
        dry_win_rate = (dry_stats['wins'] / dry_stats['trades'] * 100) if (dry_stats and dry_stats['trades'] > 0) else 0
        dna_win_rate = (dna_stats['wins'] / dna_stats['trades'] * 100) if (dna_stats and dna_stats['trades'] > 0) else 0

        # Sector Sentiment
        sectors = db.get_all_latest_sector_sentiment()

        # Unrealized PnL estimasi dari ticker_history last price
        unrealized_pnl = 0.0
        for pos in positions:
            try:
                pair = pos['pair']
                entry_price = float(pos['entry_price'] or 0)
                amount_crypto = float(pos['amount_crypto'] or 0)
                # Ambil harga terakhir dari ticker_history
                row = conn.execute(
                    "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
                ).fetchone()
                if row and row['history_json']:
                    history = json.loads(row['history_json'])
                    if history and entry_price > 0:
                        last_price = float(history[-1])
                        unrealized_pnl += (last_price - entry_price) * amount_crypto
            except Exception:
                continue

        tech_data = _get_pair_technicals(CURRENT_WATCH_PAIR)
        ai_reasoning = db.get_state(f"llm_reasoning_{CURRENT_WATCH_PAIR}")
        # Ambil last_price dari tech_data jika tersedia (hindari query DB ulang)
        _last_price = tech_data.get('last_price')
        combined_reasoning = ai_reasoning if ai_reasoning else _generate_technical_reasoning(CURRENT_WATCH_PAIR, tech_data, _last_price)

        import config as cfg
        
        # 4. Kalsulasi Saldo & Ekuitas (Predator Metrics)
        # Saldo Tersedia = Modal Awal + Total Profit Realisasi - Modal yang sedang terpakai
        total_used_idr = sum([float(pos['amount_idr'] or 0) for pos in positions])
        realized_pnl_dry = float(dry_stats['total_pnl'] or 0)
        realized_pnl_live = float(live_stats['total_pnl'] or 0)

        if cfg.DRY_RUN:
            available_idr = cfg.INITIAL_CAPITAL + realized_pnl_dry - total_used_idr
            idr_used = total_used_idr
            active_pnl = realized_pnl_dry
            active_win_rate = dry_win_rate
            # Dry Run Equity = Sisa Dana + Modal Terpakai + PnL Mengambang
            total_equity = available_idr + total_used_idr + unrealized_pnl
        else:
            # LIVE MODE: Meniru 'Estimated Asset Value' Indodax
            real_bal = _get_cached_real_balance_bundle()
            ws_tickers_for_equity = mws.get_tickers()
            if real_bal is not None:
                available_idr = float(real_bal.get("free", 0) or 0)
                idr_used = float(real_bal.get("used", 0) or 0)
                idr_total = available_idr + idr_used
                
                coin_equity_idr = _get_coin_equity_idr(
                    real_bal.get("coins", {}), ws_tickers_for_equity
                )
                
                # [Senior Fix] Total Equity LIVE = (Free IDR + Used IDR) + Market Value Seluruh Koin
                total_equity = idr_total + coin_equity_idr
                
                active_pnl = realized_pnl_live
                active_win_rate = win_rate
            else:
                available_idr = float(_REAL_BALANCE_CACHE.get("free") or 0)
                idr_used = float(_REAL_BALANCE_CACHE.get("used") or 0)
                idr_total = available_idr + idr_used
                coin_equity_idr = _get_coin_equity_idr(
                    _REAL_BALANCE_CACHE.get("coins", {}), ws_tickers_for_equity
                )
                total_equity = idr_total + coin_equity_idr
                active_pnl = realized_pnl_live
                active_win_rate = win_rate
        utilization_pct = (total_used_idr / (available_idr + total_used_idr) * 100) if (available_idr + total_used_idr) > 0 else 0
        
        # Performance definitions
        perf = db.get_daily_stats()
        import config as cfg
        quant_audit = db.get_quant_audit_report(window_hours=24, dry_run=1 if cfg.DRY_RUN else 0)
        
        top_pair_row = conn.execute(
            "SELECT pair, SUM(pnl_idr) as total_pnl FROM trade_history "
            "WHERE created_at > date('now') AND reason != 'BACKTEST' "
            "GROUP BY pair ORDER BY total_pnl DESC LIMIT 1"
        ).fetchone()
        top_pair = top_pair_row['pair'] if top_pair_row and top_pair_row['total_pnl'] > 0 else None

        best_trade_row = conn.execute(
            "SELECT MAX(pnl_idr) FROM trade_history "
            "WHERE created_at > date('now') AND reason != 'BACKTEST'"
        ).fetchone()
        best_trade = float(best_trade_row[0] or 0)

        # Hitung jumlah posisi aktif sesuai mode saat ini (live/dryrun), bukan total semua mode
        _dr_val = 1 if cfg.DRY_RUN else 0
        active_positions_count = len([p for p in positions if int(p['dry_run'] or 0) == _dr_val])

        return jsonify({
            "all_time_pnl": float(live_stats['total_pnl'] or 0) if live_stats else 0,
            "all_time_pnl_dry": realized_pnl_dry,
            "available_idr": round(available_idr, 0),
            "idr_used": round(idr_used, 0),
            "total_equity": round(total_equity, 0),
            "utilization": round(utilization_pct, 1),
            "daily_pnl": active_pnl,
            "win_rate": round(active_win_rate, 2),
            "dry_win_rate": round(dry_win_rate, 2),
            "dry_trades_count": dry_stats['trades'] if dry_stats else 0,
            "total_trades_live": live_stats['trades'] if live_stats else 0,
            "live_pnl": float(live_stats['total_pnl'] or 0) if live_stats else 0,
            "live_win_rate": round(win_rate, 2),
            "live_trades_count": live_stats['trades'] if live_stats else 0,
            "backtest_pnl": float(dna_stats['total_pnl'] or 0) if dna_stats else 0,
            "backtest_win_rate": round(dna_win_rate, 2),
            "backtest_trades_count": dna_stats['trades'] if dna_stats else 0,
            "active_positions": active_positions_count,
            "unrealized_pnl": round(unrealized_pnl, 0),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', ''),
            "latest_llm_insight": stats_dict.get('latest_llm_insight', ''),
            "hero_ai_insight": stats_dict.get('hero_ai_insight', 'Waiting for AI analysis...'),
            "hero_ai_insights": [{"message": r[0], "created_at": r[1]} for r in conn.execute("SELECT message, created_at FROM system_events WHERE type='AI_INSIGHT' ORDER BY created_at DESC LIMIT 5").fetchall()] or [{"message": stats_dict.get('hero_ai_insight', 'Waiting for AI analysis...'), "created_at": datetime.datetime.utcnow().isoformat()}],
            "active_reasoning": combined_reasoning,
            "technicals": tech_data,
            "sectors": sectors,
            "alpha_contributor": top_pair,
            "best_trade": best_trade,
            "intensity": round(perf['total_trades'] / max(1, (time.time() - _start_time) / 3600), 1),
            "weights": db.get_all_weights(),
            "quant_audit": quant_audit,
            "market_sentiment_index": int(stats_dict.get('market_sentiment_index', 50)),
            "backfill_status": stats_dict.get('backfill_status', 'N/A'),
            "history_since": conn.execute("SELECT strftime('%d/%m %H:%M', MIN(updated_at)) FROM ticker_history").fetchone()[0] or '--/-- --:--',
            "new_listings": db.get_new_listings(),
            "dry_run": bool(cfg.DRY_RUN),
            "dry_run_loose_gates": bool(cfg.DRY_RUN_LOOSE_GATES)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/positions')
def get_positions():
    """Dedicated endpoint untuk open positions dengan unrealized PnL."""
    conn = get_db_connection()
    try:
        positions = conn.execute(
            "SELECT pair, entry_price, amount_crypto, amount_idr, status, timestamp FROM positions"
        ).fetchall()

        results = []
        now = time.time()
        for pos in positions:
            pair = pos['pair']
            entry_price = float(pos['entry_price'] or 0)
            amount_crypto = float(pos['amount_crypto'] or 0)
            amount_idr = float(pos['amount_idr'] or 0)

            # Harga live dari ticker_history
            last_price = entry_price
            pnl_idr = 0.0
            pnl_pct = 0.0
            try:
                row = conn.execute(
                    "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
                ).fetchone()
                if row and row['history_json']:
                    history = json.loads(row['history_json'])
                    if history and entry_price > 0:
                        last_price = float(history[-1])
                        pnl_idr = (last_price - entry_price) * amount_crypto
                        pnl_pct = ((last_price - entry_price) / entry_price) * 100
            except Exception:
                pass

            fee_buy  = amount_idr * cfg.FEE_BUY_TAKER
            fee_sell = (last_price * amount_crypto) * cfg.FEE_SELL_TAKER
            pnl_net_idr = pnl_idr - fee_buy - fee_sell

            # Durasi posisi (gunakan datetime yang sudah di-import di atas)
            duration_sec = 0
            try:
                ts = pos['timestamp']
                if ts:
                    opened_at = datetime.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                    duration_sec = int((datetime.datetime.now(datetime.timezone.utc) - opened_at).total_seconds())
            except Exception:
                pass

            results.append({
                "pair": pair,
                "entry_price": entry_price,
                "last_price": last_price,
                "amount_crypto": amount_crypto,
                "amount_idr": amount_idr,
                "pnl_idr": round(pnl_idr, 0),
                "pnl_net_idr": round(pnl_net_idr, 0),
                "pnl_pct": round(pnl_pct, 3),
                "status": pos['status'],
                "duration_sec": duration_sec,
                "opened_at": str(pos['timestamp'])
            })

        return jsonify(results)
    except Exception as e:
        import traceback
        print("POSITIONS ERROR:", traceback.format_exc())
        return jsonify([])
    finally:
        conn.close()

@app.route('/api/pnl-history/<tf>')
def get_pnl_history_api(tf):
    """
    Menyusun histori ekuitas (PnL kumulatif) dari database trade_history.
    """
    conn = get_db_connection()
    try:
        import config as cfg
        dr_val = 1 if cfg.DRY_RUN else 0
        
        tf_map = {
            '1h': '-1 hours',
            '24h': '-24 hours',
            '7d': '-7 days'
        }
        interval = tf_map.get(tf, '-24 hours')
        
        base_row = conn.execute(
            "SELECT SUM(pnl_idr) as total FROM trade_history WHERE created_at < datetime('now', ?) AND (dry_run = ? OR is_manual = 1) AND reason != 'BACKTEST'",
            (interval, dr_val)
        ).fetchone()
        current_pnl = float(base_row['total'] or 0.0)
        
        trades = conn.execute(
            "SELECT pnl_idr, created_at FROM trade_history WHERE created_at >= datetime('now', ?) AND (dry_run = ? OR is_manual = 1) AND reason != 'BACKTEST' ORDER BY created_at ASC",
            (interval, dr_val)
        ).fetchall()
        
        results = []
        results.append({
            "time": "Start",
            "pnl": round(current_pnl, 0)
        })
        
        for t in trades:
            current_pnl += float(t['pnl_idr'] or 0.0)
            ts = t['created_at']
            try:
                dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                time_str = dt.strftime("%M:%S") if tf == '1h' else dt.strftime("%H:%M")
            except:
                time_str = ts
            results.append({"time": time_str, "pnl": round(current_pnl, 0)})
        return jsonify(results)
    except Exception as e:
        import traceback
        print(f"PNL HISTORY ERROR: {e}")
        traceback.print_exc()
        return jsonify([])
    finally:
        conn.close()

@app.route('/api/debug/positions-summary')
def get_positions_summary():
    """Ringkasan cepat status posisi + unrealized PnL untuk audit exit flow."""
    import config as cfg
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT pair, entry_price, amount_crypto, status, dry_run FROM positions"
        ).fetchall()

        status_counts = {
            "OPEN": 0,
            "EXITING": 0,
            "PENDING_BUY": 0,
            "OTHER": 0,
        }
        mode_counts = {
            "live": 0,
            "dry_run": 0,
        }
        unrealized_by_status = {
            "OPEN": 0.0,
            "EXITING": 0.0,
            "PENDING_BUY": 0.0,
            "OTHER": 0.0,
        }

        for pos in rows:
            pair = str(pos['pair'] or '').lower()
            status = str(pos['status'] or 'OTHER').upper()
            if status not in status_counts:
                status = "OTHER"

            status_counts[status] += 1
            if int(pos['dry_run'] or 0) == 1:
                mode_counts['dry_run'] += 1
            else:
                mode_counts['live'] += 1

            entry_price = float(pos['entry_price'] or 0)
            amount_crypto = float(pos['amount_crypto'] or 0)
            if entry_price <= 0 or amount_crypto <= 0 or not pair:
                continue

            try:
                row = conn.execute(
                    "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
                ).fetchone()
                if row and row['history_json']:
                    history = json.loads(row['history_json'])
                    if history:
                        last_price = float(history[-1])
                        unrealized_by_status[status] += (last_price - entry_price) * amount_crypto
            except Exception:
                continue

        return jsonify({
            "runtime_mode": "DRY_RUN" if cfg.DRY_RUN else "LIVE",
            "positions_total": len(rows),
            "mode_counts": mode_counts,
            "status_counts": status_counts,
            "unrealized_by_status": {k: round(v, 0) for k, v in unrealized_by_status.items()},
            "unrealized_total": round(sum(unrealized_by_status.values()), 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/wallet/sync', methods=['POST'])
def sync_wallet():
    """
    Sinkronisasi posisi di DB dengan saldo wallet Indodax secara real-time.
    Mengadopsi koin yang ada di wallet tapi belum ada di DB (orphan adoption).
    Menghapus posisi ghost di DB jika koin sudah tidak ada di wallet (dust < 1e-8).
    """
    import config as cfg
    if cfg.DRY_RUN:
        return jsonify({"success": False, "message": "Wallet sync hanya tersedia di mode LIVE."}), 400

    try:
        user = db.get_user('admin')
        api_key = (user['api_key'] if user else None) or os.getenv('INDODAX_API_KEY', '')
        api_secret = (user['api_secret'] if user else None) or os.getenv('INDODAX_API_SECRET', '')

        if not api_key or not api_secret:
            return jsonify({"success": False, "message": "API key tidak tersedia."}), 400

        import ccxt as _ccxt
        ex = _ccxt.indodax({'apiKey': api_key, 'secret': api_secret, 'enableRateLimit': True})
        balance = ex.fetch_balance()
        total_bal = balance.get('total', {})

        conn = get_db_connection()
        try:
            current_positions = {row['pair']: dict(row) for row in conn.execute("SELECT * FROM positions").fetchall()}
            blacklist = set(r['pair'] for r in conn.execute("SELECT pair FROM blacklist").fetchall())
        finally:
            conn.close()

        adopted = []
        removed = []
        ws_tickers = mws.get_tickers()

        # 1. Ghost removal: posisi di DB tapi koin tidak ada di wallet
        for pair, pos in list(current_positions.items()):
            if int(pos.get('dry_run') or 0) != 0:
                continue
            coin = pair[:-3].upper()  # Semua pair format '{coin}idr'; slice -3 hapus suffix 'idr'
            amount_on_exchange = float(total_bal.get(coin, 0) or 0)
            if amount_on_exchange < 1e-8:
                db.delete_position(pair)
                current_positions.pop(pair, None)
                removed.append(pair)
                logging.info(f"[WalletSync] Ghost removed: {pair}")

        # 2. Orphan adoption: koin di wallet tapi belum ada di DB sebagai LIVE posisi
        from config import TP_PCT, SL_PCT
        for coin, amount in total_bal.items():
            if coin in ('IDR', 'info') or not amount or float(amount) <= 0:
                continue
            pair = f"{coin.lower()}idr"
            if pair in blacklist:
                continue
            live_pos = current_positions.get(pair)
            if live_pos and int(live_pos.get('dry_run') or 0) == 0:
                continue  # Sudah ada posisi LIVE untuk koin ini

            # Coba ambil harga dari WS dulu, fallback REST
            price = float(ws_tickers.get(pair, {}).get('last', 0))
            if price <= 0:
                try:
                    symbol = f"{coin}/IDR"
                    ticker = ex.fetch_ticker(symbol)
                    price = float(ticker.get('last', 0))
                except Exception:
                    pass

            amount = float(amount)
            value_idr = amount * price

            if value_idr < 1000:  # Abaikan dust (< Rp 1.000)
                continue

            adopt_data = {
                "entry_price": price,
                "amount": amount,
                "amount_crypto": amount,
                "amount_idr": value_idr,
                "timestamp": datetime.datetime.now().isoformat(),
                "dry_run": False,
                "status": "OPEN",
                "entry_order_id": f"WALLET-SYNC-{int(time.time())}",
                "exit_order_id": None,
                "target_tp_pct": TP_PCT,
                "target_sl_pct": SL_PCT,
                "reason_backup": "WALLET_SYNC"
            }
            db.upsert_position(pair, adopt_data)
            current_positions[pair] = adopt_data
            adopted.append({"pair": pair, "amount": amount, "value_idr": round(value_idr, 0)})
            logging.info(f"[WalletSync] Adopted orphan: {pair} ({amount} @ Rp {price:,.0f})")

        return jsonify({
            "success": True,
            "adopted": adopted,
            "removed": removed,
            "message": f"Sync selesai. Diadopsi: {len(adopted)} koin, dihapus: {len(removed)} posisi ghost."
        })
    except Exception as e:
        logging.error(f"[WalletSync] Error: {e}")
        return jsonify({"success": False, "message": "Wallet sync gagal. Cek log server untuk detail."}), 500


def get_market_context(pair):
    """
    Menyediakan konteks pasar lengkap untuk modal ORACLE:
    Orderbook, Technicals, dan Market Stats.
    """
    pair = pair.lower()
    tech = _get_pair_technicals(pair)
    
    # Ambil orderbook depth dari DB
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT price, vol, is_bid FROM market_depth WHERE pair = ? ORDER BY price DESC", 
            (pair,)
        ).fetchall()
        
        bids = [{"price": r['price'], "vol": r['vol']} for r in rows if r['is_bid'] == 1]
        asks = [{"price": r['price'], "vol": r['vol']} for r in rows if r['is_bid'] == 0]
        
        # Ambil 24h stats dari WS cache jika tersedia
        ticker_data = mws.get_tickers().get(pair, {})
        
        return jsonify({
            "pair": pair,
            "technicals": tech,
            "orderbook": {
                "bids": bids[:10], # Top 10
                "asks": asks[-10:] if asks else [] # Top 10 (asks are DESC, so take last 10 for best asks)
            },
            "stats": {
                "vol_24h": ticker_data.get('vol_idr', 0),
                "change_24h": ticker_data.get('change_24h', 0),
                "high_24h": ticker_data.get('high', 0),
                "low_24h": ticker_data.get('low', 0),
                "last": ticker_data.get('last', tech.get('last_price', 0))
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/analyze/<pair>')
def trigger_analysis(pair):
    """Trigger LLM analysis for a specific pair on-demand."""
    db.save_state("trigger_analysis_pair", pair.lower())
    return jsonify({"status": "requested", "pair": pair})

@app.route('/api/reasoning/<pair>')
def get_pair_reasoning(pair):
    """Ambil nalar AI/LLM terbaru untuk koin tertentu."""
    p = pair.lower().strip()
    reasoning = db.get_state(f"llm_reasoning_{p}")
    if not reasoning:
        # Fallback ke technical reasoning jika LLM belum ada
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (p,)).fetchone()
            prices = json.loads(row['history_json']) if row and row['history_json'] else []
            from app.domain.technicals import get_technical_summary
            tech = get_technical_summary(prices)
            reasoning = _generate_technical_reasoning(p, tech)
        except:
            reasoning = "Data intelijen sedang dikalkulasi..."
        finally:
            conn.close()
    return jsonify({"pair": p, "reasoning": reasoning})

@app.route('/api/exit/<pair>', methods=['POST'])
@login_required
def manual_exit(pair):
    """Force an emergency exit for a specific pair via dashboard."""
    db.save_state("trigger_exit_pair", pair.lower())
    return jsonify({"status": "exit_requested", "pair": pair})

@app.route('/api/trade/manual-exit', methods=['POST'])
@login_required
def trade_manual_exit():
    """Instant manual exit for an active position.

    Accepts JSON body: {"pair": "btcidr"} or form field 'pair'.
    Validates that an open position exists for the pair AND that Net PnL < 0.1%.
    Only positions below the 0.1% net profit threshold may be force-exited to prevent
    premature exits of profitable trades. Execution is always a MARKET (taker) sell.
    """
    data = request.get_json(silent=True) or {}
    pair = (data.get('pair') or request.form.get('pair') or '').lower().strip()
    if not pair:
        return jsonify({"error": "pair is required"}), 400

    pos = db.get_position(pair)
    if not pos:
        return jsonify({"error": f"No active position found for {pair}"}), 404

    # --- Guard: hanya izinkan exit saat Net PnL < 0.1% ---
    entry_price  = float(pos.get('entry_price') or 0)
    amount_crypto = float(pos.get('amount_crypto') or 0)
    amount_idr   = float(pos.get('amount_idr') or 0)

    # Ambil harga terakhir: WS > ticker_history > entry_price
    last_price = float(mws.get_tickers().get(pair, {}).get('last', 0))
    if last_price <= 0:
        try:
            conn = get_db_connection()
            row = conn.execute(
                "SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)
            ).fetchone()
            conn.close()
            if row and row['history_json']:
                history = json.loads(row['history_json'])
                if history:
                    last_price = float(history[-1])
        except Exception:
            pass
    if last_price <= 0:
        last_price = entry_price

    raw_pnl   = (last_price - entry_price) * amount_crypto
    fee_buy   = amount_idr * cfg.FEE_BUY_TAKER
    fee_sell  = (last_price * amount_crypto) * cfg.FEE_SELL_TAKER
    pnl_net   = raw_pnl - fee_buy - fee_sell

    # Hanya izinkan manual exit jika Net PnL < MANUAL_EXIT_MAX_NET_PCT (0.1%).
    # Ini mencegah exit prematur di posisi yang masih menguntungkan.
    pnl_net_pct = pnl_net / amount_idr if amount_idr > 0 else 0
    if pnl_net_pct >= cfg.MANUAL_EXIT_MAX_NET_PCT:
        return jsonify({
            "error": f"Manual exit hanya diizinkan saat Net PnL < {cfg.MANUAL_EXIT_MAX_NET_PCT*100:.1f}%. "
                     "Biarkan bot mengelola exit saat posisi untung.",
            "pnl_net_idr": round(pnl_net, 0),
            "pnl_net_pct": round(pnl_net_pct * 100, 4)
        }), 400

    db.save_state("trigger_exit_pair", pair)
    logging.info(f"[Manual Exit] Dashboard triggered instant market exit for {pair} (pnl_net={round(pnl_net,0)} IDR)")
    return jsonify({"status": "exit_requested", "pair": pair})

@app.route('/api/bot/toggle-dry-run', methods=['POST'])
def toggle_dry_run():
    """Toggle mode dengan policy auth:
    - DRY -> LIVE: wajib login
    - LIVE -> DRY: auto logout + clear session
    """
    import config as cfg
    # Baca nilai saat ini dari DB agar toggle akurat
    current_val = db.get_state("dry_run") == "true"
    new_val = not current_val

    # Hanya izinkan mode LIVE jika user terautentikasi
    if not new_val and 'user_id' not in session:
        return jsonify({
            "error": "Unauthorized",
            "login_required": True,
            "target_mode": "LIVE"
        }), 401

    db.save_state("dry_run", str(new_val).lower())
    cfg.clear_dry_run_cache()    # Hapus cache config
    db._mode_cache = None        # Hapus cache DatabaseRouter langsung
    logging.info(f"🔄 [DYNAMIC MODE] User changed mode to: {'DRY RUN' if new_val else 'LIVE'}")

    logged_out = False
    # Saat balik ke DRY, paksa logout dan bersihkan session
    if new_val:
        session.clear()
        logged_out = True

    # Push stats_update langsung agar frontend tidak tunggu 5s push loop
    try:
        socketio.emit('stats_update', _build_stats_payload())
        socketio.emit('trades_update', _build_trades_payload())
    except Exception as _e:
        logging.warning(f"[toggle] immediate push failed: {_e}")

    return jsonify({"success": True, "dry_run": new_val, "logged_out": logged_out})

@app.route('/api/bot/toggle-loose-gates', methods=['POST'])
def toggle_loose_gates():
    """Toggle mode LOOSE GATES untuk testing."""
    import config as cfg
    current_val = db.get_state("dry_run_loose_gates") == "true"
    new_val = not current_val
    
    db.save_state("dry_run_loose_gates", str(new_val).lower())
    cfg.clear_dry_run_cache() # Gunakan cache yang sama untuk sinkronisasi
    logging.info(f"🔄 [DYNAMIC GATES] User changed loose gates to: {new_val}")
    return jsonify({"success": True, "loose_gates": new_val})

@app.route('/api/bot-status')
def get_bot_status():
    """Status bot: online/offline/dry_run, uptime, last trade."""
    conn = get_db_connection()
    try:
        uptime_sec = int(time.time() - _start_time)

        # Cek apakah bot main berjalan dengan melihat last update di bot_state
        last_state = conn.execute(
            "SELECT updated_at FROM bot_state ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()

        bot_alive = False
        last_heartbeat = None
        if last_state:
            last_heartbeat = str(last_state['updated_at'])
            try:
                updated = datetime.datetime.fromisoformat(str(last_state['updated_at']))
                age_sec = (datetime.datetime.utcnow() - updated).total_seconds()
                bot_alive = age_sec < 60
            except Exception:
                pass

        # Last trade
        last_trade = conn.execute(
            "SELECT created_at FROM trade_history ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_trade_at = str(last_trade['created_at']) if last_trade else None

        # LLM Stats
        llm_stats = LLMWorker.get_instance().get_stats()

        return jsonify({
            "is_running": bot_alive,
            "dry_run": bool(cfg.DRY_RUN),
            "uptime_sec": uptime_sec,
            "last_heartbeat": last_heartbeat,
            "last_trade_at": last_trade_at,
            "llm": llm_stats
        })
    except Exception as e:
        return jsonify({"is_running": False, "dry_run": bool(cfg.DRY_RUN), "uptime_sec": 0})
    finally:
        conn.close()

@app.route('/api/history/<pair>')
def get_pair_history(pair):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)).fetchone()
        if row and row['history_json']:
            return jsonify(json.loads(row['history_json']))
        return jsonify([])
    except Exception:
        return jsonify([])
    finally:
        conn.close()

@app.route('/api/ohlcv/<pair>/<tf>')
def get_ohlcv(pair, tf):
    """Ambil data OHLCV dari bursa untuk timeframe tertentu."""
    try:
        # Convert pair dari btcidr ke BTC/IDR
        symbol = pair.upper()
        if symbol.endswith('IDR'):
            symbol = symbol[:-3] + '/IDR'
        
        # Mapping timeframe
        tf_map = {
            '5m': '5m',
            '15m': '15m',
            '1h': '1h',
            '24h': '1d'
        }
        target_tf = tf_map.get(tf, '1h')
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=target_tf, limit=100)
        # Format untuk AlgoChart: [{time, value}, ...]
        # AlgoChart area mode menggunakan property 'value'
        # AlgoChart candle mode menggunakan history array (diproses di frontend)
        # Kita kembalikan format yang konsisten
        return jsonify(ohlcv)
    except Exception as e:
        print(f"Error fetching OHLCV: {e}")
        return jsonify([])

@app.route('/api/trades/sync', methods=['POST'])
@login_required
def trigger_full_sync():
    """Trigger mass trade sync via bot background process."""
    # Simpan jumlah trade saat ini agar frontend bisa deteksi kapan sync selesai
    conn = get_db_connection()
    try:
        count_row = conn.execute("SELECT COUNT(*) as cnt FROM trade_history WHERE reason != 'BACKTEST'").fetchone()
        db.save_state("sync_pre_count", str(count_row['cnt'] if count_row else 0))
    except Exception:
        pass
    finally:
        conn.close()
    db.save_state("trigger_sync_all", "1")
    return jsonify({"success": True, "message": "Sync dimulai. Ledger akan otomatis terupdate setelah sync selesai."})

@app.route('/api/trades/sync/status')
def get_sync_status():
    """Cek apakah sync sudah selesai dengan membandingkan jumlah trade."""
    try:
        conn = get_db_connection()
        try:
            count_row = conn.execute("SELECT COUNT(*) as cnt FROM trade_history WHERE reason != 'BACKTEST'").fetchone()
            current_count = int(count_row['cnt'] if count_row else 0)
        finally:
            conn.close()
        pre_count = int(db.get_state("sync_pre_count") or 0)
        is_running = db.get_state("trigger_sync_all") == "1"
        new_trades = max(0, current_count - pre_count)
        return jsonify({
            "is_running": is_running,
            "pre_count": pre_count,
            "current_count": current_count,
            "new_trades": new_trades,
            "done": not is_running and current_count > 0
        })
    except Exception as e:
        return jsonify({"is_running": False, "new_trades": 0, "done": True})

@app.route('/api/trades')
def get_trades():
    return jsonify(_build_trades_payload())

@app.route('/api/radar')
def get_radar_data():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT pair, trend, prediction_prob, ch_5m, ch_15m, ch_1h, volatility, score, history_json FROM ticker_history"
        ).fetchall()
        results = []
        for row in rows:
            try:
                history = json.loads(row['history_json'] or '[]')
                if not history: continue
                last = float(history[-1])
                prev = float(history[-2]) if len(history) > 1 else last
                change = ((last - prev) / prev) * 100 if prev > 0 else 0
                
                results.append({
                    "pair": row['pair'],
                    "price": last,
                    "change": round(change, 2),
                    "ch_5m": float(row['ch_5m'] or 0),
                    "ch_15m": float(row['ch_15m'] or 0),
                    "ch_1h": float(row['ch_1h'] or 0),
                    "volatility": float(row['volatility'] or 0),
                    "score": float(row['score'] or 0),
                    "prediction": float(row['prediction_prob'] or 0.0),
                    "trend": row['trend'] or 'N/A'
                })
            except: continue
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/radar-audit')
def get_radar_audit():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT key, value, updated_at
            FROM bot_state
            WHERE key LIKE 'radar_reco_%'
               OR key LIKE 'gate_skip_%'
               OR key LIKE 'llm_veto_ts_%'
            """
        ).fetchall()

        now = datetime.datetime.now()
        window_sec = 24 * 3600
        per_pair = {}

        def _pair_state(pair_name):
            if pair_name not in per_pair:
                per_pair[pair_name] = {
                    'radar_reco': None,
                    'radar_ts': None,
                    'gate_msg': None,
                    'gate_ts': None,
                    'veto_ts': None,
                }
            return per_pair[pair_name]

        for r in rows:
            key = str(r['key'] or '')
            val = str(r['value'] or '')
            ts_text = str(r['updated_at'] or '')
            try:
                ts = datetime.datetime.fromisoformat(ts_text.replace(' ', 'T'))
            except Exception:
                continue

            if key.startswith('radar_reco_'):
                pair = key.replace('radar_reco_', '', 1)
                state = _pair_state(pair)
                state['radar_reco'] = val.upper()
                state['radar_ts'] = ts
            elif key.startswith('gate_skip_'):
                pair = key.replace('gate_skip_', '', 1)
                state = _pair_state(pair)
                state['gate_msg'] = val
                state['gate_ts'] = ts
            elif key.startswith('llm_veto_ts_'):
                pair = key.replace('llm_veto_ts_', '', 1)
                state = _pair_state(pair)
                try:
                    state['veto_ts'] = datetime.datetime.fromisoformat(val.replace(' ', 'T'))
                except Exception:
                    state['veto_ts'] = ts

        buy_signals = 0
        buy_blocked_total = 0
        buy_blocked_ai_veto = 0
        buy_blocked_gate = 0
        buy_tradable = 0

        for _, st in per_pair.items():
            radar_ts = st['radar_ts']
            if not radar_ts or (now - radar_ts).total_seconds() > window_sec:
                continue
            if st['radar_reco'] != 'BUY':
                continue

            buy_signals += 1

            gate_msg = str(st.get('gate_msg') or '')
            gate_ts = st.get('gate_ts')
            gate_fresh = bool(gate_ts and (now - gate_ts).total_seconds() <= window_sec)
            gate_up = gate_msg.upper()

            veto_ts = st.get('veto_ts')
            veto_fresh = bool(veto_ts and (now - veto_ts).total_seconds() <= window_sec)
            has_ai_veto = veto_fresh or ('LLM VETO' in gate_up)

            has_gate_block = gate_fresh and (
                gate_up.startswith('SKIP')
                or 'BLOCK' in gate_up
                or 'VETO' in gate_up
            )

            if has_ai_veto:
                buy_blocked_total += 1
                buy_blocked_ai_veto += 1
            elif has_gate_block:
                buy_blocked_total += 1
                buy_blocked_gate += 1
            else:
                buy_tradable += 1

        return jsonify({
            "window_hours": 24,
            "buy_signals_24h": buy_signals,
            "buy_blocked_total_24h": buy_blocked_total,
            "buy_blocked_ai_veto_24h": buy_blocked_ai_veto,
            "buy_blocked_gate_24h": buy_blocked_gate,
            "buy_tradable_24h": buy_tradable,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/logs')
def get_logs():
    conn = get_db_connection()
    try:
        # 1. Fetch AI Signals dari ticker_history
        ai_scans = conn.execute("""
            SELECT pair, prediction_prob, trend, updated_at as created_at
            FROM ticker_history
            WHERE prediction_prob > 0.4
            ORDER BY updated_at DESC LIMIT 10
        """).fetchall()

        # 2. Fetch Oracle Intelligence dari bot_state
        oracle_data = conn.execute("""
            SELECT key, value, updated_at as created_at
            FROM bot_state
            WHERE key IN ('market_regime', 'regime_leader', 'latest_whale', 'latest_fake_pump')
            ORDER BY updated_at DESC
        """).fetchall()

        # 3. Fetch Trade Analytics
        analytics = conn.execute("""
            SELECT pair, entry_timestamp as created_at, ai_prob, whale_boost, sector_sentiment
            FROM trade_analytics
            ORDER BY entry_timestamp DESC LIMIT 5
        """).fetchall()

        results = []

        # Process AI Signals
        for a in ai_scans:
            results.append({
                "created_at": a['created_at'],
                "pair": a['pair'].upper(),
                "side": "PREDICTION",
                "price": f"Prob {round(a['prediction_prob']*100)}%",
                "status": f"Trend: {a['trend']}",
                "level": "INFO"
            })

        # Process Oracle Data
        for o in oracle_data:
            icon = "🔮 ORACLE"
            level = "INFO"
            if o['key'] == 'market_regime':
                icon = "📡 REGIME"
                level = "INFO"
            if o['key'] == 'latest_whale':
                icon = "🐋 WHALE"
                level = "ALERT"
            if o['key'] == 'latest_fake_pump':
                icon = "⚠️ FAKE PUMP"
                level = "WARN"

            results.append({
                "created_at": o['created_at'],
                "pair": o['value'].split('|')[0].upper() if '|' in o['value'] else 'GLOBAL',
                "side": icon,
                "price": o['value'].split('|')[1] if '|' in o['value'] else o['value'],
                "status": "DETECTED",
                "level": level
            })

        # Process Analytics
        for d in analytics:
            results.append({
                "created_at": d['created_at'],
                "pair": d['pair'].upper(),
                "side": "ANALYSIS",
                "price": f"Whale: {d['whale_boost']}x",
                "status": f"Sector: {round(d['sector_sentiment'],1)}%",
                "level": "INFO"
            })

        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return jsonify(results[:25])
    except Exception as e:
        import traceback
        print("LOG ERROR:", traceback.format_exc())
        return jsonify([])
    finally:
        conn.close()

# ── Crypto News Feed ──────────────────────────────────────────────────────────
_news_cache = {"items": [], "fetched_at": 0}
_NEWS_TTL = 300  # seconds

_NEWS_FEEDS = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph",  "https://cointelegraph.com/rss"),
    ("Decrypt",        "https://decrypt.co/feed"),
]

def _fetch_rss_titles(url: str, source: str, limit: int = 8) -> list:
    """Fetch RSS and return list of {title, source} dicts. Never raises."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AlgoVibe/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        ns = ""
        items = root.findall(".//item") or root.findall(".//entry")
        results = []
        for item in items[:limit]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                results.append({"title": title_el.text.strip(), "source": source})
        return results
    except Exception as e:
        logging.warning(f"[news] Failed to fetch {source}: {e}")
        return []


@app.route('/api/news')
def api_news():
    global _news_cache
    now = time.time()
    if now - _news_cache["fetched_at"] < _NEWS_TTL and _news_cache["items"]:
        return jsonify(_news_cache["items"])

    items = []
    for source, url in _NEWS_FEEDS:
        items.extend(_fetch_rss_titles(url, source))

    if items:
        _news_cache = {"items": items, "fetched_at": now}

    return jsonify(items or _news_cache["items"])


@app.route('/<path:path>')
def static_proxy(path):
    dist_dir = os.path.join(app.static_folder, 'dist')
    full = os.path.join(dist_dir, path)
    if os.path.exists(full):
        return send_from_directory(dist_dir, path)
    # Fallback: serve Vue SPA index.html for client-side routing
    return send_from_directory(dist_dir, 'index.html')

@socketio.on('watch_pair')
def handle_watch_pair(data):
    global CURRENT_WATCH_PAIR
    pair = data.get('pair', CURRENT_WATCH_PAIR).lower().strip()
    if not pair:
        return
    CURRENT_WATCH_PAIR = pair
    db.save_state('current_watch_pair', pair)
    # Trigger WS Market to subscribe to this pair's trades
    mws.subscribe_market_trades(pair)
    db.save_state('trigger_analysis_pair', pair)

def _build_stats_payload():
    """Bangun payload stats langsung tanpa melalui Flask request context."""
    conn = get_db_connection()
    try:
        stats_dict = {r['key']: r['value'] for r in conn.execute("SELECT * FROM bot_state").fetchall()}
        positions  = conn.execute("SELECT * FROM positions").fetchall()
        perf       = db.get_daily_stats()
        # 1. LIVE STATS (Real Money)
        live_stats = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE dry_run = 0"
        ).fetchone()
        
        # 2. DRY RUN STATS (Live Simulation)
        dry_stats = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE dry_run = 1 AND reason != 'BACKTEST'"
        ).fetchone()

        # 3. BACKTEST STATS (AI Knowledge / DNA)
        dna_stats = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE reason = 'BACKTEST'"
        ).fetchone()

        win_rate = (live_stats['wins'] / live_stats['trades'] * 100) if (live_stats and live_stats['trades'] > 0) else 0
        dry_win_rate = (dry_stats['wins'] / dry_stats['trades'] * 100) if (dry_stats and dry_stats['trades'] > 0) else 0
        dna_win_rate = (dna_stats['wins'] / dna_stats['trades'] * 100) if (dna_stats and dna_stats['trades'] > 0) else 0

        unrealized_pnl = 0.0
        ws_tickers = mws.get_tickers() # Pre-fetch untuk efisiensi
        
        import config as cfg
        dr_val = 1 if cfg.DRY_RUN else 0
        
        # [NEW] Predator Metrics Calculation
        total_used_idr = 0.0
        for pos in positions:
            if int(pos['dry_run'] or 0) == dr_val:
                total_used_idr += float(pos['amount_idr'] or 0)

        realized_pnl_dry = float(dry_stats['total_pnl'] or 0)
        realized_pnl_live = float(live_stats['total_pnl'] or 0)

        if cfg.DRY_RUN:
            available_idr = cfg.INITIAL_CAPITAL + realized_pnl_dry - total_used_idr
            idr_used = total_used_idr
            active_pnl = realized_pnl_dry
            active_win_rate = dry_win_rate
            # Dry Run Equity = Sisa Dana + Modal Terpakai + PnL Mengambang
            total_equity_val = available_idr + total_used_idr + unrealized_pnl
        else:
            # LIVE MODE: Meniru 'Estimated Asset Value' Indodax
            real_bal = _get_cached_real_balance_bundle()
            ws_tickers_for_equity = mws.get_tickers()
            if real_bal is not None:
                # available_idr tetap menampilkan 'free' untuk info dana siap pakai di UI
                available_idr = float(real_bal.get("free", 0) or 0)
                idr_used = float(real_bal.get("used", 0) or 0)
                idr_total = float(real_bal.get("total", 0) or 0)
                
                # Hitung nilai market semua koin non-IDR
                coin_equity_idr = _get_coin_equity_idr(
                    real_bal.get("coins", {}), ws_tickers_for_equity
                )
                
                # [Senior Fix] Total Equity LIVE = Total IDR (Free+Used) + Nilai Koin saat ini.
                total_equity_val = idr_total + coin_equity_idr
                
                active_pnl = realized_pnl_live
                active_win_rate = win_rate
            else:
                # Fallback jika cache kosong
                available_idr = float(_REAL_BALANCE_CACHE.get("free") or 0)
                idr_used = float(_REAL_BALANCE_CACHE.get("used") or 0)
                idr_total = float(_REAL_BALANCE_CACHE.get("total") or 0)
                coin_equity_idr = _get_coin_equity_idr(
                    _REAL_BALANCE_CACHE.get("coins", {}), ws_tickers_for_equity
                )
                total_equity_val = idr_total + coin_equity_idr
                active_pnl = realized_pnl_live
                active_win_rate = win_rate

        # [Senior Audit] Calculate Unrealized PnL
        for pos in positions:
            if int(pos['dry_run'] or 0) != dr_val:
                continue

            pair = pos['pair']
            ep = float(pos['entry_price'] or 0)
            if ep <= 0: continue
            
            # Ambil harga live (Prioritas: Trade Stream > Summary > DB)
            last_price = 0
            ws_trades = mws.get_trades(pair)
            if ws_trades:
                try: last_price = float(ws_trades[0][4])
                except: pass
            
            if last_price <= 0:
                last_price = float(ws_tickers.get(pair, {}).get('last', 0))
                
            if last_price <= 0:
                # Fallback terakhir ke DB history
                try:
                    row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)).fetchone()
                    if row and row['history_json']:
                        history = json.loads(row['history_json'])
                        if history: last_price = float(history[-1])
                except: pass

            if last_price > 0:
                unrealized_pnl += (last_price - ep) * float(pos['amount_crypto'] or 0)

        total_equity = total_equity_val
        utilization_pct = (total_used_idr / (available_idr + total_used_idr) * 100) if (available_idr + total_used_idr) > 0 else 0
        # Injeksi Harga Realtime (Prioritas: Trade Stream > Summary > DB)
        ws_tickers = mws.get_tickers()
        ws_trades = mws.get_trades(CURRENT_WATCH_PAIR)
        tech_data = _get_pair_technicals(CURRENT_WATCH_PAIR)
        
        # 1. Ambil dari Trade Stream (Paling Cepat / Sub-detik)
        ws_price = 0
        if ws_trades:
            try: ws_price = float(ws_trades[0][4]) # Index 4 is price
            except: pass
            
        # 2. Fallback ke Summary Stream
        if ws_price <= 0:
            ws_price = float(ws_tickers.get(CURRENT_WATCH_PAIR, {}).get('last', 0))
            
        if ws_price > 0:
            tech_data['last_price'] = ws_price

        ai_reasoning, updated_at = db.get_state_with_time(f"llm_reasoning_{CURRENT_WATCH_PAIR}")
        
        # Validasi Kesegaran Analisis (Jika > 3 menit, anggap stale dan gunakan Technical Fallback)
        is_stale = True
        if updated_at:
            try:
                # SQLite CURRENT_TIMESTAMP is UTC
                dt = datetime.datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - dt).total_seconds() < 180:
                    is_stale = False
            except: pass
                
        _last_price = tech_data.get('last_price')
        combined_reasoning = ai_reasoning if (ai_reasoning and not is_stale) else _generate_technical_reasoning(CURRENT_WATCH_PAIR, tech_data, _last_price)

        history_since = conn.execute(
            "SELECT strftime('%d/%m %H:%M', MIN(updated_at)) FROM ticker_history"
        ).fetchone()[0] or '--/-- --:--'

        import config as cfg

        # PnL & Win Rate Selection (Live vs Dry Run) — strict by mode.
        if cfg.DRY_RUN:
            active_pnl = float(dry_stats['total_pnl'] or 0) if dry_stats else 0
            active_win_rate = dry_win_rate
        else:
            active_pnl = float(live_stats['total_pnl'] or 0) if live_stats else 0
            active_win_rate = win_rate

        # Cari Top Pair, Best Trade & Asset Contributions (Overall)
        top_pair_row = conn.execute(
            "SELECT pair, SUM(pnl_idr) as total_pnl FROM trade_history "
            "WHERE reason != 'BACKTEST' AND dry_run = ? "
            "GROUP BY pair ORDER BY total_pnl DESC LIMIT 1", (dr_val,)
        ).fetchone()
        
        contribution_rows = conn.execute(
            "SELECT pair, SUM(pnl_idr) as total_pnl, COUNT(*) as tx_count FROM trade_history "
            "WHERE reason != 'BACKTEST' AND dry_run = ? "
            "GROUP BY pair ORDER BY total_pnl DESC", (dr_val,)
        ).fetchall()
        
        asset_contributions = []
        for row in contribution_rows:
            asset_contributions.append({
                "pair": row['pair'],
                "pnl": float(row['total_pnl'] or 0),
                "trades": row['tx_count']
            })

        best_trade_row = conn.execute(
            "SELECT MAX(pnl_idr) FROM trade_history "
            "WHERE reason != 'BACKTEST' AND dry_run = ?", (dr_val,)
        ).fetchone()
        
        top_pair = top_pair_row['pair'] if top_pair_row and top_pair_row['total_pnl'] > 0 else None
        best_trade = float(best_trade_row[0] or 0)
        regime = stats_dict.get('market_regime', 'CHOPPY')

        # Overall engagements (Closed + Open)
        total_trades_overall = dry_stats['trades'] if cfg.DRY_RUN else live_stats['trades']
        open_count = len([p for p in positions if int(p['dry_run'] or 0) == dr_val and (p['status'] or '').upper() in ['OPEN', 'EXITING']])
        total_engagements = total_trades_overall + open_count
        
        equity_reasoning, equity_action_plan = _generate_equity_reasoning(
            active_pnl, unrealized_pnl, active_win_rate, total_engagements,
            top_pair=top_pair, best_trade=best_trade, regime=regime
        )

        # --- Uptime Logic ---
        first_trade_row = conn.execute("SELECT MIN(created_at) FROM trade_history WHERE reason != 'BACKTEST'").fetchone()
        first_trade_ts = 0
        if first_trade_row and first_trade_row[0]:
            try:
                dt_str = first_trade_row[0]
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                first_trade_ts = int(dt.timestamp() * 1000)
            except: pass

        # --- LLM Executive Report Integration ---
        equity_reasoning_ai, updated_at = db.get_state_with_time("llm_equity_report")
        
        is_stale_report = True
        if updated_at:
            try:
                dt = datetime.datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - dt).total_seconds() < 600:
                    is_stale_report = False
            except: pass
            
        if is_stale_report:
            _generate_equity_report_llm({
                "pnl": active_pnl, "unrealized": unrealized_pnl, "win_rate": active_win_rate,
                "trades": total_engagements, "open_count": open_count, "regime": regime, "top_pair": top_pair,
                "intensity": round(total_engagements / max(1, (time.time() - _start_time) / 3600), 1)
            })
            
        # Poll sekali untuk laporan terbaru
        latest_report = LLMWorker.get_instance().get_latest_result_by_type("equity_report")
        if latest_report:
            db.set_state("llm_equity_report", latest_report)
            equity_reasoning_ai = latest_report

        equity_reasoning_tech, equity_action_plan = _generate_equity_reasoning(
            active_pnl, unrealized_pnl, active_win_rate, total_engagements,
            top_pair=top_pair, best_trade=best_trade, regime=regime
        )

        final_equity_report = equity_reasoning_ai if equity_reasoning_ai else equity_reasoning_tech

        # --- Recent Trades for History Tab ---
        recent_trades = []
        try:
            recent_trades_rows = conn.execute("""
                SELECT pair, entry_price, exit_price, amount_crypto, amount_idr,
                       pnl_idr, pnl_pct, reason, duration_seconds, created_at
                FROM trade_history
                WHERE reason != 'BACKTEST' AND dry_run = ?
                ORDER BY created_at DESC LIMIT 20
            """, (int(dr_val),)).fetchall()
            for r in recent_trades_rows:
                recent_trades.append({
                    "pair":        r['pair'],
                    "side":        'SELL',
                    "entry_price": float(r['entry_price'] or 0),
                    "exit_price":  float(r['exit_price'] or 0),
                    "amount":      float(r['amount_crypto'] or 0),
                    "amount_idr":  float(r['amount_idr'] or 0),
                    "pnl_idr":     float(r['pnl_idr'] or 0),
                    "pnl_pct":     float(r['pnl_pct'] or 0),
                    "reason":      r['reason'],
                    "created_at":  r['created_at'],
                })
        except Exception as e:
            print(f"⚠️ History Fetch Error: {e}")


        # ── BTC Condition (for Signal Radar UX) ─────────────────────────────
        _btc_ticker = ws_tickers.get('btcidr', {}) or {}
        btc_price = float(_btc_ticker.get('last', 0))
        btc_change_24h = float(_btc_ticker.get('change_24h', 0))
        try:
            _btc_row = conn.execute(
                "SELECT ch_5m, ch_15m, ch_1h, trend, score FROM ticker_history WHERE pair = 'btcidr'"
            ).fetchone()
        except Exception:
            _btc_row = None
        btc_ch_5m  = float(_btc_row['ch_5m']  or 0) if _btc_row else 0.0
        btc_ch_15m = float(_btc_row['ch_15m'] or 0) if _btc_row else 0.0
        btc_ch_1h  = float(_btc_row['ch_1h']  or 0) if _btc_row else 0.0
        btc_trend  = str(_btc_row['trend'] or 'N/A') if _btc_row else 'N/A'
        btc_score  = float(_btc_row['score']  or 50) if _btc_row else 50.0
        btc_weakening_flag = btc_ch_5m < 0  # proxy: 5-min change negative = weakening
        btc_crash_flag     = btc_ch_5m < -2.0  # proxy for crash kill-switch territory

        payload = {
            "available_idr": round(available_idr, 0),
            "idr_used": round(idr_used, 0),
            "total_equity": round(total_equity, 0),
            "utilization": round(utilization_pct, 1),
            "recent_trades": recent_trades,
            "first_trade_ts": first_trade_ts,
            "daily_trades": perf['total_trades'],
            "trades": total_engagements,
            "history_count": total_trades_overall,
            "open_count": open_count,
            "daily_pnl": active_pnl,
            "win_rate": round(active_win_rate, 2),
            "dry_win_rate": round(dry_win_rate, 2),
            "dry_trades_count": dry_stats['trades'] if dry_stats else 0,
            "total_trades_live": live_stats['trades'] if live_stats else 0,
            "live_pnl": float(live_stats['total_pnl'] or 0) if live_stats else 0,
            "live_win_rate": round(win_rate, 2),
            "live_trades_count": live_stats['trades'] if live_stats else 0,
            "backtest_pnl": float(dna_stats['total_pnl'] or 0) if dna_stats else 0,
            "backtest_win_rate": round(dna_win_rate, 2),
            "backtest_trades_count": dna_stats['trades'] if dna_stats else 0,
            "active_positions": open_count,
            "unrealized_pnl": round(unrealized_pnl, 0),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', ''),
            "latest_llm_insight": stats_dict.get('latest_llm_insight', ''),
            "hero_ai_insight": stats_dict.get('hero_ai_insight', 'Waiting for AI analysis...'),
            "hero_ai_insights": [{"message": r[0], "created_at": r[1]} for r in conn.execute("SELECT message, created_at FROM system_events WHERE type='AI_INSIGHT' ORDER BY created_at DESC LIMIT 5").fetchall()] or [{"message": stats_dict.get('hero_ai_insight', 'Waiting for AI analysis...'), "created_at": datetime.datetime.utcnow().isoformat()}],
            "active_reasoning": combined_reasoning,
            "equity_reasoning": final_equity_report,
            "equity_action_plan": equity_action_plan,
            "asset_contributions": asset_contributions,
            "technicals": tech_data,
            "sectors": db.get_all_latest_sector_sentiment(),
            "weights": db.get_all_weights(),
            "market_sentiment_index": int(stats_dict.get('market_sentiment_index', 50)),
            "backfill_status": stats_dict.get('backfill_status', 'N/A'),
            "history_since": history_since,
            "new_listings": db.get_new_listings(),
            "bot_running": True,
            "dry_run": bool(cfg.DRY_RUN),
            "dry_run_loose_gates": bool(cfg.DRY_RUN_LOOSE_GATES),
            "manual_exit_max_net_pct": cfg.MANUAL_EXIT_MAX_NET_PCT,
            # BTC Condition — used by Signal Radar UX
            "btc_price":      round(btc_price, 0),
            "btc_change_24h": round(btc_change_24h, 2),
            "btc_ch_5m":      round(btc_ch_5m, 2),
            "btc_ch_15m":     round(btc_ch_15m, 2),
            "btc_ch_1h":      round(btc_ch_1h, 2),
            "btc_trend":      btc_trend,
            "btc_score":      round(btc_score, 1),
            "btc_weakening":  btc_weakening_flag,
            "btc_crash":      btc_crash_flag,
        }
        return payload
    finally:
        conn.close()

def _build_radar_payload():
    """Bangun payload radar untuk semua pair market (WS + DB ticker_history)."""
    conn = get_db_connection()
    # Ambil snapshot harga terbaru dari WS Buffer (Realtime detik)
    ws_tickers = mws.get_tickers()
    
    try:
        rows = conn.execute(
            "SELECT pair, trend, prediction_prob, ch_5m, ch_15m, ch_1h, volatility, score, signal, history_json, updated_at "
            "FROM ticker_history"
        ).fetchall()

        db_by_pair = {str(r['pair']).lower(): r for r in rows if r['pair']}
        all_pairs = set(db_by_pair.keys()) | {str(p).lower() for p in ws_tickers.keys()}

        def _clamp(v, lo, hi):
            return max(lo, min(hi, v))

        def _derive_signal(score, prediction, change_now, trend):
            t = str(trend or '').upper()
            if score >= 70 and prediction >= 0.55:
                return "BUY"
            if score <= 30 and prediction <= 0.45:
                return "SELL"
            if change_now >= 0.35 and ("UP" in t or "BULL" in t):
                return "BUY"
            if change_now <= -0.35 and ("DOWN" in t or "BEAR" in t):
                return "SELL"
            return "HOLD"

        def _is_fresh(ts_str, max_age_sec=600):
            if not ts_str:
                return False
            try:
                ts = datetime.datetime.fromisoformat(str(ts_str))
                return (datetime.datetime.now() - ts).total_seconds() <= max_age_sec
            except Exception:
                return False # Default

        def _parse_state_ts(ts_str):
            if not ts_str:
                return None
            text = str(ts_str)
            candidates = [text, text.replace(' ', 'T')]
            for c in candidates:
                try:
                    return datetime.datetime.fromisoformat(c)
                except Exception:
                    continue
            return None

        results = []
        for pair in all_pairs:
            try:
                row = db_by_pair.get(pair)
                history = []
                if row and row['history_json']:
                    history = json.loads(row['history_json'] or '[]')
                
                # Injeksi harga dari WS jika ada, jika tidak pakai dari DB
                ws_data = ws_tickers.get(pair, {}) or ws_tickers.get(pair.upper(), {})
                last = float(ws_data.get('last', 0))
                ch_24h = float(ws_data.get('change_24h', 0))
                
                if last <= 0:
                    last = float(history[-1]) if history else 0
                
                # Gunakan titik sebelumnya agar delta harga radar benar-benar mencerminkan momentum terbaru.
                prev = float(history[-2]) if len(history) > 1 else last
                change = ((last - prev) / prev * 100) if prev > 0 else 0
                

                trend = (row['trend'] if row else None) or ('UP' if change > 0 else ('DOWN' if change < 0 else 'SIDEWAYS'))
                score = float(row['score'] or 0) if row else _clamp(50 + (change * 4.0) + (ch_24h * 0.8), 0, 100)
                prediction = float(row['prediction_prob'] or 0.0) if row else _clamp(0.5 + (change / 20.0) + (ch_24h / 200.0), 0.0, 1.0)
                signal = (row['signal'] if row else None) or _derive_signal(score, prediction, change, trend)

                radar_reco = db.get_state(f"radar_reco_{pair}", signal)
                radar_reason = db.get_state(f"radar_reco_reason_{pair}", "")

                gate_skip, gate_updated_at = db.get_state_with_time(f"gate_skip_{pair}")
                gate_ts = _parse_state_ts(gate_updated_at)
                gate_fresh = bool(gate_ts and (datetime.datetime.now() - gate_ts).total_seconds() <= 600)
                gate_msg = gate_skip if gate_fresh else ""

                veto_raw = db.get_state(f"llm_veto_{pair}")
                veto_ts_raw = db.get_state(f"llm_veto_ts_{pair}")
                veto_fresh = _is_fresh(veto_ts_raw, max_age_sec=15 * 60)
                veto_conf = 0
                veto_reason = ""
                ai_veto = False
                if veto_raw and veto_fresh:
                    try:
                        parsed_veto = LLMWorker.parse_structured_result(veto_raw)
                        veto_conf = int(parsed_veto.get('confidence', 0) or 0)
                        veto_reason = parsed_veto.get('reason', '')
                        ai_veto = str(parsed_veto.get('verdict', 'HOLD')).upper() == 'AVOID' and veto_conf >= 75
                    except Exception:
                        pass

                is_blocked = ai_veto or (gate_fresh and str(gate_msg).upper().startswith('SKIP')) or (gate_fresh and 'BLOCK' in str(gate_msg).upper())
                tradable_reason = "AI VETO" if ai_veto else (gate_msg or "READY")

                results.append({
                    "pair":       pair,
                    "price":      last,
                    "change":     round(change, 2),
                    "change_24h": round(ch_24h, 2),
                    "vol_idr":    float(ws_data.get("vol_idr", 0)),
                    "ch_5m":      float(row['ch_5m'] or 0) if row else 0.0,
                    "ch_15m":     float(row['ch_15m'] or 0) if row else 0.0,
                    "ch_1h":      float(row['ch_1h'] or 0) if row else 0.0,
                    "volatility": float(row['volatility'] or 0) if row else 0.0,
                    "score":      float(score),
                    "prediction": float(prediction),
                    "trend":      trend,
                    "signal":     signal,
                    "radar_reco": str(radar_reco or signal).upper(),
                    "radar_reason": radar_reason,
                    "gate_status": gate_msg,
                    "gate_updated_at": gate_updated_at,
                    "ai_veto": ai_veto,
                    "ai_veto_reason": veto_reason,
                    "ai_veto_confidence": veto_conf,
                    "tradable": not is_blocked,
                    "tradable_reason": tradable_reason,
                    "updated_at": row["updated_at"] if row else None
                })
            except Exception:
                continue
        def _signal_rank(it):
            s = str(it.get('radar_reco') or it.get('signal', '')).upper()
            if 'BUY' in s:
                return 0
            if 'SELL' in s:
                return 1
            return 2

        results.sort(key=lambda x: (_signal_rank(x), -float(x.get('score', 0)), str(x.get('pair', ''))))
        return results
    finally:
        conn.close()

def _build_trades_payload():
    """[Senior Audit] Robust trade payload builder with per-item safety guards."""
    conn = get_db_connection()
    results = []
    try:
        import config as cfg
        dr_val = 1 if cfg.DRY_RUN else 0

        # 1. Fetch Positions (OPEN / PENDING_BUY / EXITING)
        positions = conn.execute("SELECT * FROM positions").fetchall()
        ws_tickers = mws.get_tickers()

        for row in positions:
            p = dict(row)
            try:
                pair = p['pair']
                # Filter by dry_run if needed, but usually we show all active in current mode
                if int(p['dry_run'] or 0) != dr_val:
                    continue

                pos_status = str(p['status'] or 'OPEN')
                entry_price = float(p['entry_price'] or 0)
                amount_crypto = float(p['amount_crypto'] or 0)
                
                # Live Price Fallback (WS > DB > Entry)
                last_price = float(ws_tickers.get(pair, {}).get('last', 0))
                
                if last_price <= 0:
                    try:
                        row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)).fetchone()
                        if row and row['history_json']:
                            history = json.loads(row['history_json'])
                            if history: last_price = float(history[-1])
                    except: pass

                if last_price <= 0: last_price = entry_price

                pnl_idr = (last_price - entry_price) * amount_crypto
                pnl_pct = ((last_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

                # Beri label reason yang jelas berdasarkan status nyata posisi
                if pos_status == 'PENDING_BUY':
                    display_reason = "MENUNGGU FILL"
                elif pos_status == 'EXITING':
                    display_reason = "SELL ORDER TERKIRIM"
                else:
                    display_reason = "OPEN POSITION"

                # Dynamic TP/SL percentages based on actual prices in DB
                stored_tp_pct = float(p.get('target_tp_pct') or 0)
                stored_sl_pct = float(p.get('target_sl_pct') or 0)
                
                # SL dynamic calculation
                trailing_sl_price = float(p.get('trailing_sl_price') or 0)
                if trailing_sl_price > 0 and entry_price > 0:
                    current_sl_pct = (trailing_sl_price - entry_price) / entry_price
                else:
                    current_sl_pct = stored_sl_pct

                current_tp_pct = stored_tp_pct

                _amount_idr = float(p.get('amount_idr') or 0)
                _fee_buy  = _amount_idr * cfg.FEE_BUY_TAKER
                _fee_sell = (last_price * amount_crypto) * cfg.FEE_SELL_TAKER
                pnl_net_idr = pnl_idr - _fee_buy - _fee_sell

                results.append({
                    "id":           f"pos_{pair}",
                    "pair":         pair,
                    "reason":       display_reason,
                    "pnl_idr":      round(pnl_idr, 0),
                    "pnl_net_idr":  round(pnl_net_idr, 0),
                    "pnl_pct":      round(pnl_pct, 2),
                    "entry_price":  entry_price,
                    "exit_price":   last_price,
                    "target_tp":    current_tp_pct * 100,
                    "target_sl":    current_sl_pct * 100,
                    "confidence":   int(p.get('ai_confidence') or 0),
                    "amount_idr":   _amount_idr,
                    "amount_coin":  amount_crypto,
                    "duration":     0,
                    "created_at":   str(p.get('timestamp') or ''),
                    "status":       pos_status,
                    "pos_status":   pos_status,
                    "is_manual":    0
                })
            except Exception as e:
                print(f"Error processing position {p.get('pair')}: {e}")

        # 2. Sort by creation time to show newest positions first
        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return results
    except Exception as e:
        print(f"CRITICAL: _build_trades_payload failure: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        conn.close()

def _build_logs_payload():
    """Ambil log sinyal AI, oracle, dan system events dari DB."""
    conn = get_db_connection()
    try:
        # 1. Fetch System Events (New Source)
        events = conn.execute("""
            SELECT pair, type as side, message as price, level as status, created_at
            FROM system_events
            ORDER BY created_at DESC LIMIT 20
        """).fetchall()

        # 2. Fetch AI Signals dari ticker_history (Legacy fallback for predictions)
        ai_scans = conn.execute("""
            SELECT pair, prediction_prob, trend, updated_at as created_at
            FROM ticker_history WHERE prediction_prob > 0.4
            ORDER BY updated_at DESC LIMIT 5
        """).fetchall()

        # 3. Fetch Oracle data from bot_state
        oracle_data = conn.execute("""
            SELECT key, value, updated_at as created_at FROM bot_state
            WHERE key IN ('market_regime', 'regime_leader', 'latest_whale', 'latest_fake_pump')
            ORDER BY updated_at DESC
        """).fetchall()

        results = []
        
        # Add System Events first (highest priority)
        for e in events:
            results.append({
                "created_at": str(e['created_at']),
                "pair": e['pair'].upper() if e['pair'] else 'SYSTEM',
                "side": e['side'],
                "price": e['price'],
                "status": e['status'],
                "level": e['status'] # Dashboard uses 'level' for UI styling
            })

        for a in ai_scans:
            # Avoid duplicates if already in system_events
            results.append({
                "created_at": str(a['created_at']),
                "pair":  a['pair'].upper(),
                "side":  "PREDICTION",
                "price": f"Prob {round(a['prediction_prob']*100)}%",
                "status": f"Trend: {a['trend']}",
                "level": "INFO"
            })
            
        icon_map = {
            'market_regime': '📡 REGIME',
            'regime_leader': '🔮 ORACLE',
            'latest_whale':  '🐋 WHALE',
            'latest_fake_pump': '⚠️ FAKE PUMP'
        }
        for o in oracle_data:
            val = o['value'] or ''
            results.append({
                "created_at": str(o['created_at']),
                "pair":  val.split('|')[0].upper() if '|' in val else 'GLOBAL',
                "side":  icon_map.get(o['key'], 'EVENT'),
                "price": val.split('|')[1] if '|' in val else val,
                "status": "DETECTED",
                "level": "ALERT" if "whale" in o['key'] else ("WARN" if "pump" in o['key'] else "INFO")
            })

        # Sort combined results by created_at descending
        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return results[:30]
    finally:
        conn.close()

def _build_market_trades_payload():
    """Ambil data trades terbaru dari mws untuk watch pair."""
    try:
        trades = mws.get_trades(CURRENT_WATCH_PAIR)
        results = []
        for t in trades:
            try:
                # Indodax format confirmed: [pair, timestamp, sequence, side, price, vol_idr, vol_coin]
                results.append({
                    "time": float(t[1]) * 1000, # index 1 is timestamp
                    "price": float(t[4]),       # index 4 is price
                    "amount": float(t[6]),      # index 6 is volume coin
                    "side": str(t[3]).lower(),  # index 3 is side
                    "id": t[2]                  # index 2 is sequence
                })
            except (IndexError, ValueError, TypeError):
                continue
        return results
    except Exception as e:
        logging.error(f"Build trades error: {e}")
        return []

def push_data_loop():
    """Background thread — push data via WebSocket.
    Fast path (1s): orderbook + market trades (realtime).
    Slow path (5s): stats, radar, trades history, logs (berat).
    """
    _slow_counter = 0
    _last_pending_action_id = None  # Track last forwarded action_id to avoid spam
    while True:
        try:
            # Fast: data realtime sub-detik
            depth = db.get_market_depth(CURRENT_WATCH_PAIR)
            socketio.emit('orderbook_update', {
                "pair": CURRENT_WATCH_PAIR,
                "bids": depth.get('bids', []),
                "asks": depth.get('asks', [])
            })
            socketio.emit('market_trades_update', {
                "pair": CURRENT_WATCH_PAIR,
                "trades": _build_market_trades_payload()
            })

            # ── Supreme Commander: forward pending approval to frontend ──────────────
            try:
                raw_approval = db.get_state("pending_approval")
                if raw_approval:
                    pending = json.loads(raw_approval)
                    if (
                        pending.get("status") == "PENDING"
                        and pending.get("action_id") != _last_pending_action_id
                    ):
                        _last_pending_action_id = pending.get("action_id")
                        socketio.emit('pending_action', pending)
                elif _last_pending_action_id is not None:
                    # Approval resolved — tell frontend to dismiss
                    socketio.emit('pending_action_cleared', {})
                    _last_pending_action_id = None
            except Exception as _ae:
                logging.warning(f"[ApprovalGate] Error checking pending_approval: {_ae}")

            # ── Auto-harvest silent toast notification ────────────────────────────
            try:
                raw_notif = db.get_state("auto_harvest_notification")
                if raw_notif:
                    notif = json.loads(raw_notif)
                    socketio.emit('toast_notification', {
                        "title": "🚀 Auto-Harvest",
                        "message": notif.get("message", "Basket Exit Completed (Profit Secured)"),
                        "type": "success",
                    })
                    db.save_state("auto_harvest_notification", "")
            except Exception as _ne:
                logging.warning(f"[AutoHarvest] Error forwarding notification: {_ne}")

            # ── Strategic Break: forward full-screen blocking popup to frontend ──
            try:
                raw_sb = db.get_state("strategic_break_payload")
                if raw_sb:
                    sb = json.loads(raw_sb)
                    if sb.get("status") == "PENDING":
                        socketio.emit('strategic_break', sb)
            except Exception as _sbe:
                logging.warning(f"[StrategicBreak] Error forwarding payload: {_sbe}")

            # Slow: setiap 5 iterasi (~5 detik)
            _slow_counter += 1
            if _slow_counter >= 5:
                _slow_counter = 0
                socketio.emit('stats_update',  _build_stats_payload())
                socketio.emit('radar_update',  _build_radar_payload())
                socketio.emit('trades_update', _build_trades_payload())
                socketio.emit('logs_update',   _build_logs_payload())
        except Exception as e:
            print(f"WS Error: {e}")
        socketio.sleep(1)

@socketio.on('approval_response')
def handle_approval_response(data):
    """Supreme Commander: receive user's approve/reject decision from the frontend."""
    action_id = data.get('action_id', '')
    decision  = data.get('decision', '')  # 'APPROVED' or 'REJECTED'

    if not action_id or decision not in ('APPROVED', 'REJECTED'):
        logging.warning(f"[ApprovalGate] Invalid approval_response payload: {data}")
        return

    try:
        raw = db.get_state("pending_approval")
        if not raw:
            return
        pending = json.loads(raw)
        if pending.get("action_id") != action_id:
            logging.warning(f"[ApprovalGate] action_id mismatch: got {action_id}, expected {pending.get('action_id')}")
            return
        if pending.get("status") != "PENDING":
            return  # Already resolved
        pending["status"] = decision
        db.save_state("pending_approval", json.dumps(pending))
        logging.info(f"[ApprovalGate] {decision} received for action {action_id} ({pending.get('action_type', '')})")
    except Exception as e:
        logging.error(f"[ApprovalGate] Error processing approval_response: {e}")


@app.route('/api/session/action', methods=['POST'])
@login_required
def session_action():
    """
    Strategic Break user action endpoint.

    Accepts JSON: { "action": "CONTINUE" | "STOP" }

    CONTINUE — Recalculates next target as (current_equity + target_percentage) and
               resets session_mode to NORMAL so buying is re-enabled.
    STOP     — Keeps HARVEST_ONLY status; buy functions remain blocked.
    """
    data = request.get_json(silent=True) or {}
    action = data.get('action', '').upper()

    if action not in ('CONTINUE', 'STOP'):
        return jsonify({"error": "Invalid action. Use 'CONTINUE' or 'STOP'."}), 400

    try:
        raw_sb = db.get_state("strategic_break_payload")
        if not raw_sb:
            return jsonify({"error": "No active strategic break."}), 404
        payload = json.loads(raw_sb)

        if action == 'CONTINUE':
            # Recalculate next target: current total equity * (1 + target percentage).
            # Use the persisted daily_profit_target as the target pct reference.
            try:
                balance_str = db.get_state("cached_idr_balance")
                from config import INITIAL_CAPITAL as _IC
                equity = float(balance_str) if balance_str else float(_IC)
            except Exception:
                from config import INITIAL_CAPITAL as _IC
                equity = float(_IC)

            # Derive target_pct from the original target vs equity ratio (fallback 1%)
            try:
                orig_target = float(db.get_state("daily_profit_target") or 0)
                target_pct = (orig_target / equity) if equity > 0 else 0.01
            except Exception:
                target_pct = 0.01

            new_target = equity * (1 + target_pct)
            db.save_state("daily_profit_target", str(new_target))
            db.save_state("daily_profit_target_ts", datetime.datetime.now().isoformat())
            db.save_state("session_mode", "NORMAL")
            db.save_state("session_buy_enabled", "true")

            payload["status"] = "CONTINUE"
            db.save_state("strategic_break_payload", json.dumps(payload))

            # Notify frontend to dismiss the popup
            socketio.emit('strategic_break_cleared', {"action": "CONTINUE", "new_target": new_target})
            logging.info(f"[StrategicBreak] CONTINUE — new target: Rp {new_target:,.0f}")
            return jsonify({"status": "ok", "action": "CONTINUE", "new_target": new_target})

        else:  # STOP
            # Keep HARVEST_ONLY — do not change session_mode.
            payload["status"] = "STOP"
            db.save_state("strategic_break_payload", json.dumps(payload))
            socketio.emit('strategic_break_cleared', {"action": "STOP"})
            logging.info("[StrategicBreak] STOP — session remains HARVEST_ONLY.")
            return jsonify({"status": "ok", "action": "STOP"})

    except Exception as e:
        logging.error(f"[StrategicBreak] session_action error: {e}")
        return jsonify({"error": str(e)}), 500

# Start background thread
socketio.start_background_task(push_data_loop)

def _background_wallet_sync_loop():
    """
    Background thread yang berjalan di dashboard untuk menjaga sinkronisasi wallet Indodax
    dengan database secara otomatis setiap 5 menit (hanya di mode LIVE).
    """
    import config as _cfg
    from config import TP_PCT, SL_PCT, DB_PATH_LIVE
    socketio.sleep(30)  # Tunggu 30 detik setelah startup sebelum sync pertama
    while True:
        try:
            if _cfg.DRY_RUN:
                socketio.sleep(60)
                continue

            user = db.get_user('admin')
            api_key = (user['api_key'] if user else None) or os.getenv('INDODAX_API_KEY', '')
            api_secret = (user['api_secret'] if user else None) or os.getenv('INDODAX_API_SECRET', '')

            if not api_key or not api_secret:
                socketio.sleep(300)
                continue

            import ccxt as _ccxt
            ex = _ccxt.indodax({'apiKey': api_key, 'secret': api_secret, 'enableRateLimit': True})
            balance = ex.fetch_balance()
            total_bal = balance.get('total', {})

            conn = get_db_connection()
            try:
                current_positions = {
                    row['pair']: dict(row)
                    for row in conn.execute(
                        "SELECT * FROM positions WHERE dry_run = 0"
                    ).fetchall()
                }
                blacklist = set(r['pair'] for r in conn.execute("SELECT pair FROM blacklist").fetchall())
            finally:
                conn.close()

            ws_tickers = mws.get_tickers()
            adopted_count = 0
            removed_count = 0

            # Ghost removal
            for pair, pos in list(current_positions.items()):
                coin = pair[:-3].upper()  # Semua pair format '{coin}idr'; slice -3 hapus suffix 'idr'
                amount_on_exchange = float(total_bal.get(coin, 0) or 0)
                if amount_on_exchange < 1e-8:
                    db.delete_position(pair)
                    current_positions.pop(pair, None)
                    removed_count += 1
                    logging.info(f"[BgWalletSync] Ghost removed: {pair}")

            # Orphan adoption
            for coin, amount in total_bal.items():
                if coin in ('IDR', 'info') or not amount or float(amount) <= 0:
                    continue
                pair = f"{coin.lower()}idr"
                if pair in blacklist or pair in current_positions:
                    continue

                price = float(ws_tickers.get(pair, {}).get('last', 0))
                if price <= 0:
                    try:
                        ticker = ex.fetch_ticker(f"{coin}/IDR")
                        price = float(ticker.get('last', 0))
                    except Exception:
                        pass

                amount_f = float(amount)
                value_idr = amount_f * price
                if value_idr < 1000:
                    continue

                adopt_data = {
                    "entry_price": price,
                    "amount": amount_f,
                    "amount_crypto": amount_f,
                    "amount_idr": value_idr,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "dry_run": False,
                    "status": "OPEN",
                    "entry_order_id": f"BG-SYNC-{int(time.time())}",
                    "exit_order_id": None,
                    "target_tp_pct": TP_PCT,
                    "target_sl_pct": SL_PCT,
                    "reason_backup": "BG_WALLET_SYNC"
                }
                db.upsert_position(pair, adopt_data)
                current_positions[pair] = adopt_data
                adopted_count += 1
                logging.info(f"[BgWalletSync] Adopted: {pair} ({amount_f} @ Rp {price:,.0f})")

            if adopted_count or removed_count:
                logging.info(f"[BgWalletSync] Selesai — adopted: {adopted_count}, removed: {removed_count}")

        except Exception as e:
            logging.warning(f"[BgWalletSync] Error (akan retry dalam 5m): {e}")

        socketio.sleep(300)  # 5 menit

socketio.start_background_task(_background_wallet_sync_loop)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8080, debug=False)
