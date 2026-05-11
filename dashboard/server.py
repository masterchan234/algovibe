import eventlet
eventlet.monkey_patch()
from eventlet.green import zmq as zmq_eventlet  # eventlet-native zmq (no gevent dependency)
import os
import sys
import time
import uuid
import hashlib
import datetime
import logging
import urllib.request
import xml.etree.ElementTree as ET
import threading
from collections import deque
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import psycopg2
import json
import jwt as pyjwt
from flask import Flask, jsonify, send_from_directory, request, session, redirect, url_for, abort, make_response, render_template, g
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
from shared.transport import IPC_EQUITY, IPC_MARKET, IPC_COMMAND
from shared.algovibe_pb2 import EquityUpdate, Tick, Trade  # type: ignore[attr-defined]
from werkzeug.middleware.proxy_fix import ProxyFix

# --- [ZMQ COMMAND PUBLISHER] ---
_ctx = zmq_eventlet.Context()
_cmd_pub = _ctx.socket(zmq_eventlet.PUB)
_cmd_pub.bind(IPC_COMMAND)
logging.info(f"📡 [IPC] Command Publisher bound to {IPC_COMMAND}")

def broadcast_command(action, payload=None):
    """Broadcast a command to all subscribers (e.g. executive-core)."""
    msg = {"action": action, "payload": payload or {}, "ts": time.time()}
    # Use multipart: [topic, payload] for ZMQSubscriber compatibility
    _cmd_pub.send_multipart([b"CMD", json.dumps(msg).encode()])
    logging.info(f"📤 [IPC] Broadcasted command: {action}")

# Exchange instance untuk public data (OHLCV)
exchange = ccxt.indodax()

app = Flask(__name__, static_folder='static/dist', static_url_path='/static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ── Pentest Fix: Custom JSON Encoder for Datetime ──
class CustomJSONProvider(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return super().default(obj)

app.json_encoder = CustomJSONProvider


# ── Pentest Fix: Secure Date Parsing ──
def _parse_iso_date(ts_val):
    """Safe ISO date parser for both string and datetime objects."""
    if ts_val is None:
        return None
    if isinstance(ts_val, datetime.datetime):
        return ts_val
    try:
        # Handle formats: '2026-05-08 20:00:00', '2026-05-08T20:00:00', etc.
        s = str(ts_val).replace(' ', 'T').replace('Z', '+00:00')
        return datetime.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None

# ── Pentest Fix: Custom JSON for SocketIO ──
class JSONUtil:
    @staticmethod
    def dumps(obj, **kwargs):
        return json.dumps(obj, cls=CustomJSONProvider, **kwargs)
    @staticmethod
    def loads(s, **kwargs):
        return json.loads(s, **kwargs)

@app.errorhandler(Exception)
def handle_exception(e):
    # Log the full traceback
    logging.exception(f"Unhandled Exception: {e}")
    # Return JSON for API routes
    if request.path.startswith('/api/'):
        return jsonify({"error": str(e), "path": request.path}), 500
    return "Internal Server Error", 500

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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=10, ping_interval=5, json=JSONUtil)

# --- [Manual CSRF Protection] ---
def generate_csrf():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

@app.before_request
def validate_csrf():
    # Only validate CSRF on POST/PUT/DELETE
    if request.method in ["POST", "PUT", "DELETE"]:
        # Special case for socket.io path (handled by socketio)
        if request.path.startswith('/socket.io'):
            return

        # JWT Bearer tokens are sent in the Authorization header (not cookies),
        # so they are inherently CSRF-safe.  Validating the Flask session token
        # here would always fail for stateless JWT clients because the Flask
        # session is empty.  Skip CSRF validation when a Bearer token is present.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:]
            if bearer and _decode_token(bearer) is not None:
                return

        token = request.headers.get("X-CSRFToken")
        if not token or token != session.get("_csrf_token"):
            logging.warning(f"🚫 CSRF Validation Failed for {request.method} {request.path}")
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

@socketio.on('ping_latency')
def handle_ping_latency(data):
    # logger.debug(f"Ping latency received: {data}")
    emit('pong_latency', data)

@socketio.on('connect')
def handle_connect(auth=None):
    # Handshake: JWT/session for authenticated users, optional explicit view-token for controlled guest access.
    client_token = auth.get('token') if auth else None

    is_authenticated = False
    is_guest = False
    session_id = None

    # 1. JWT auth — primary path for dashboard clients
    if client_token and client_token != generate_view_token():
        payload = _decode_token(client_token)
        if payload and payload.get('user_id'):
            sess_id = payload.get('session_id')
            if sess_id:
                if db._live.get_active_session(sess_id):
                    is_authenticated = True
                    session_id = sess_id
                    join_room(f'session_{session_id}')
                else:
                    logging.warning(f"🚫 WS connect rejected: session {sess_id[:8]} is no longer active")
                    return False
            else:
                # Legacy JWT without session_id — allow but without targeted room
                is_authenticated = True
            join_room('authenticated')
            join_room(f"user_{payload['user_id']}")
            logging.info(f"🔌 User '{payload.get('username')}' connected via JWT (session: {session_id or 'legacy'})")

    # 2. Legacy Flask session fallback
    elif 'user_id' in session:
        is_authenticated = True
        join_room('authenticated')
        join_room(f"user_{session['user_id']}")
        sess_id = session.get('session_id')
        if sess_id:
            session_id = sess_id
            join_room(f'session_{session_id}')
        logging.info(f"🔌 User '{session.get('username')}' connected via Flask session")

    # 3. View-only guest token
    elif client_token and client_token == generate_view_token():
        is_guest = True
        join_room('public')
        logging.info("🔌 Guest User connected (Public/View-only mode)")

    # 4. Anonymous access is blocked for security.
    else:
        logging.warning("🚫 Anonymous WS connection rejected")
        return False

    if not is_authenticated and not is_guest:
        logging.warning(f"🚫 Unauthorized WS connection attempt")
        return False

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
def _get_jwt_secret():
    return app.config['SECRET_KEY']

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", 24))

# Throttle session activity updates to avoid per-request DB writes (update at most every 30s)
_SESSION_ACTIVITY_THROTTLE: dict = {}  # session_id -> last_update_ts

def _get_client_ip() -> str:
    """Return real client IP, respecting X-Forwarded-For from Nginx Proxy Manager."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""

def _decode_token(token: str):
    """Decode and validate a JWT. Returns payload dict or None."""
    try:
        return pyjwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None

def _issue_token(user: dict, session_id: str = None) -> str:
    """Issue a signed JWT for a user record, embedding the session_id."""
    if session_id is None:
        session_id = str(uuid.uuid4())
    payload = {
        "user_id": user["id"],
        "username": user["username"],
        "role": user.get("role", "USER"),
        "session_id": session_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return pyjwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)

def _get_current_user_payload():
    """Return decoded JWT payload from request, or None if not authenticated.

    Checks (in order):
      1. Authorization: Bearer <token> header
      2. Legacy Flask session (backward-compat for non-JWT clients)
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return _decode_token(token)
    # Legacy session fallback
    if "user_id" in session:
        return {"user_id": session["user_id"], "username": session.get("username", ""), "role": "USER", "session_id": session.get("session_id")}
    return None

def _validate_session_in_db(session_id: str | None) -> bool:
    """Return True if session_id is active in the live DB (or if no session_id for legacy tokens)."""
    if not session_id:
        return True  # Legacy JWT without session_id — allow for backward-compat
    sess = db._live.get_active_session(session_id)
    if not sess:
        return False
    # Throttled activity update: write to DB at most every 30s per session
    now = time.time()
    if now - _SESSION_ACTIVITY_THROTTLE.get(session_id, 0) > 30:
        _SESSION_ACTIVITY_THROTTLE[session_id] = now
        db._live.update_session_activity(session_id)
    return True

def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        payload = _get_current_user_payload()
        if not payload:
            return jsonify({"error": "Unauthorized", "login_required": True}), 401
        if not _validate_session_in_db(payload.get("session_id")):
            return jsonify({"error": "Session expired or invalidated", "login_required": True}), 401
        return f(*args, **kwargs)
    return decorated_function

# Keep legacy alias so existing call-sites compiled before this change still work
login_required = token_required

# ── Default User Initialization ──
try:
    if not db.get_user('admin'):
        db.create_user('admin', 'admin123', os.getenv('INDODAX_API_KEY', ''), os.getenv('INDODAX_API_SECRET', ''))
        logging.info("✅ Default user 'admin' created (Pass: admin123). PLEASE CHANGE THIS!")
except Exception as e:
    logging.error(f"Error initializing default user: {e}")

from config import INITIAL_CAPITAL as _IC
# NOTE: cfg.DRY_RUN is imported dynamically inside functions to always read current value

# Default pair — akan diperbarui saat user memilih koin di dashboard
CURRENT_WATCH_PAIR = "btcidr"
# Pair khusus untuk kebutuhan market analysis (tetap BTC, tidak mengikuti watch pair UI)
ANALYSIS_PAIR = "btcidr"

# Realtime BTC tick buffer (source: IPC market stream from market-pipe).
# Stores tuples: (timestamp_ms, price). Kept for max 24h window.
_BTC_TICK_BUFFER = deque(maxlen=120000)
_BTC_TICK_LOCK = threading.Lock()
_BTC_WINDOW_24H_MS = 24 * 60 * 60 * 1000

# Inisialisasi Market WS untuk dashboard — tidak distart di sini; data ticker
# dibaca dari cache internal saat dibutuhkan oleh endpoint REST.
# Double ingress ke exchange dilarang keras di arsitektur microservice ini.
mws = MarketDataWS()
# NOTE: mws.start() TIDAK dipanggil — dashboard bukan ingress exchange.

_start_time = time.time()

from app.infrastructure.migrations import run_migrations, sync_schema_both_dbs

# Ensure both live and dryrun databases have up-to-date schema on startup
# try:
#     sync_schema_both_dbs()
# except Exception as _sync_err:
#     logging.warning(f"⚠️ sync_schema_both_dbs failed: {_sync_err}")

# Set untuk track DB path yang sudah dimigrasi di proses ini
# Prevents redundant migration runs on every request
_MIGRATED_DBS: set = set()

def get_db_connection():
    """Membuka koneksi SQLite ke DB aktif melalui DatabaseRouter."""
    # Ambil path dari database yang sedang aktif (live/dryrun)
    active_db = db._active()
    conn = active_db.get_raw_connection()
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
    [Senior Logic] ALWAYS FETCH FRESH from DB (no cache) to keep dashboard in sync with bot.
    """
    # ALWAYS fetch fresh from Database (no caching — balance changes every cycle)
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
                "source": "DATABASE_FRESH"
            }
    except Exception as e:
        logging.warning(f"⚠️ Failed to read balances from DB: {e}")

    # Fallback ke REST API (Jika DB kosong atau gagal)
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
            "SELECT history_json FROM ticker_history WHERE pair = %s",
            (pair,)
        ).fetchone()
        
        prices = []
        if row and row['history_json']:
            prices = json.loads(row['history_json'])
        
        tech = get_technical_summary(prices)
        
        # 2. Orderbook-based technicals (Imbalance & Spread)
        depth = conn.execute("SELECT price, vol, is_bid FROM market_depth WHERE pair = %s", (pair,)).fetchall()
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
            FROM trade_history WHERE dry_run = %s AND reason != 'BACKTEST'
        """, (bool(dry_val),)).fetchone()

        exits = conn.execute("""
            SELECT reason, COUNT(*) as cnt, ROUND(AVG(pnl_pct), 2) as avg_pct
            FROM trade_history WHERE dry_run = %s AND reason != 'BACKTEST'
            GROUP BY reason ORDER BY cnt DESC LIMIT 6
        """, (bool(dry_val),)).fetchall()

        top_pairs = conn.execute("""
            SELECT pair, COUNT(*) as trades, ROUND(SUM(pnl_idr), 0) as total_pnl
            FROM trade_history WHERE dry_run = %s AND reason != 'BACKTEST'
            GROUP BY pair ORDER BY total_pnl DESC LIMIT 5
        """, (bool(dry_val),)).fetchall()

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
_last_market_brief_llm_ts = 0.0
_last_market_brief_payload_hash = ""


def _extract_json_brief_text(raw_text: str) -> str | None:
    """Parse strict JSON response from LLM and extract brief text."""
    if not raw_text:
        return None

    text = str(raw_text).strip()

    # Direct JSON
    try:
        obj = json.loads(text)
        brief = str(obj.get("brief") or "").strip() if isinstance(obj, dict) else ""
        return brief or None
    except Exception:
        pass

    # JSON in codeblock
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    if m:
        try:
            obj = json.loads(m.group(1))
            brief = str(obj.get("brief") or "").strip() if isinstance(obj, dict) else ""
            return brief or None
        except Exception:
            pass

    # First balanced JSON object
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        brief = str(obj.get("brief") or "").strip() if isinstance(obj, dict) else ""
                        return brief or None
                    except Exception:
                        start = None
                        continue
    return None


def _request_market_brief_llm(brief_vars: dict):
    """Send full market variables to LLM and let LLM write MARKET INTELLIGENCE BRIEF."""
    global _last_market_brief_llm_ts, _last_market_brief_payload_hash

    worker = LLMWorker.get_instance()
    if not worker.is_active:
        return

    payload_json = json.dumps(brief_vars, ensure_ascii=False, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    now = time.time()

    # Avoid spamming LLM with identical payload in short windows.
    if (now - _last_market_brief_llm_ts) < 45 and payload_hash == _last_market_brief_payload_hash:
        return

    prompt = (
        "Anda adalah Market Intelligence Writer.\n"
        "Gunakan HANYA data JSON di bawah ini. Jangan menambah fakta dari luar, jangan asumsi, jangan halusinasi.\n"
        "Balas WAJIB dalam JSON valid SATU OBJEK dengan schema tepat berikut:\n"
        '{"brief":"..."}\n'
        "Nilai brief: 1 paragraf ringkas Bahasa Indonesia (maks 650 karakter) dengan format konten:\n"
        "BiasData | DriversData | PlanData | InvalidasiData\n"
        "PlanData dan InvalidasiData HARUS diturunkan dari variabel yang tersedia di DATA_JSON.\n"
        "Jika data kurang, tulis eksplisit 'data tidak cukup'.\n"
        "Jangan keluarkan teks lain di luar JSON.\n\n"
        f"DATA_JSON:\n{payload_json}"
    )

    worker.query_structured("market_intel_brief", prompt)
    _last_market_brief_llm_ts = now
    _last_market_brief_payload_hash = payload_hash

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
@app.route('/login')
@app.route('/mode-select')
@app.route('/dashboard-live')
@app.route('/dashboard-dry')
def index():
    response = make_response(send_from_directory(app.static_folder, 'index.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")


@app.route('/api/public/landing-data', methods=['GET'])
def public_landing_data():
    """Provides a public 'teaser' of market signals and AI insights for the landing page."""
    conn = None
    try:
        conn = get_db_connection()

        # 1. Signals — selalu dari live WebSocket/IPC (mws._tickers), bukan DB.
        ws_tickers_snap = mws.get_tickers()
        
        # Filter koin aktif (bukan stablecoin) dan memiliki volume transaksi minimum Rp 20 juta
        _STABLECOINS = {'usdt', 'usdc', 'busd', 'bidr', 'dai', 'tusd', 'idrt'}
        
        candidates = []
        for pair_key, data in ws_tickers_snap.items():
            vol_idr = float(data.get('vol_idr') or 0)
            if vol_idr < 20_000_000:  # Filter likuiditas min Rp 20 juta IDR
                continue
                
            base_coin = pair_key.lower().replace('idr', '').strip()
            if base_coin in _STABLECOINS:
                continue
                
            # Hitung change_24h dari posisi harga terakhir relatif terhadap midpoint range 24h.
            last_price = float(data.get('last') or 0)
            high_24h = float(data.get('high') or 0)
            low_24h = float(data.get('low') or 0)
            if high_24h > 0 and low_24h > 0 and high_24h > low_24h and last_price > 0:
                mid_24h = (high_24h + low_24h) / 2.0
                ch_24h = ((last_price - mid_24h) / mid_24h) * 100.0
            else:
                ch_24h = 0.0
                
            candidates.append((pair_key, data, ch_24h))
            
        # Urutkan berdasarkan pergerakan/volatilitas absolut (Top Movers) terbesar
        candidates.sort(key=lambda x: abs(x[2]), reverse=True)
        
        # Untuk kompatibilitas fallback dan log aktivitas publik di bawah
        _live_pairs = [(pair_key, data) for pair_key, data, ch_24h in candidates]
        
        signals = []
        for pair_key, data, ch_24h in candidates[:8]:
            last_price = float(data.get('last') or 0.0)
            if ch_24h > 1.0:
                trend = 'UP'
            elif ch_24h < -1.0:
                trend = 'DOWN'
            else:
                trend = 'SIDEWAYS'

            # Hitung technical indicators dari price history DB
            tech = {}
            try:
                _th_row = conn.execute(
                    "SELECT history_json FROM ticker_history WHERE pair = %s", (pair_key,)
                ).fetchone()
                _prices = json.loads(_th_row['history_json']) if _th_row and _th_row['history_json'] else []
                if len(_prices) >= 2:
                    tech = get_technical_summary(_prices)
                    tech['last_price'] = float(_prices[-1])
            except Exception:
                pass

            # Order book buy pressure
            try:
                _depth = conn.execute(
                    "SELECT vol, is_bid FROM market_depth WHERE pair = %s", (pair_key,)
                ).fetchall()
                _bid_vol = sum(r['vol'] for r in _depth if r['is_bid'] == 1)
                _ask_vol = sum(r['vol'] for r in _depth if r['is_bid'] == 0)
                _total_ob = _bid_vol + _ask_vol
                tech['buy_pressure'] = round(_bid_vol / _total_ob * 100, 1) if _total_ob > 0 else 50.0
            except Exception:
                tech['buy_pressure'] = 50.0

            # Derive multi-confluence trade signal untuk score & verdict
            trade_sig = derive_trade_signal(tech) if tech else {}

            signals.append({
                "pair": pair_key.upper().replace('IDR', ''),
                "score": trade_sig.get('buy_score', 0),
                "sell_score": trade_sig.get('sell_score', 0),
                "confidence": trade_sig.get('confidence', 50),
                "verdict": trade_sig.get('verdict', 'HOLD'),
                "trend": trend,
                "price": last_price,
                "change_24h": round(ch_24h, 2),
                # Indikator teknikal (11 indikator aktif)
                "rsi": tech.get('rsi', 50),
                "macd_hist": tech.get('macd_hist', 0),
                "bb_pct": tech.get('bb_pct', 0.5),
                "stoch_k": tech.get('stoch_k', 50),
                "stoch_d": tech.get('stoch_d', 50),
                "ema_cross": tech.get('ema_cross', 'NEUTRAL'),
                "williams_r": tech.get('williams_r', -50),
                "slope": tech.get('slope', 0),
                "roc": tech.get('roc', 0),
                "volume_ratio": tech.get('volume_ratio', 1.0),
                "obv_slope": tech.get('obv_slope', 0.0),
                "buy_pressure": tech.get('buy_pressure', 50.0),
                "signal": tech.get('signal', 'NEUTRAL'),
            })

        # 2. Market Stats — BTC price selalu dari live WS, fallback ke bot_state DB
        sentiment = int(db.get_state("market_sentiment_index") or 50)
        _btc_live = ws_tickers_snap.get('btcidr', {})
        btc_price = float(_btc_live.get('last') or 0) or float(db.get_state("last_price_btcidr") or 0)
        regime = db.get_state("current_market_regime") or "SCANNING"

        # 3. MARKET INTELLIGENCE BRIEF (LLM-backed, real-data variables)
        llm_feed = _build_llm_decisions_payload(limit=5)
        brief_items = [
            {
                "message": str(item.get("message") or "").strip(),
                "created_at": str(item.get("created_at") or "")
            }
            for item in llm_feed
            if str(item.get("side") or "") == "MARKET_INTEL_BRIEF"
               and str(item.get("message") or "").strip()
        ]

        # Fallback: generate live insights dari WS snapshot ketika LLM brief tidak tersedia.
        if brief_items:
            insights = brief_items[:5]
        else:
            now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _STABLECOIN_BASES_FB = {'usdt', 'usdc', 'busd', 'bidr', 'dai', 'tusd'}
            _crypto_fb = [
                (k, v) for k, v in _live_pairs
                if k.lower().replace('idr', '') not in _STABLECOIN_BASES_FB
            ]
            def _mid_ch_fb(v):
                last = float(v.get('last') or 0)
                high = float(v.get('high') or 0)
                low = float(v.get('low') or 0)
                if high > 0 and low > 0 and high > low and last > 0:
                    mid = (high + low) / 2.0
                    return round(((last - mid) / mid) * 100.0, 2)
                return float(v.get('change_24h') or 0)
            _scored_fb = [(k.upper().replace('IDR', ''), _mid_ch_fb(v)) for k, v in _crypto_fb]
            _fb_gainer = max(_scored_fb, key=lambda x: x[1], default=("N/A", 0.0))
            _fb_loser = min(_scored_fb, key=lambda x: x[1], default=("N/A", 0.0))
            _fb_trend = max(_scored_fb, key=lambda x: abs(x[1]), default=("N/A", 0.0))
            btc_str = f" | BTC/IDR {btc_price:,.0f}" if btc_price and btc_price > 0 else ""
            regime_lbl = "Warming up" if regime == "SCANNING" else regime
            movers_fb = " | ".join(p for p in [
                f"↑ {_fb_gainer[0]} {_fb_gainer[1]:+.2f}%" if _fb_gainer[0] != "N/A" else "",
                f"↓ {_fb_loser[0]} {_fb_loser[1]:+.2f}%" if _fb_loser[0] != "N/A" else "",
                f"🔥 {_fb_trend[0]} {_fb_trend[1]:+.2f}%" if _fb_trend[0] != "N/A" else "",
            ] if p)
            insights = [
                {
                    "message": (
                        f"Market live: {len(_live_pairs)} pair aktif"
                        f"{btc_str}"
                        f" | Regime {regime_lbl} | Sentimen {sentiment}%"
                        + (f" | {movers_fb}" if movers_fb else "")
                    ),
                    "created_at": now_ts
                }
            ]
        
        # 4. Intelligence Reports
        art_rows = conn.execute(
            "SELECT title, excerpt, content, category, created_at FROM intelligence_reports "
            "ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        articles = [{
            "title": r['title'],
            "excerpt": r['excerpt'],
            "content": r['content'],
            "category": r['category'],
            "created_at": str(r['created_at'])
        } for r in art_rows]

        # 4. Public activity logs — selalu dari live signals (tidak bergantung DB events).
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        stream_ts = now_utc.isoformat()
        public_logs = []
        for pair_key, data in _live_pairs[:8]:
            last = float(data.get('last') or 0)
            high = float(data.get('high') or 0)
            low = float(data.get('low') or 0)
            if high > 0 and low > 0 and high > low and last > 0:
                mid = (high + low) / 2.0
                ch = round(((last - mid) / mid) * 100.0, 2)
            else:
                ch = 0.0
            vol = float(data.get('vol_idr') or 0)
            pair_label = pair_key.upper().replace('IDR', '')
            trend_label = 'UP' if ch > 1.0 else ('DOWN' if ch < -1.0 else 'SIDEWAYS')
            public_logs.append({
                "time": stream_ts,
                "message": f"📡 Live: {pair_label} {ch:+.2f}% vs midrange | Vol IDR {vol/1e6:.1f}M | {trend_label}"
            })

        response = jsonify({
            "success": True,
            "signals": signals[:8],
            "insights": insights,
            "articles": articles,
            "public_logs": public_logs,
            "market": {
                "sentiment": int(sentiment),
                "btc_price": float(btc_price),
                "regime": regime
            }
        })
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        import traceback
        logging.error(f"[LandingData] Global Error: {e}")
        logging.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return jsonify({"status": "Login endpoint ready. Use POST to authenticate."})
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = db.verify_user(username, password)
    if not user:
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    # ── Single Login Enforcement ──
    # Invalidate all existing active sessions for this user and force-logout those clients.
    old_sessions = db._live.get_active_sessions_for_user(user['id'])
    for old_sess in old_sessions:
        old_sid = old_sess['session_id']
        socketio.emit('FORCE_LOGOUT', {"reason": "logged_in_elsewhere"}, room=f'session_{old_sid}')
        db._live.deactivate_session(old_sid)
        logging.info(f"🔐 Force-logout emitted to session {old_sid[:8]}... (user: {user['username']})")

    # ── Create new session ──
    session_id = str(uuid.uuid4())
    ip_address = _get_client_ip()
    user_agent = request.headers.get('User-Agent', '')
    db._live.create_user_session(session_id, user['id'], ip_address, user_agent)
    logging.info(f"✅ New session created for '{user['username']}' from {ip_address} (session: {session_id[:8]}...)")

    token = _issue_token(user, session_id)
    # Also set legacy Flask session for WebSocket auth and backward-compat
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['session_id'] = session_id
    return jsonify({
        "success": True,
        "token": token,
        "user": {"username": user['username'], "role": user['role']}
    })

@app.route('/api/logout', methods=['POST'])
@login_required
def logout_api():
    payload = _get_current_user_payload()
    if payload and payload.get("session_id"):
        db._live.deactivate_session(payload["session_id"])
        _SESSION_ACTIVITY_THROTTLE.pop(payload["session_id"], None)
    session.clear()
    return jsonify({"success": True})

@app.route('/api/check-auth')
@login_required
def check_auth():
    payload = _get_current_user_payload()
    if payload:
        return jsonify({"authenticated": True, "username": payload.get("username", "")})
    return jsonify({"authenticated": False})

@app.route('/api/refresh-token', methods=['POST'])
def refresh_token():
    """Issue a new JWT given a valid (non-expired) token."""
    payload = _get_current_user_payload()
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    user = db.get_user(payload.get("username", ""))
    if not user:
        return jsonify({"error": "User not found"}), 404
    # Preserve the existing session_id so the DB session remains valid
    new_token = _issue_token(user, session_id=payload.get("session_id"))
    return jsonify({"success": True, "token": new_token})

@app.route('/api/view-token')
@login_required
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
@login_required
def get_stats():
    """
    Endpoint utama untuk KPI dashboard. 
    Menggunakan _build_stats_payload untuk konsistensi antara REST API dan WebSocket.
    """
    try:
        payload = _build_stats_payload()
        return jsonify(payload)
    except Exception as e:
        logging.error(f"Error in get_stats route: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/positions')
@login_required
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
                    "SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)
                ).fetchone()
                if row and row['history_json']:
                    history = json.loads(row['history_json'])
                    if history and entry_price > 0:
                        last_price = float(history[-1])
                        pnl_idr = (last_price - entry_price) * amount_crypto
                        pnl_pct = ((last_price - entry_price) / entry_price) * 100
            except Exception:
                pass

            exec_guard = _estimate_market_sell_net_from_orderbook(pair, amount_crypto, entry_price, amount_idr)
            fee_buy  = amount_idr * cfg.FEE_BUY_TAKER
            fee_sell = (last_price * amount_crypto) * cfg.FEE_SELL_TAKER
            pnl_net_idr = exec_guard['net_pnl_idr'] if exec_guard.get('ok') else (pnl_idr - fee_buy - fee_sell)
            pnl_net_pct = (pnl_net_idr / amount_idr * 100) if amount_idr > 0 else 0.0

            # Durasi posisi (gunakan datetime yang sudah di-import di atas)
            duration_sec = 0
            try:
                ts = pos['timestamp']
                if ts:
                    opened_at = _parse_iso_date(ts)
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
                "pnl_net_pct": round(pnl_net_pct, 3),
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


def _estimate_market_sell_net_from_orderbook(pair, amount, entry_price, amount_idr, depth_limit=20):
    """Estimate full-fill market sell PnL from live bid depth.

    Mirrors the bot-side exit guard: consume the bid stack from best bid down,
    then apply taker fees to estimate the actual net result of a market sell.
    """
    import config as cfg

    amount = float(amount or 0)
    entry_price = float(entry_price or 0)
    amount_idr = float(amount_idr or 0)
    if amount <= 0 or entry_price <= 0:
        return {
            "ok": False,
            "reason": "invalid_input",
            "filled_amount": 0.0,
            "avg_price": 0.0,
            "sell_value": 0.0,
            "net_pnl_idr": 0.0,
            "fee_buy": 0.0,
            "fee_sell": 0.0,
        }

    bids = []
    try:
        live_depth = mws.get_market_depth(pair) or {}
        bids = list(live_depth.get('bids') or [])
    except Exception:
        bids = []

    if not bids:
        try:
            db_depth = db.get_market_depth(pair) or {}
            bids = list(db_depth.get('bids') or [])
        except Exception:
            bids = []

    if not bids:
        return {
            "ok": False,
            "reason": "no_orderbook",
            "filled_amount": 0.0,
            "avg_price": 0.0,
            "sell_value": 0.0,
            "net_pnl_idr": 0.0,
            "fee_buy": amount_idr * cfg.FEE_BUY_TAKER,
            "fee_sell": 0.0,
        }

    remaining = amount
    filled = 0.0
    sell_value = 0.0
    for lvl in bids[:depth_limit]:
        px = float(lvl.get('price') or 0)
        vol = float(lvl.get('vol') or 0)
        if px <= 0 or vol <= 0:
            continue
        take = min(remaining, vol)
        sell_value += take * px
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break

    if filled <= 0:
        return {
            "ok": False,
            "reason": "zero_fill",
            "filled_amount": 0.0,
            "avg_price": 0.0,
            "sell_value": 0.0,
            "net_pnl_idr": 0.0,
            "fee_buy": amount_idr * cfg.FEE_BUY_TAKER,
            "fee_sell": 0.0,
        }

    if remaining > 1e-8:
        return {
            "ok": False,
            "reason": "insufficient_bid_depth",
            "filled_amount": filled,
            "avg_price": sell_value / filled,
            "sell_value": sell_value,
            "net_pnl_idr": 0.0,
            "fee_buy": amount_idr * cfg.FEE_BUY_TAKER,
            "fee_sell": sell_value * cfg.FEE_SELL_TAKER,
        }

    avg_price = sell_value / filled
    fee_buy = amount_idr * cfg.FEE_BUY_TAKER
    fee_sell = sell_value * cfg.FEE_SELL_TAKER
    gross_pnl = (avg_price - entry_price) * filled
    net_pnl_idr = gross_pnl - (fee_buy + fee_sell)

    return {
        "ok": True,
        "reason": "ok",
        "filled_amount": filled,
        "avg_price": avg_price,
        "sell_value": sell_value,
        "net_pnl_idr": net_pnl_idr,
        "fee_buy": fee_buy,
        "fee_sell": fee_sell,
    }


@app.route('/api/llm-decisions')
@login_required
def get_llm_decisions():
    """Riwayat keputusan LiteRT-LM (LLM_EXIT_SIGNAL dari system_events)."""
    return jsonify(_build_llm_decisions_payload())


@app.route('/api/pnl-history/<tf>')
@login_required
def get_pnl_history_api(tf):
    """
    Menyusun histori ekuitas (PnL kumulatif) dari database trade_history.
    """
    conn = get_db_connection()
    try:
        import config as cfg
        dr_val = 1 if _is_dry_run() else 0
        
        tf_map = {
            '1h': '-1 hours',
            '24h': '-24 hours',
            '7d': '-7 days'
        }
        interval = tf_map.get(tf, '-24 hours')
        
        base_row = conn.execute(
            "SELECT SUM(pnl_idr) as total FROM trade_history WHERE created_at < NOW() + (%s)::interval AND (dry_run = %s OR is_manual = 1) AND reason != 'BACKTEST'",
            (interval, dr_val)
        ).fetchone()
        current_pnl = float(base_row['total'] or 0.0)
        
        trades = conn.execute(
            "SELECT pnl_idr, created_at FROM trade_history WHERE created_at >= NOW() + (%s)::interval AND (dry_run = %s OR is_manual = 1) AND reason != 'BACKTEST' ORDER BY created_at ASC",
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
@login_required
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
                    "SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)
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
@login_required
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


@app.route('/api/orders')
@login_required
def get_orders():
    """Return recent live order activity, falling back to positions/trade history when order_book is empty."""
    try:
        live_conn = db._live.get_raw_connection()
        rows = live_conn.execute(
            "SELECT order_id, pair, side, price, amount, status, created_at, updated_at "
            "FROM order_book ORDER BY created_at DESC LIMIT 20"
        ).fetchall()

        if rows:
            payload = [dict(r) for r in rows]
            live_conn.close()
            return jsonify(payload)

        position_rows = live_conn.execute(
            "SELECT pair, entry_order_id, entry_price, amount_crypto, amount_idr, status, timestamp "
            "FROM positions "
            "WHERE UPPER(COALESCE(status, '')) IN ('OPEN', 'EXITING', 'PENDING_BUY') "
            "ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()

        trade_rows = live_conn.execute(
            "SELECT pair, exit_order_id, exit_price, amount_crypto, amount_idr, reason, created_at "
            "FROM trade_history "
            "WHERE COALESCE(exit_order_id, '') <> '' AND COALESCE(dry_run, false) = false "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        live_conn.close()

        fallback = []
        for row in position_rows:
            fallback.append({
                "order_id": row['entry_order_id'] or f"open:{row['pair']}:{row['timestamp']}",
                "pair": row['pair'],
                "side": "buy",
                "price": row['entry_price'],
                "amount": row['amount_crypto'] or row['amount_idr'],
                "status": row['status'] or 'OPEN',
                "created_at": str(row['timestamp'] or ''),
                "updated_at": str(row['timestamp'] or ''),
                "source": "positions",
            })

        for row in trade_rows:
            fallback.append({
                "order_id": row['exit_order_id'] or f"exit:{row['pair']}:{row['created_at']}",
                "pair": row['pair'],
                "side": "sell",
                "price": row['exit_price'],
                "amount": row['amount_crypto'] or row['amount_idr'],
                "status": row['reason'] or 'CLOSED',
                "created_at": str(row['created_at'] or ''),
                "updated_at": str(row['created_at'] or ''),
                "source": "trade_history",
            })

        fallback.sort(key=lambda item: item.get('created_at') or '', reverse=True)
        return jsonify(fallback[:20])
    except Exception as e:
        logging.warning(f"[/api/orders] {e}")
        return jsonify([])


@app.route('/api/live/state')
@login_required
def get_live_state():
    """Return persistent live-engine state for dashboard state-recovery on page load.

    Reads from the live database so the value reflects the most recent bot write
    regardless of which mode the dashboard is currently displaying.
    """
    try:
        live_conn = db._live.get_raw_connection()
        rows = live_conn.execute(
            "SELECT key, value FROM bot_state WHERE key IN "
            "('target_protection_active', 'peak_net_pnl_session', 'momentum_state', "
            "'session_mode', 'session_latched', "
            "'net_zero_survival_active', 'net_pnl_velocity', "
            "'net_pnl_realized', 'net_pnl_unrealized')"
        ).fetchall()
        live_conn.close()
        state = {r['key']: r['value'] for r in rows}
        return jsonify({
            "target_protection_active": state.get("target_protection_active", "false") == "true",
            "peak_net_pnl_session": float(state.get("peak_net_pnl_session") or 0),
            "momentum_state": state.get("momentum_state", "STALLING"),
            "session_mode": state.get("session_mode", "NORMAL"),
            "is_latched": (
                (state.get("session_latched", "false") == "true")
                or (state.get("session_mode", "NORMAL") == "HARVEST_ONLY")
            ),
            "bot_offline": False,
            "net_zero_survival_active": state.get("net_zero_survival_active", "false") == "true",
            "net_pnl_velocity": float(state.get("net_pnl_velocity") or 0),
            "net_pnl_realized": float(state.get("net_pnl_realized") or 0),
            "net_pnl_unrealized": float(state.get("net_pnl_unrealized") or 0),
        })
    except Exception as e:
        logging.warning(f"[/api/live/state] {e}")
        return jsonify({
            "target_protection_active": False,
            "peak_net_pnl_session": 0,
            "momentum_state": "STALLING",
            "session_mode": "NORMAL",
            "is_latched": False,
            "bot_offline": True,
            "net_zero_survival_active": False,
            "net_pnl_velocity": 0,
            "net_pnl_realized": 0,
            "net_pnl_unrealized": 0,
        })


# ── Mode-specific API aliases ─────────────────────────────────────────────────
# Routes under /api/dry/* and /api/live/* are thin wrappers that set a
# per-request DRY_RUN override via Flask's `g` object, so that individual
# requests see a specific mode without mutating the shared global config
# (which would cause race conditions under eventlet concurrency).

def _is_dry_run() -> bool:
    """Return effective DRY_RUN value for the current request.
    Respects g._dry_run_override when set by mode-specific aliases.
    Falls back to cfg.DRY_RUN when called outside a request/app context
    (e.g. from background tasks like push_data_loop)."""
    import config as _cfg
    try:
        return getattr(g, '_dry_run_override', _cfg.DRY_RUN)
    except RuntimeError:
        # Outside request context (background task) — use global config directly
        return _cfg.DRY_RUN

@app.route('/api/dry/stats')
def get_dry_stats():
    """Stats endpoint forcing Dry-Run database view."""
    g._dry_run_override = True
    return get_stats()

@app.route('/api/live/stats')
def get_live_stats_alias():
    """Stats endpoint forcing Live database view (auth required)."""
    if _get_current_user_payload() is None:
        return jsonify({"error": "Unauthorized"}), 401
    g._dry_run_override = False
    return get_stats()

@app.route('/api/dry/positions')
def get_dry_positions():
    """Positions from dry-run DB."""
    g._dry_run_override = True
    return get_positions()

@app.route('/api/live/positions')
@login_required
def get_live_positions():
    """Positions from live DB (auth required)."""
    g._dry_run_override = False
    return get_positions()

@app.route('/api/dry/pnl-history/<tf>')
def get_dry_pnl_history(tf):
    """PnL history from dry-run DB."""
    g._dry_run_override = True
    return get_pnl_history_api(tf)

@app.route('/api/live/pnl-history/<tf>')
@login_required
def get_live_pnl_history(tf):
    """PnL history from live DB (auth required)."""
    g._dry_run_override = False
    return get_pnl_history_api(tf)

# ─────────────────────────────────────────────────────────────────────────────


@app.route('/api/market-context/<pair>')
@login_required
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
            "SELECT price, vol, is_bid FROM market_depth WHERE pair = %s ORDER BY price DESC", 
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
@login_required
def trigger_analysis(pair):
    """Trigger LLM analysis for a specific pair on-demand."""
    db.save_state("trigger_analysis_pair", pair.lower())
    return jsonify({"status": "requested", "pair": pair})

@app.route('/api/reasoning/<pair>')
@login_required
def get_pair_reasoning(pair):
    """Ambil nalar AI/LLM terbaru untuk koin tertentu."""
    p = pair.lower().strip()
    reasoning = db.get_state(f"llm_reasoning_{p}")
    if not reasoning:
        # Fallback ke technical reasoning jika LLM belum ada
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (p,)).fetchone()
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
                "SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)
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

@app.route('/api/mode/current')
@login_required
def get_current_mode():
    """Return the engine's active mode directly from DB — the single source of truth.
    Frontend calls this on login to sync localStorage with actual engine state."""
    raw = db.get_state('dry_run')
    if raw is None:
        is_dry = True  # safe default
    else:
        is_dry = str(raw).lower() in ('1', 'true')
    return jsonify({
        'mode': 'DRY' if is_dry else 'LIVE',
        'dry_run': is_dry,
        'technical_only_mode': bool(cfg.TECHNICAL_ONLY_MODE),
    })

@app.route('/api/bot/select-mode', methods=['POST'])
@login_required
def select_mode():
    """
    Explicitly select operational mode (LIVE vs DRY).
    """
    import config as cfg
    data = request.get_json()
    mode = data.get('mode') # 'LIVE' or 'DRY'
    
    if mode not in ['LIVE', 'DRY']:
        return jsonify({"success": False, "message": "Invalid mode selection"}), 400
    
    target_dry_run = (mode == 'DRY')
    
    # Update config (In-memory for current process)
    cfg.DRY_RUN = target_dry_run
    # Update DB state for persistent retrieval by other engines
    db.save_state("dry_run", "1" if target_dry_run else "0")
    
    # Sync with Executive Core via ZMQ
    broadcast_command("MODE_CHANGE", {"dry_run": target_dry_run})
    
    logged_out = False

    # Push stats_update langsung agar frontend tidak tunggu 5s push loop
    try:
        socketio.emit('stats_update', _build_stats_payload())
        socketio.emit('trades_update', _build_trades_update_packet())
    except:
        pass

    return jsonify({
        "success": True, 
        "mode": mode, 
        "dry_run": target_dry_run, 
        "logged_out": logged_out
    })

@app.route('/api/bot/toggle-loose-gates', methods=['POST'])
@login_required
def toggle_loose_gates():
    """Toggle mode LOOSE GATES untuk testing."""
    import config as cfg
    current_val = db.get_state("dry_run_loose_gates") == "true"
    new_val = not current_val
    
    db.save_state("dry_run_loose_gates", str(new_val).lower())
    cfg.clear_dry_run_cache() # Gunakan cache yang sama untuk sinkronisasi
    logging.info(f"🔄 [DYNAMIC GATES] User changed loose gates to: {new_val}")
    return jsonify({"success": True, "loose_gates": new_val})

@app.route('/api/bot/toggle-technical-only', methods=['POST'])
@login_required
def toggle_technical_only_mode():
    """Toggle TECHNICAL_ONLY_MODE runtime via bot_state (no restart required)."""
    data = request.get_json(silent=True) or {}
    requested = data.get('technical_only_mode')

    current_val = bool(cfg.TECHNICAL_ONLY_MODE)
    if requested is None:
        new_val = not current_val
    else:
        if isinstance(requested, bool):
            new_val = requested
        else:
            new_val = str(requested).lower() in ('1', 'true', 'yes', 'on')

    db.save_state("technical_only_mode", "true" if new_val else "false")
    cfg.clear_dry_run_cache()
    broadcast_command("STATE_UPDATE", {"key": "technical_only_mode", "value": "true" if new_val else "false"})

    logging.info(f"🔧 [TECHNICAL_ONLY_MODE] Updated to: {new_val}")
    return jsonify({"success": True, "technical_only_mode": new_val})

@app.route('/api/bot/unblock', methods=['POST'])
@login_required
def unblock_bot():
    """Reset consecutive_losses counter to lift GLOBAL PAUSE and allow new entries."""
    try:
        current = int(db.get_state("consecutive_losses", 0) or 0)
        db.save_state("consecutive_losses", "0")
        mode = "DRY" if _is_dry_run() else "LIVE"
        logging.info(f"[Dashboard] ✅ Bot unblocked ({mode}): consecutive_losses reset from {current} → 0")
        # Push fresh stats so the frontend badge clears immediately
        try:
            socketio.emit('stats_update', _build_stats_payload())
        except Exception:
            pass
        return jsonify({"success": True, "prev_losses": current, "consecutive_losses": 0, "mode": mode})
    except Exception as e:
        logging.error(f"[Dashboard] Unblock failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
@app.route('/api/bot/update-state', methods=['POST'])
@login_required
def update_bot_state():
    """Manual override for bot state (Harvest Only, reset losses, etc)."""
    data = request.json
    key = data.get('key')
    value = data.get('value')
    
    if not key or value is None:
        return jsonify({"error": "Missing key or value"}), 400
        
    try:
        if key == 'daily_target_hit' and str(value) == '0':
            # [Smart Reset] User wants to continue trading.
            # Move the goalpost so automation doesn't re-latch immediately.
            try:
                stats = _build_stats_payload()
                current_pnl = float(stats.get('net_pnl', 0))
                # Add a buffer (Rp 10k or 0.1% of capital, whichever is higher)
                new_target = current_pnl + 10000 
                db.save_state("daily_profit_target", str(new_target))
                db.save_state("session_mode", "SCALPING")
                db.save_state("session_latched", "false")
                logging.info(f"🎯 [Manual Target Reset] Moving target to Rp {new_target:,.0f} and resuming SCALPING.")
            except Exception as _re:
                logging.warning(f"Could not move goalpost during reset: {_re}")

        db.save_state(key, value)
        # Broadcast command to notify executive-core of state change
        broadcast_command("STATE_UPDATE", {"key": key, "value": value})
        
        logging.info(f"🛠️ [Manual Override] Updated {key} to {value}")
        return jsonify({"status": "ok", "key": key, "value": value})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/bot-status')
@login_required
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
                updated = _parse_iso_date(last_state['updated_at'])
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

        # Full Bot State from DB
        all_states = {row['key']: row['value'] for row in conn.execute("SELECT key, value FROM bot_state").fetchall()}

        return jsonify({
            "is_running": bot_alive,
            "dry_run": bool(cfg.DRY_RUN),
            "technical_only_mode": bool(cfg.TECHNICAL_ONLY_MODE),
            "uptime_sec": uptime_sec,
            "last_heartbeat": last_heartbeat,
            "last_trade_at": last_trade_at,
            "llm": llm_stats,
            "session_mode": all_states.get("session_mode", "N/A"),
            "consecutive_losses": int(all_states.get("consecutive_losses", 0)),
            "daily_target_hit": all_states.get("daily_target_hit") == "1",
            "bot_state": all_states
        })
    except Exception as e:
        return jsonify({"is_running": False, "dry_run": bool(cfg.DRY_RUN), "technical_only_mode": bool(cfg.TECHNICAL_ONLY_MODE), "uptime_sec": 0})
    finally:
        conn.close()

@app.route('/api/history/<pair>')
@login_required
def get_pair_history(pair):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
        if row and row['history_json']:
            return jsonify(json.loads(row['history_json']))
        return jsonify([])
    except Exception:
        return jsonify([])
    finally:
        conn.close()

@app.route('/api/ohlcv/<pair>/<tf>')
@login_required
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
@login_required
def sync_trades_status():
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
@login_required
def get_trades():
    return jsonify(_build_trades_payload())

@app.route('/api/radar')
@login_required
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
                    "trend": row['trend'] if row['trend'] not in ('BACKFILLED', None, '') else 'N/A'
                })
            except: continue
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/radar-audit')
@login_required
def get_radar_audit():
    conn = None
    try:
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT key, value, updated_at
            FROM bot_state
            WHERE key LIKE %s
               OR key LIKE %s
               OR key LIKE %s
            """,
            ('radar_reco_%', 'gate_skip_%', 'llm_veto_ts_%')
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
            
            # Robust datetime extraction from updated_at column
            ts = r['updated_at']
            if ts and not isinstance(ts, datetime.datetime):
                try:
                    ts = _parse_iso_date(ts)
                except Exception:
                    ts = None
            
            if not ts:
                continue
                
            # Normalise to naive for safe comparison with datetime.datetime.now()
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)

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
                    # llm_veto_ts_ value is also a timestamp string
                    v_ts = _parse_iso_date(val)
                    if v_ts.tzinfo is not None:
                        v_ts = v_ts.replace(tzinfo=None)
                    state['veto_ts'] = v_ts
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
        logging.exception("Radar Audit Error:")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/logs')
@login_required
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
@login_required
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
    dist_dir = app.static_folder
    full = os.path.join(dist_dir, path)
    if os.path.exists(full):
        return send_from_directory(dist_dir, path)
    # Fallback: serve Vue SPA index.html for client-side routing
    response = make_response(send_from_directory(dist_dir, 'index.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@socketio.on('watch_pair')
def handle_watch_pair(data):
    global CURRENT_WATCH_PAIR
    pair = data.get('pair', CURRENT_WATCH_PAIR).lower().strip()
    if not pair:
        return
    CURRENT_WATCH_PAIR = pair
    db.save_state('current_watch_pair', pair)
    db.save_state('trigger_analysis_pair', pair)
    
    # [Fix] Aktifkan subscibe trades & OB untuk pair yang sedang dipantau
    try:
        mws.subscribe_market_trades(pair)
        mws._subscribe_specific_ob(pair)
    except Exception as e:
        logging.warning(f"Failed to subscribe dynamic data for {pair}: {e}")


def _pct_change_from_history(history, lookback):
    """Compute percentage change from an in-memory price history list."""
    try:
        if not isinstance(history, list) or len(history) < 2:
            return 0.0
        curr = float(history[-1])
        ref = float(history[-lookback]) if len(history) >= lookback else float(history[0])
        if ref <= 0:
            return 0.0
        return ((curr - ref) / ref) * 100.0
    except Exception:
        return 0.0


def _append_btc_tick(ts_ms, price):
    """Append BTC tick to rolling in-memory buffer and prune entries older than 24h."""
    try:
        ts_ms = int(ts_ms)
        price = float(price)
        if ts_ms <= 0 or price <= 0:
            return
        cutoff = ts_ms - _BTC_WINDOW_24H_MS
        with _BTC_TICK_LOCK:
            _BTC_TICK_BUFFER.append((ts_ms, price))
            while _BTC_TICK_BUFFER and _BTC_TICK_BUFFER[0][0] < cutoff:
                _BTC_TICK_BUFFER.popleft()
    except Exception:
        return


def _pct_change_from_time_window(points, window_ms):
    """Compute percentage change using oldest point inside the requested time window."""
    try:
        if not points or len(points) < 2:
            return 0.0
        last_ts, last_px = points[-1]
        target_ts = int(last_ts) - int(window_ms)
        ref_px = float(points[0][1])
        for ts, px in points:
            if int(ts) >= target_ts:
                ref_px = float(px)
                break
        if ref_px <= 0:
            return 0.0
        return ((float(last_px) - ref_px) / ref_px) * 100.0
    except Exception:
        return 0.0


def _get_btc_realtime_metrics():
    """Build BTC momentum metrics from IPC tick buffer (5m/15m/1h/24h + trend)."""
    try:
        with _BTC_TICK_LOCK:
            points = list(_BTC_TICK_BUFFER)
        if len(points) < 2:
            return {
                "has_data": False,
                "price": 0.0,
                "ch_5m": 0.0,
                "ch_15m": 0.0,
                "ch_1h": 0.0,
                "ch_24h": 0.0,
                "trend": "N/A",
                "age_sec": None,
            }

        _, last_px = points[-1]
        last_ts = int(points[-1][0])
        prices = [float(p[1]) for p in points]

        ch_5m = _pct_change_from_time_window(points, 5 * 60 * 1000)
        ch_15m = _pct_change_from_time_window(points, 15 * 60 * 1000)
        ch_1h = _pct_change_from_time_window(points, 60 * 60 * 1000)
        ch_24h = _pct_change_from_time_window(points, _BTC_WINDOW_24H_MS)

        trend = _derive_trend_from_history(prices)
        if trend == "N/A":
            if ch_1h > 0.2:
                trend = "UP"
            elif ch_1h < -0.2:
                trend = "DOWN"
            else:
                trend = "SIDEWAYS"

        age_sec = int(max(0, (int(time.time() * 1000) - last_ts) / 1000))

        return {
            "has_data": True,
            "price": float(last_px),
            "ch_5m": float(ch_5m),
            "ch_15m": float(ch_15m),
            "ch_1h": float(ch_1h),
            "ch_24h": float(ch_24h),
            "trend": str(trend),
            "age_sec": age_sec,
        }
    except Exception:
        return {
            "has_data": False,
            "price": 0.0,
            "ch_5m": 0.0,
            "ch_15m": 0.0,
            "ch_1h": 0.0,
            "ch_24h": 0.0,
            "trend": "N/A",
            "age_sec": None,
        }


def _derive_trend_from_history(history):
    """Lightweight trend derivation from EMA-like moving averages."""
    try:
        if not isinstance(history, list) or len(history) < 20:
            return "N/A"
        avg_20 = sum(float(x) for x in history[-20:]) / 20.0
        avg_50 = (
            sum(float(x) for x in history[-50:]) / 50.0
            if len(history) >= 50
            else avg_20
        )
        if avg_20 > (avg_50 * 1.001):
            return "UP"
        if avg_20 < (avg_50 * 0.999):
            return "DOWN"
        return "SIDEWAYS"
    except Exception:
        return "N/A"

def _build_stats_payload(force_dry=None):
    """Bangun payload stats langsung tanpa melalui Flask request context."""
    import config as cfg
    # Determine which mode we are building for
    dr_val = force_dry if force_dry is not None else bool(cfg.DRY_RUN)
    
    # Use the appropriate DB instance
    db_inst = db.dryrun if dr_val else db.live
    conn = db_inst.get_raw_connection()

    try:
        # Load bot_state from the TARGET database
        stats_dict = {r['key']: r['value'] for r in conn.execute("SELECT * FROM bot_state").fetchall()}
        positions  = conn.execute("SELECT * FROM positions").fetchall()
        perf       = db_inst.get_daily_stats(dry_run=dr_val)
        
        # 1. LIVE STATS (Real Money) - use proper DB method for all-time stats
        live_stats = db.live.get_all_time_stats(dry_run=False)
        
        # 2. DRY RUN STATS (Live Simulation) - use proper DB method for all-time stats
        dry_stats = db.dryrun.get_all_time_stats(dry_run=True)

        # 3. BACKTEST STATS (AI Knowledge / DNA)
        dna_row = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_idr) as total_pnl FROM trade_history WHERE reason = 'BACKTEST'"
        ).fetchone()
        # Convert row to standard dict format for consistency
        dna_stats = {
            'total_trades': dna_row['trades'] if dna_row else 0,
            'win_count': dna_row['wins'] if dna_row else 0,
            'total_pnl_idr': float(dna_row['total_pnl'] or 0) if dna_row else 0.0
        } if dna_row else {}

        win_rate = (live_stats['win_count'] / live_stats['total_trades'] * 100) if (live_stats and live_stats['total_trades'] > 0) else 0
        dry_win_rate = (dry_stats['win_count'] / dry_stats['total_trades'] * 100) if (dry_stats and dry_stats['total_trades'] > 0) else 0
        dna_win_rate = (dna_stats.get('win_count', 0) / dna_stats.get('total_trades', 1) * 100) if (dna_stats and dna_stats.get('total_trades', 0) > 0) else 0

        unrealized_pnl = 0.0
        ws_tickers = mws.get_tickers() # Pre-fetch untuk efisiensi
        
        # dr_val is already set correctly at the start of the function
        
        # [NEW] Predator Metrics Calculation
        total_used_idr = 0.0
        for pos in positions:
            if bool(pos['dry_run']) == dr_val:
                total_used_idr += float(pos['amount_idr'] or 0)

        realized_pnl_dry = float(dry_stats['total_pnl_idr'] or 0)
        realized_pnl_live = float(live_stats['total_pnl_idr'] or 0)

        if dr_val:
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
            if bool(pos['dry_run']) != dr_val:
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
                    row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
                    if row and row['history_json']:
                        history = json.loads(row['history_json'])
                        if history: last_price = float(history[-1])
                except: pass

            if last_price > 0:
                gross = (last_price - ep) * float(pos['amount_crypto'] or 0)
                fee_buy = float(pos['amount_idr'] or 0) * cfg.FEE_BUY_TAKER
                fee_sell = float(pos['amount_crypto'] or 0) * last_price * cfg.FEE_SELL_TAKER
                unrealized_pnl += gross - fee_buy - fee_sell

        total_equity = total_equity_val
        utilization_pct = (total_used_idr / (available_idr + total_used_idr) * 100) if (available_idr + total_used_idr) > 0 else 0
        # Injeksi Harga Realtime (Prioritas: Trade Stream > Summary > DB)
        ws_tickers = mws.get_tickers()
        ws_trades = mws.get_trades(ANALYSIS_PAIR)
        tech_data = _get_pair_technicals(ANALYSIS_PAIR)
        
        # 1. Ambil dari Trade Stream (Paling Cepat / Sub-detik)
        ws_price = 0
        if ws_trades:
            try: ws_price = float(ws_trades[0][4]) # Index 4 is price
            except: pass
            
        # 2. Fallback ke Summary Stream
        if ws_price <= 0:
            ws_price = float(ws_tickers.get(ANALYSIS_PAIR, {}).get('last', 0))
            
        if ws_price > 0:
            tech_data['last_price'] = ws_price

        ai_reasoning, updated_at = db.get_state_with_time(f"llm_reasoning_{ANALYSIS_PAIR}")
        
        # Validasi Kesegaran Analisis (Jika > 3 menit, anggap stale dan gunakan Technical Fallback)
        is_stale = True
        if updated_at:
            try:
                # Handle both string (SQLite legacy) and datetime object (PostgreSQL)
                if isinstance(updated_at, datetime.datetime):
                    dt = updated_at
                else:
                    dt = datetime.datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                
                # Normalise to naive for comparison
                if dt.tzinfo: dt = dt.replace(tzinfo=None)
                
                if (datetime.datetime.now() - dt).total_seconds() < 180:
                    is_stale = False
            except: pass
                
        _last_price = tech_data.get('last_price')
        combined_reasoning = ai_reasoning if (ai_reasoning and not is_stale) else _generate_technical_reasoning(ANALYSIS_PAIR, tech_data, _last_price)

        history_since = conn.execute(
            "SELECT TO_CHAR(MIN(updated_at), 'DD/MM HH24:MI') FROM ticker_history"
        ).fetchone()[0] or '--/-- --:--'

        import config as cfg

        # PnL & Win Rate Selection (Live vs Dry Run) — strict by mode.
        if dr_val:
            active_pnl = float(dry_stats['total_pnl_idr'] or 0) if dry_stats else 0
            active_win_rate = dry_win_rate
        else:
            active_pnl = float(live_stats['total_pnl_idr'] or 0) if live_stats else 0
            active_win_rate = win_rate

        # Net PnL for dashboard headline should include open-position unrealized PnL.
        net_total_pnl = float(active_pnl) + float(unrealized_pnl)

        # Cari Top Pair, Best Trade & Asset Contributions (Overall)
        top_pair_row = conn.execute(
            "SELECT pair, SUM(pnl_idr) as total_pnl FROM trade_history "
            "WHERE reason != 'BACKTEST' AND dry_run = %s "
            "GROUP BY pair ORDER BY total_pnl DESC LIMIT 1", (dr_val,)
        ).fetchone()
        
        contribution_rows = conn.execute(
            "SELECT pair, SUM(pnl_idr) as total_pnl, COUNT(*) as tx_count FROM trade_history "
            "WHERE reason != 'BACKTEST' AND dry_run = %s "
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
            "WHERE reason != 'BACKTEST' AND dry_run = %s", (bool(dr_val),)
        ).fetchone()
        
        top_pair = top_pair_row['pair'] if top_pair_row and top_pair_row['total_pnl'] > 0 else None
        best_trade = float(best_trade_row[0] or 0)
        regime = stats_dict.get('market_regime', 'CHOPPY')

        # Overall engagements (Closed + Open)
        total_trades_overall = dry_stats['total_trades'] if dr_val else live_stats['total_trades']
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
                dt_raw = first_trade_row[0]
                if isinstance(dt_raw, datetime.datetime):
                    dt = dt_raw
                else:
                    dt = datetime.datetime.strptime(dt_raw, "%Y-%m-%d %H:%M:%S")
                
                # Normalise to UTC for timestamp
                if not dt.tzinfo: dt = dt.replace(tzinfo=datetime.timezone.utc)
                first_trade_ts = int(dt.timestamp() * 1000)
            except: pass

        # --- LLM Executive Report Integration ---
        equity_reasoning_ai, updated_at = db.get_state_with_time("llm_equity_report")
        
        is_stale_report = True
        if updated_at:
            try:
                if isinstance(updated_at, datetime.datetime):
                    dt = updated_at
                else:
                    dt = datetime.datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                
                if dt.tzinfo: dt = dt.replace(tzinfo=None)
                if (datetime.datetime.now() - dt).total_seconds() < 600:
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
                WHERE reason != 'BACKTEST' AND dry_run = %s
                ORDER BY created_at DESC LIMIT 20
            """, (bool(dr_val),)).fetchall()
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
                    "created_at":  str(r['created_at'] or ''),
                })
        except Exception as e:
            print(f"⚠️ History Fetch Error: {e}")


        # ── BTC Condition (for Signal Radar UX) ─────────────────────────────
        # Realtime-first: price/24h from WS summary. If interval metrics are
        # empty or stale in DB, recompute from the latest BTC history snapshot.
        _btc_ticker = ws_tickers.get('btcidr', {}) or {}
        btc_price = float(_btc_ticker.get('last', 0))
        btc_change_24h = float(_btc_ticker.get('change_24h', 0))
        try:
            _btc_row = conn.execute(
                "SELECT ch_5m, ch_15m, ch_1h, trend, score, history_json, updated_at "
                "FROM ticker_history WHERE pair = 'btcidr'"
            ).fetchone()
        except Exception:
            _btc_row = None

        btc_history = []
        btc_history_updated_at = None
        if _btc_row:
            btc_history_updated_at = _btc_row['updated_at']
            try:
                raw_hist = _btc_row['history_json']
                if raw_hist:
                    parsed_hist = json.loads(raw_hist)
                    if isinstance(parsed_hist, list):
                        btc_history = [float(x) for x in parsed_hist if x is not None]
            except Exception:
                btc_history = []

        if btc_price <= 0 and btc_history:
            btc_price = float(btc_history[-1])

        btc_ch_5m  = float(_btc_row['ch_5m']  or 0) if _btc_row else 0.0
        btc_ch_15m = float(_btc_row['ch_15m'] or 0) if _btc_row else 0.0
        btc_ch_1h  = float(_btc_row['ch_1h']  or 0) if _btc_row else 0.0
        btc_trend  = str(_btc_row['trend'] or 'N/A') if _btc_row else 'N/A'
        btc_score  = float(_btc_row['score'] or 50) if _btc_row else 50.0

        btc_rt = _get_btc_realtime_metrics()
        if btc_rt['has_data']:
            btc_price = btc_rt['price'] if btc_rt['price'] > 0 else btc_price
            btc_change_24h = btc_rt['ch_24h']
            btc_ch_5m = btc_rt['ch_5m']
            btc_ch_15m = btc_rt['ch_15m']
            btc_ch_1h = btc_rt['ch_1h']
            btc_trend = btc_rt['trend']

        if btc_history:
            if abs(btc_ch_5m) < 1e-9:
                btc_ch_5m = _pct_change_from_history(btc_history, 5)
            if abs(btc_ch_15m) < 1e-9:
                btc_ch_15m = _pct_change_from_history(btc_history, 15)
            if abs(btc_ch_1h) < 1e-9:
                btc_ch_1h = _pct_change_from_history(btc_history, 60)

            if str(btc_trend).upper() in ('N/A', 'UNKNOWN', ''):
                btc_trend = _derive_trend_from_history(btc_history)
            elif str(btc_trend).upper() == 'SIDEWAYS' and abs(btc_ch_1h) >= 0.25:
                # If momentum is materially moving, avoid a stale SIDEWAYS label.
                btc_trend = _derive_trend_from_history(btc_history)

        # Optional diagnostics to inspect freshness in frontend/devtools.
        btc_data_source = 'ipc_tick_buffer' if btc_rt['has_data'] else ('ws_summary' if _btc_ticker else 'ticker_history')
        btc_updated_age_sec = btc_rt['age_sec']
        if btc_updated_age_sec is None:
            try:
                dt_upd = _parse_iso_date(btc_history_updated_at)
                if dt_upd is not None:
                    if dt_upd.tzinfo is None:
                        dt_upd = dt_upd.replace(tzinfo=datetime.timezone.utc)
                    btc_updated_age_sec = int((datetime.datetime.now(datetime.timezone.utc) - dt_upd).total_seconds())
            except Exception:
                btc_updated_age_sec = None

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
            "net_pnl": net_total_pnl,
            "all_time_pnl": active_pnl,
            "win_rate": round(active_win_rate, 2),
            "dry_win_rate": round(dry_win_rate, 2),
            "dry_trades_count": dry_stats['total_trades'] if dry_stats else 0,
            "total_trades_live": live_stats['total_trades'] if live_stats else 0,
            "live_pnl": float(live_stats['total_pnl_idr'] or 0) if live_stats else 0,
            "live_win_rate": round(win_rate, 2),
            "live_trades_count": live_stats['total_trades'] if live_stats else 0,
            "backtest_pnl": float(dna_stats.get('total_pnl_idr') or 0) if dna_stats else 0,
            "backtest_win_rate": round(dna_win_rate, 2),
            "backtest_trades_count": dna_stats.get('total_trades', 0) if dna_stats else 0,
            "active_positions": open_count,
            "unrealized_pnl": round(unrealized_pnl, 0),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', ''),
            "latest_llm_insight": stats_dict.get('latest_llm_insight', ''),
            "hero_ai_insight": stats_dict.get('hero_ai_insight', 'Waiting for AI analysis...'),
            "hero_ai_insights": [{"message": r[0], "created_at": str(r[1])} for r in conn.execute("SELECT message, created_at FROM system_events WHERE type='AI_INSIGHT' ORDER BY created_at DESC LIMIT 5").fetchall()] or [{"message": stats_dict.get('hero_ai_insight', 'Waiting for AI analysis...'), "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}],
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
            "manual_exit_max_net_pct": cfg.MANUAL_EXIT_MAX_NET_PCT,
            
            # --- Dynamic Session & Gate State (Source of Truth: bot_state) ---
            "session_mode": stats_dict.get('session_mode', 'SCALPING'),
            "session_buy_enabled": stats_dict.get('session_buy_enabled', 'true').lower() == 'true',
            "daily_target_hit": stats_dict.get('daily_target_hit', '0') == '1',
            "consecutive_losses": int(stats_dict.get('consecutive_losses', 0)),
            "entry_gate_state": stats_dict.get('entry_gate_state', ''),
            "entry_gate_pair": stats_dict.get('entry_gate_pair', ''),
            "entry_gate_reason": stats_dict.get('entry_gate_reason', ''),
            "entry_gate_at": stats_dict.get('entry_gate_at', ''),
            "dry_run": bool(dr_val),
            "dry_run_loose_gates": stats_dict.get('dry_run_loose_gates', 'false').lower() == 'true',
            "technical_only_mode": bool(cfg.TECHNICAL_ONLY_MODE),
            "momentum_state": db.get_state("momentum_state") or "STALLING",
            "net_pnl_velocity": float(db.get_state("net_pnl_velocity") or 0),
            
            # --- BTC Condition — used by Signal Radar UX ---
            "btc_price":      round(btc_price, 0),
            "btc_change_24h": round(btc_change_24h, 2),
            "btc_ch_5m":      round(btc_ch_5m, 2),
            "btc_ch_15m":     round(btc_ch_15m, 2),
            "btc_ch_1h":      round(btc_ch_1h, 2),
            "btc_trend":      btc_trend,
            "btc_score":      round(btc_score, 1),
            "btc_weakening":  btc_weakening_flag,
            "btc_crash":      btc_crash_flag,
            "btc_data_source": btc_data_source,
            "btc_updated_age_sec": btc_updated_age_sec,
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
                ts = _parse_iso_date(ts_str)
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
                    return _parse_iso_date(c)
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

                # Jika feed WS tidak memberi perubahan 24h (sering 0 di mode dashboard),
                # gunakan fallback dari indikator DB agar UI tidak selalu 0.00%.
                if abs(ch_24h) < 1e-9:
                    ch_24h = float(row['ch_1h'] or 0) if row else 0
                    if abs(ch_24h) < 1e-9:
                        ch_24h = change
                

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
                    "gate_updated_at": str(gate_updated_at or ''),
                    "ai_veto": ai_veto,
                    "ai_veto_reason": veto_reason,
                    "ai_veto_confidence": veto_conf,
                    "tradable": not is_blocked,
                    "tradable_reason": tradable_reason,
                    "updated_at": str(row["updated_at"] or '') if row else None
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
                        row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)).fetchone()
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
                exec_guard = _estimate_market_sell_net_from_orderbook(pair, amount_crypto, entry_price, _amount_idr)
                _fee_buy  = _amount_idr * cfg.FEE_BUY_TAKER
                _fee_sell = (last_price * amount_crypto) * cfg.FEE_SELL_TAKER
                pnl_net_idr = exec_guard['net_pnl_idr'] if exec_guard.get('ok') else (pnl_idr - _fee_buy - _fee_sell)
                pnl_net_pct = (pnl_net_idr / _amount_idr * 100) if _amount_idr > 0 else 0.0
                is_fast_opp = int(p.get('is_high_opportunity_fast') or 0)

                results.append({
                    "id":           f"pos_{pair}",
                    "pair":         pair,
                    "reason":       display_reason,
                    "pnl_idr":      round(pnl_idr, 0),
                    "pnl_net_idr":  round(pnl_net_idr, 0),
                    "pnl_pct":      round(pnl_pct, 2),
                    "pnl_net_pct":  round(pnl_net_pct, 2),
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
                    "is_manual":    0,
                    "is_fast_opportunity": bool(is_fast_opp)
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

def _build_trades_update_packet():
    """Bundle trades with active mode marker to avoid stale cross-mode UI updates."""
    return {
        "dry_run": bool(_is_dry_run()),
        "items": _build_trades_payload(),
    }

def _build_logs_payload():
    """Ambil log sinyal AI, oracle, dan system events dari DB."""
    conn = get_db_connection()
    try:
        # 1. Fetch System Events (New Source)
        events = conn.execute("""
            SELECT pair, type as side, message as price, level as status, created_at
            FROM system_events
            ORDER BY created_at DESC LIMIT 100
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

        # 4. Fetch latest market trades from websocket cache (watch pair)
        market_trades = _build_market_trades_payload()

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

        for t in market_trades[:20]:
            side_raw = str(t.get('side', '')).upper()
            side = side_raw if side_raw in ('BUY', 'SELL') else 'MARKET'
            pair = str(t.get('pair', CURRENT_WATCH_PAIR)).upper()
            price = float(t.get('price', 0) or 0)
            amount = float(t.get('amount', 0) or 0)
            raw_ts = float(t.get('time', 0) or 0)
            # Support both epoch-seconds and epoch-milliseconds from trade cache.
            ts_ms = int(raw_ts * 1000) if raw_ts > 0 and raw_ts < 1_000_000_000_000 else int(raw_ts)
            if ts_ms <= 0:
                ts_ms = int(time.time() * 1000)
            created_at = datetime.datetime.fromtimestamp(ts_ms / 1000.0).isoformat()
            results.append({
                "created_at": created_at,
                "pair": pair,
                "side": side,
                "price": f"Market {side} | {amount:.6f} @ Rp {int(price):,}".replace(',', '.'),
                "status": "LIVE",
                "level": "INFO",
                "source": "MARKET_WS",
                "trade_id": t.get('id')
            })

        # Sort combined results by created_at descending
        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return results[:30]
    finally:
        conn.close()

def _build_llm_decisions_payload(limit: int = 20):
    """Kembalikan riwayat keputusan LiteRT-LM dari system_events."""
    conn = get_db_connection()
    try:
        # MARKET INTELLIGENCE BRIEF: semua variabel dari live WebSocket/IPC, bukan DB.
        regime_raw = db.get_state("current_market_regime")
        sentiment_raw = db.get_state("market_sentiment_index")

        regime = str(regime_raw).upper() if regime_raw else "SCANNING"
        sentiment = int(float(sentiment_raw)) if sentiment_raw not in (None, "") else None

        # Ambil snapshot live tickers dari IPC/WS bridge
        live_tickers = mws.get_tickers()

        _btc_live = live_tickers.get('btcidr', {})
        btc_price = float(_btc_live.get('last') or 0) or None

        _active_pairs = [
            (k, v) for k, v in live_tickers.items()
            if float(v.get('vol_idr') or 0) > 0
        ]
        # Urutkan berdasarkan volume IDR (pair paling aktif).
        _active_pairs.sort(key=lambda x: float(x[1].get('vol_idr') or 0), reverse=True)

        # Hitung buy/sell bias dari posisi harga vs midpoint 24h range
        buy_count = 0
        sell_count = 0
        for _, v in _active_pairs:
            last = float(v.get('last') or 0)
            high = float(v.get('high') or 0)
            low = float(v.get('low') or 0)
            if high > 0 and low > 0 and high > low and last > 0:
                mid = (high + low) / 2.0
                if last > mid * 1.01:
                    buy_count += 1
                elif last < mid * 0.99:
                    sell_count += 1
        avg_score = None
        now_str = datetime.datetime.now().isoformat()
        last_ticker_update = now_str

        # Filter stablecoin agar tidak muncul di top mover stats
        _STABLECOIN_BASES = {'usdt', 'usdc', 'busd', 'bidr', 'dai', 'tusd'}
        _crypto_pairs = [
            (k, v) for k, v in _active_pairs
            if k.lower().replace('idr', '') not in _STABLECOIN_BASES
        ]

        # Hitung % change vs midpoint 24h range untuk setiap crypto pair
        def _midpoint_ch(ticker_val):
            last = float(ticker_val.get('last') or 0)
            high = float(ticker_val.get('high') or 0)
            low = float(ticker_val.get('low') or 0)
            if high > 0 and low > 0 and high > low and last > 0:
                mid = (high + low) / 2.0
                return round(((last - mid) / mid) * 100.0, 2)
            return float(ticker_val.get('change_24h') or 0)

        _scored_crypto = [
            (k.upper().replace('IDR', ''), _midpoint_ch(v))
            for k, v in _crypto_pairs
        ]

        # Top gainer: % change tertinggi (positif)
        _gainers = sorted(_scored_crypto, key=lambda x: x[1], reverse=True)
        top_gainer = _gainers[0] if _gainers else ("N/A", 0.0)

        # Top loser: % change terendah (negatif)
        _losers = sorted(_scored_crypto, key=lambda x: x[1])
        top_loser = _losers[0] if _losers else ("N/A", 0.0)

        # Top trending: % change absolut tertinggi (paling bergerak)
        _trending = sorted(_scored_crypto, key=lambda x: abs(x[1]), reverse=True)
        top_trending = _trending[0] if _trending else ("N/A", 0.0)

        # Fallback top_pair_key untuk market_vars backward compat
        top_pair_key = top_trending[0]
        top_ch_1h = top_trending[1]

        detail_tickers = []
        for k, v in _active_pairs[:12]:
            last = float(v.get('last') or 0)
            high = float(v.get('high') or 0)
            low = float(v.get('low') or 0)
            if high > 0 and low > 0 and high > low and last > 0:
                mid = (high + low) / 2.0
                ch = round(((last - mid) / mid) * 100.0, 2)
            else:
                ch = 0.0
            detail_tickers.append({
                "pair": k.upper(),
                "signal": "BUY" if ch > 1 else ("SELL" if ch < -1 else "HOLD"),
                "trend": "UP" if ch > 1.0 else ("DOWN" if ch < -1.0 else "SIDEWAYS"),
                "score": 50,
                "prediction_prob": 0.5,
                "ch_5m": 0,
                "ch_15m": 0,
                "ch_1h": ch,
                "vol_idr": float(v.get('vol_idr') or 0),
                "high_24h": high,
                "low_24h": low,
                "updated_at": now_str,
            })

        market_vars = {
            "generated_at": now_str,
            "regime": regime,
            "sentiment_pct": sentiment,
            "btc_idr": btc_price,
            "signal_count": {
                "buy": buy_count,
                "sell": sell_count,
            },
            "avg_ai_score": avg_score,
            "top_trending": {"pair": top_trending[0], "ch": top_trending[1]},
            "top_gainer": {"pair": top_gainer[0], "ch": top_gainer[1]},
            "top_loser": {"pair": top_loser[0], "ch": top_loser[1]},
            "live_pairs_count": len(_active_pairs),
            "tickers": detail_tickers,
        }

        _request_market_brief_llm(market_vars)

        worker = LLMWorker.get_instance()
        llm_brief = worker.get_latest_result_by_type("market_intel_brief") if worker.is_active else None

        parsed_brief = _extract_json_brief_text(str(llm_brief or ""))

        if parsed_brief:
            brief_message = parsed_brief
            brief_created_at = now_str
        else:
            # Synthesize brief dari live market data saat LLM belum merespons.
            # Tidak menggunakan persisted_brief dari DB agar konten selalu fresh dan valid.
            btc_part = f" | BTC/IDR {btc_price:,.0f}" if btc_price and btc_price > 0 else ""
            regime_label = "Warming up" if regime == "SCANNING" else regime
            gainer_part = f"↑ {top_gainer[0]} {top_gainer[1]:+.2f}%" if top_gainer[0] != "N/A" else ""
            loser_part = f"↓ {top_loser[0]} {top_loser[1]:+.2f}%" if top_loser[0] != "N/A" else ""
            trend_part = f"🔥 {top_trending[0]} {top_trending[1]:+.2f}%" if top_trending[0] != "N/A" else ""
            movers = " | ".join(p for p in [gainer_part, loser_part, trend_part] if p)
            brief_message = (
                f"Market live: {len(_active_pairs)} pair aktif"
                f"{btc_part}"
                f" | Regime {regime_label} | Sentimen {sentiment}%"
                + (f" | {movers}" if movers else "")
                + f" | Bullish {buy_count} vs Bearish {sell_count} pair"
            )
            brief_created_at = now_str

        rows = conn.execute(
            """
            SELECT DISTINCT ON (message) id, pair, message, type as side, level, created_at
            FROM system_events
            WHERE type IN ('LLM_EXIT_SIGNAL', 'LLM_ENTRY_SIGNAL', 'LLM_EXIT_HOLD', 'LLM_EXIT_TIMEOUT', 'AI_INSIGHT', 'AI_ORACLE')
            ORDER BY message, created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        payload = [
            {
                "id": 0,
                "pair": "MARKET",
                "side": "MARKET_INTEL_BRIEF",
                "message": brief_message,
                "level": "INFO",
                "created_at": brief_created_at,
            }
        ]

        payload.extend([
            {
                "id": int(r["id"] or 0),
                "pair": (r["pair"] or "").upper(),
                "side": r["side"] or "LLM_EXIT_SIGNAL",
                "message": r["message"] or "",
                "level": r["level"] or "WARNING",
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ])

        return payload
    except Exception as exc:
        logging.warning(f"[llm-decisions] payload error: {exc}")
        return []
    finally:
        conn.close()


def _build_market_trades_payload(limit_total: int = 120, limit_per_pair: int = 12):
    """Ambil data trades terbaru multi-coin dari cache mws."""
    try:
        with mws._lock:
            snapshot = {
                str(pair).lower(): list(rows[:limit_per_pair])
                for pair, rows in (mws._trades or {}).items()
                if rows
            }

        results = []
        for pair, trades in snapshot.items():
            for t in trades:
                try:
                    # Indodax format: [pair, timestamp, sequence, side, price, vol_idr, vol_coin]
                    raw_ts = float(t[1])
                    ts_ms = int(raw_ts * 1000) if raw_ts < 1_000_000_000_000 else int(raw_ts)
                    results.append({
                        "pair": pair,
                        "time": ts_ms,
                        "price": float(t[4]),
                        "amount": float(t[6]),
                        "side": str(t[3]).lower(),
                        "id": t[2],
                    })
                except (IndexError, ValueError, TypeError):
                    continue

        results.sort(key=lambda x: float(x.get("time") or 0), reverse=True)
        return results[:limit_total]
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
    _last_strategic_break_ts = None  # Track last emitted strategic_break triggered_at to emit only once
    _last_bracket_exit_id = None  # Track last forwarded bracket_exit_confirm exit_id to avoid spam
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
                        ts = sb.get("triggered_at", "")
                        if ts != _last_strategic_break_ts:
                            socketio.emit('strategic_break', sb)
                            _last_strategic_break_ts = ts
            except Exception as _sbe:
                logging.warning(f"[StrategicBreak] Error forwarding payload: {_sbe}")

            # ── Bracket Exit Confirm: forward negative-PnL exit popup to frontend ──
            try:
                raw_bec = db.get_state("bracket_exit_confirm")
                if raw_bec:
                    bec = json.loads(raw_bec)
                    if (
                        bec.get("status") == "PENDING"
                        and bec.get("exit_id") != _last_bracket_exit_id
                    ):
                        _last_bracket_exit_id = bec.get("exit_id")
                        socketio.emit('bracket_exit_confirm', bec, room='authenticated')
                elif _last_bracket_exit_id is not None:
                    # Resolved — tell frontend to dismiss
                    socketio.emit('bracket_exit_confirm_cleared', {}, room='authenticated')
                    _last_bracket_exit_id = None
            except Exception as _bece:
                logging.warning(f"[BracketExit] Error forwarding bracket_exit_confirm: {_bece}")

            # Fast: regime update setiap 1s (low-latency)
            try:
                _regime_now = db.get_state("market_regime") or "CHOPPY"
                _sentiment_now = int(db.get_state("market_sentiment_index") or 50)
                _regime_leader_now = db.get_state("regime_leader") or ""
                socketio.emit('regime_update', {
                    "regime": _regime_now,
                    "market_sentiment_index": _sentiment_now,
                    "regime_leader": _regime_leader_now,
                }, room='public')
                # Also emit to authenticated room for dashboard users
                socketio.emit('regime_update', {
                    "regime": _regime_now,
                    "market_sentiment_index": _sentiment_now,
                    "regime_leader": _regime_leader_now,
                }, room='authenticated')
            except Exception as _re:
                logging.warning(f"[push_data_loop] regime_update error: {_re}")

            # Slow: setiap 5 iterasi (~5 detik)
            _slow_counter += 1
            if _slow_counter >= 5:
                _slow_counter = 0
                # individual emitters wrapped in try-except to prevent one failure from killing the whole loop
                # Emit stats for both modes so users on either dashboard see updates
                try: 
                    logging.info("📡 Emitting stats_update for DRY and LIVE modes...")
                    socketio.emit('stats_update', _build_stats_payload(force_dry=True))
                    socketio.emit('stats_update', _build_stats_payload(force_dry=False))
                except Exception as _e: 
                    logging.error(f"stats_update emit error: {_e}")

                try: 
                    _radar_payload = _build_radar_payload()
                    socketio.emit('radar_update', _radar_payload, room='public')
                    socketio.emit('radar_update', _radar_payload, room='authenticated')
                except Exception as _e: logging.error(f"radar_update emit error: {_e}")

                try: socketio.emit('trades_update',         _build_trades_update_packet())
                except Exception as _e: logging.error(f"trades_update emit error: {_e}")

                try: 
                    _logs_payload = _build_logs_payload()
                    socketio.emit('logs_update', _logs_payload, room='authenticated')
                    # Emit a sanitized version to public
                    socketio.emit('public_activity', _logs_payload[:10], room='public')
                except Exception as _e: logging.error(f"logs_update emit error: {_e}")

                try: 
                    _llm_payload = _build_llm_decisions_payload()
                    socketio.emit('llm_decisions_update', _llm_payload, room='public')
                    socketio.emit('llm_decisions_update', _llm_payload, room='authenticated')
                except Exception as _e: logging.error(f"llm_decisions_update emit error: {_e}")
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


@socketio.on('bracket_exit_response')
def handle_bracket_exit_response(data):
    """Receive user CONFIRM / ABORT decision for a negative-PnL bracket exit."""
    exit_id  = data.get('exit_id', '')
    decision = data.get('decision', '')  # 'CONFIRM' or 'ABORT'

    if not exit_id or decision not in ('CONFIRM', 'ABORT'):
        logging.warning(f"[BracketExit] Invalid bracket_exit_response payload: {data}")
        return

    try:
        raw = db.get_state("bracket_exit_confirm")
        if not raw:
            logging.debug(f"[BracketExit] No pending confirmation found for exit_id={exit_id}")
            return
        pending = json.loads(raw)
        if pending.get("exit_id") != exit_id:
            logging.warning(f"[BracketExit] exit_id mismatch: got {exit_id}, expected {pending.get('exit_id')}")
            return
        if pending.get("status") != "PENDING":
            return  # Already resolved
        pending["status"] = decision
        db.save_state("bracket_exit_confirm", json.dumps(pending))
        logging.info(f"[BracketExit] {decision} received for exit {exit_id}")
    except Exception as e:
        logging.error(f"[BracketExit] Error processing bracket_exit_response: {e}")


@app.route('/api/session/status', methods=['GET'])
@login_required
def session_status():
    """
    Returns the current session mode and strategic break payload so the frontend
    can recover its state on page refresh.
    """
    try:
        session_mode = db.get_state("session_mode") or "NORMAL"
        # is_latched combines both the explicit latch flag (which survives a brief
        # session_mode write race) and the authoritative HARVEST_ONLY mode value.
        session_latched = (db.get_state("session_latched") or "false").lower() == "true"
        is_latched = session_latched or (session_mode == "HARVEST_ONLY")

        strategic_break_data = None
        raw_sb = db.get_state("strategic_break_payload")
        if raw_sb:
            try:
                sb = json.loads(raw_sb)
                if sb.get("status") == "PENDING":
                    strategic_break_data = sb
            except Exception:
                pass

        return jsonify({
            "session_status": session_mode,
            "is_latched": is_latched,
            "strategic_break": strategic_break_data,
        })
    except Exception as e:
        logging.error(f"[SessionStatus] Error: {e}")
        return jsonify({"error": "Internal server error."}), 500


@app.route('/api/session/action', methods=['POST'])
@login_required
def session_action():
    """
    Strategic Break user action endpoint.

    Accepts JSON: { "action": "CONTINUE" | "STOP" | "RESET" }

    CONTINUE — Recalculates next target as (equity × target_pct), clears the
               HARVEST_ONLY latch, and resets session_mode to NORMAL.
    STOP     — Keeps HARVEST_ONLY latch active for the rest of the day.
    RESET    — Clears the HARVEST_ONLY latch and returns to NORMAL without
               recalculating the target (keeps the current target in place).
    """
    data = request.get_json(silent=True) or {}
    action = data.get('action', '').upper()

    if action not in ('CONTINUE', 'STOP', 'RESET'):
        return jsonify({"error": "Invalid action. Use 'CONTINUE', 'STOP', or 'RESET'."}), 400

    try:
        raw_sb = db.get_state("strategic_break_payload")
        if not raw_sb:
            # Allow RESET even when there is no active strategic break payload
            # (e.g. if the bot was restarted and the payload was cleared).
            if action == 'RESET':
                db.save_state("session_mode", "NORMAL")
                db.save_state("session_buy_enabled", "true")
                db.save_state("session_latched", "false")
                db.save_state("strategic_break_payload", "")
                socketio.emit('strategic_break_cleared', {"action": "RESET"})
                logging.info("[StrategicBreak] RESET — latch cleared, session returned to NORMAL.")
                return jsonify({"status": "ok", "action": "RESET"})
            return jsonify({"error": "No active strategic break."}), 404
        payload = json.loads(raw_sb)

        if action == 'CONTINUE':
            # Recalculate next target: equity × same profit percentage.
            # Use the persisted daily_profit_target as the profit-pct reference.
            try:
                balance_str = db.get_state("cached_idr_balance")
                equity = float(balance_str) if balance_str else float(_IC)
            except Exception:
                equity = float(_IC)

            # Derive target_pct from the original target vs equity ratio (fallback 1%)
            try:
                orig_target = float(db.get_state("daily_profit_target") or 0)
                target_pct = (orig_target / equity) if equity > 0 else 0.01
            except Exception as _tpe:
                logging.warning(f"[StrategicBreak] Could not derive target_pct: {_tpe}. Using 1% fallback.")
                target_pct = 0.01

            # New profit target = equity × target_pct
            new_target = equity * target_pct
            db.save_state("daily_profit_target", str(new_target))
            db.save_state("daily_profit_target_ts", datetime.datetime.now(datetime.timezone.utc).isoformat())
            db.save_state("session_mode", "NORMAL")
            db.save_state("session_buy_enabled", "true")
            db.save_state("session_latched", "false")   # release latch

            payload["status"] = "CONTINUE"
            db.save_state("strategic_break_payload", json.dumps(payload))

            # Notify frontend to dismiss the popup
            socketio.emit('strategic_break_cleared', {"action": "CONTINUE", "new_target": new_target})
            logging.info(f"[StrategicBreak] CONTINUE — latch cleared, new target: Rp {new_target:,.0f}")
            return jsonify({"status": "ok", "action": "CONTINUE", "new_target": new_target})

        elif action == 'RESET':
            # Release the latch and return to NORMAL without changing the target.
            db.save_state("session_mode", "NORMAL")
            db.save_state("session_buy_enabled", "true")
            db.save_state("session_latched", "false")   # release latch

            payload["status"] = "RESET"
            db.save_state("strategic_break_payload", json.dumps(payload))

            socketio.emit('strategic_break_cleared', {"action": "RESET"})
            logging.info("[StrategicBreak] RESET — latch cleared, session returned to NORMAL.")
            return jsonify({"status": "ok", "action": "RESET"})

        else:  # STOP
            # Keep HARVEST_ONLY latch — do not change session_mode or latched flag.
            payload["status"] = "STOP"
            db.save_state("strategic_break_payload", json.dumps(payload))
            socketio.emit('strategic_break_cleared', {"action": "STOP"})
            logging.debug("[StrategicBreak] STOP — session remains HARVEST_ONLY (latched).")
            return jsonify({"status": "ok", "action": "STOP"})

    except Exception as e:
        logging.error(f"[StrategicBreak] session_action error: {e}")
        return jsonify({"error": "Internal server error processing session action."}), 500


@app.route('/api/resilience/status', methods=['GET'])
@login_required
def get_resilience_status():
    """
    Returns the current resilience / circuit-breaker state so the frontend
    and Telegram bot can surface SYSTEM_PAUSE alerts to the user.

    Response keys
    ─────────────
    circuit_breaker_status : "CLOSED" | "OPEN" | "HALF_OPEN" (from bot_state DB)
    circuit_breaker_tripped_at : ISO timestamp or empty string
    system_pause_active    : bool — True when session_mode is "SYSTEM_PAUSE"
    session_latched        : bool
    """
    try:
        cb_status    = db.get_state("circuit_breaker_status") or "CLOSED"
        cb_tripped   = db.get_state("circuit_breaker_tripped_at") or ""
        session_mode = db.get_state("session_mode") or "NORMAL"
        latched_raw  = db.get_state("session_latched") or "false"
        return jsonify({
            "circuit_breaker_status":     cb_status,
            "circuit_breaker_tripped_at": cb_tripped,
            "system_pause_active":        session_mode == "SYSTEM_PAUSE",
            "session_latched":            latched_raw.lower() == "true",
            "session_mode":               session_mode,
        })
    except Exception as e:
        logging.error(f"[ResilienceStatus] Error: {e}")
        return jsonify({"error": "Internal server error."}), 500


# Start background thread
socketio.start_background_task(push_data_loop)

# ── ZMQ Equity Bridge — forward executive-core equity updates to SocketIO ────
def _zmq_equity_bridge():
    """Background task: subscribe to equity_stream.ipc and emit equity_update events.

    Uses zmq.green (eventlet-compatible) so recv() cooperatively yields to the
    eventlet hub instead of blocking the main thread.
    """
    ctx = zmq_eventlet.Context.instance()
    sub = ctx.socket(zmq_eventlet.SUB)
    # Subscribe to all messages — equity stream has no topic framing
    sub.setsockopt(zmq_eventlet.SUBSCRIBE, b"")
    sub.connect(IPC_EQUITY)
    logging.info(f"[ZMQ] Equity bridge connected to {IPC_EQUITY}")
    _err_delay = 0.1  # exponential backoff starting at 100ms, capped at 30s
    try:
        while True:
            try:
                raw = sub.recv()
                _err_delay = 0.1  # reset backoff on success
                equity = EquityUpdate()
                equity.ParseFromString(raw)
                socketio.emit('equity_update', {
                    'net_pnl':          equity.net_pnl,
                    'realized_pnl':     equity.total_realized,
                    'unrealized_pnl':   equity.total_unrealized,
                    'velocity':         equity.velocity,
                    'peak_equity':      equity.peak_equity,
                    'timestamp':        equity.timestamp,
                }, namespace='/')
            except Exception as _eq_err:
                logging.warning(f"[ZMQ] Equity bridge error: {_eq_err}")
                eventlet.sleep(_err_delay)
                _err_delay = min(_err_delay * 2, 30)
    finally:
        sub.close(linger=0)

socketio.start_background_task(_zmq_equity_bridge)

# ── ZMQ Market Bridge — forward market-pipe ticks to dashboard ────────────────
def _zmq_market_bridge():
    ctx = zmq_eventlet.Context.instance()
    sub = ctx.socket(zmq_eventlet.SUB)
    sub.setsockopt(zmq_eventlet.SUBSCRIBE, b"")
    sub.connect(IPC_MARKET)
    logging.info(f"[ZMQ] Market bridge connected to {IPC_MARKET}")
    _err_delay = 0.1
    try:
        while True:
            try:
                # Receive multipart [topic, data]
                parts = sub.recv_multipart()
                if len(parts) < 2: continue
                
                topic = parts[0].decode()
                raw = parts[1]
                _err_delay = 0.1
                
                if topic == "tick":
                    # logging.debug(f"[ZMQ] Received tick")
                    tick = Tick()
                    tick.ParseFromString(raw)
                    pair = str(tick.symbol or '').lower()
                    with mws._lock:
                        if pair not in mws._tickers:
                            mws._tickers[pair] = {
                                "last": tick.price, "buy": tick.bid, "sell": tick.ask,
                                "high": tick.high_24h, "low": tick.low_24h, "open": 0,
                                "vol_idr": tick.vol_idr, "vol_coin": "0",
                                "bid_depth": 0, "ask_depth": 0,
                                "bids_list": [], "asks_list": []
                            }
                        else:
                            t = mws._tickers[pair]
                            t["last"] = tick.price
                            t["buy"] = tick.bid
                            t["sell"] = tick.ask
                            if tick.high_24h > 0: t["high"] = tick.high_24h
                            if tick.low_24h > 0: t["low"] = tick.low_24h
                            if tick.vol_idr > 0: t["vol_idr"] = tick.vol_idr

                    if pair == 'btcidr':
                        _append_btc_tick(tick.timestamp, tick.price)
                
                elif topic == "trade":
                    logging.info(f"[ZMQ] Received trade for {pair if 'pair' in locals() else 'unknown'}")
                    trade = Trade()
                    trade.ParseFromString(raw)
                    pair = trade.symbol.lower()
                    # Format for dashboard: [pair, timestamp, sequence, side, price, vol_idr, vol_coin]
                    t_fmt = [
                        pair,
                        trade.timestamp / 1000, # convert back to epoch sec
                        trade.tid,
                        trade.side,
                        trade.price,
                        trade.price * trade.amount,
                        trade.amount
                    ]
                    with mws._lock:
                        if pair not in mws._trades:
                            mws._trades[pair] = []
                        mws._trades[pair].insert(0, t_fmt)
                        mws._trades[pair] = mws._trades[pair][:50]

            except Exception as _m_err:
                logging.warning(f"[ZMQ] Market bridge error: {_m_err}")
                eventlet.sleep(_err_delay)
                _err_delay = min(_err_delay * 2, 30)
    finally:
        sub.close(linger=0)

socketio.start_background_task(_zmq_market_bridge)

# ── Inject SocketIO emit into the shared EmergencyNotifier ──────────────────
try:
    from app.infrastructure.resilience import emergency_notifier as _emergency_notifier
    _emergency_notifier.set_socketio_fn(
        lambda event, data: socketio.emit(event, data, room="public")
    )
    logging.info("✅ EmergencyNotifier SocketIO channel wired.")
except Exception as _wire_err:
    logging.warning(f"⚠️ Could not wire EmergencyNotifier SocketIO: {_wire_err}")

def _background_wallet_sync_loop():
    """
    Background thread yang berjalan di dashboard untuk menjaga sinkronisasi wallet Indodax
    dengan database secara otomatis setiap 5 menit (hanya di mode LIVE).
    """
    import config as _cfg
    from config import TP_PCT, SL_PCT
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
                        "SELECT * FROM positions WHERE dry_run = FALSE"
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
