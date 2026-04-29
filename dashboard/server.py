import eventlet
eventlet.monkey_patch()
import os
import sys
import time
import datetime
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import sqlite3
import json
from flask import Flask, jsonify, send_from_directory, request, session, redirect, url_for, abort, make_response, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_wtf.csrf import CSRFProtect, generate_csrf
from functools import wraps
import secrets
from engines.database import db
from engines.ws_market import MarketDataWS
from engines.technicals import get_technical_summary, derive_trade_signal
import ccxt

# Exchange instance untuk public data (OHLCV)
exchange = ccxt.indodax()

app = Flask(__name__, static_folder='static')

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
csrf = CSRFProtect(app)
# Exclude SocketIO from CSRF as it has its own mechanism
# but keep it for normal API routes
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=10, ping_interval=5)

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
    # Cek token handshake dari auth payload
    client_token = auth.get('token') if auth else None
    valid_token = generate_view_token()
    
    is_authenticated = 'user_id' in session
    is_token_valid = (client_token == valid_token)

    if not is_authenticated and not is_token_valid:
        logging.warning("🚫 Unauthorized WS connection (No Session, No Valid Token)")
        return False # Tolak koneksi
    
    # Semua orang yang lolos masuk ke room public
    join_room('public')
    
    if is_authenticated:
        user_id = session.get('user_id')
        username = session.get('username')
        join_room(f"user_{user_id}")
        logging.info(f"🔌 Authenticated User {username} connected (Public + Private)")
    else:
        logging.info("🔌 Guest User connected via Valid Token (Public Only)")

@socketio.on('disconnect')
def handle_disconnect():
    username = session.get('username', 'Unknown')
    logging.info(f"📡 User {username} disconnected from WS")

# ── Background Throttler for UI Performance ──
def ui_emitter_thread():
    """Mengirim update ke UI secara berkala (Throttling) agar HP user tidak panas."""
    while True:
        socketio.sleep(0.5) # Emit setiap 500ms
        # Emit data publik ke semua
        # (Data radar dan ohlcv biasanya di-handle oleh event spesifik, 
        # tapi di sini kita bisa batch update harga jika perlu)
        pass

# Start the background thread
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

from config import DB_PATH, DRY_RUN

# Default pair — akan diperbarui saat user memilih koin di dashboard
CURRENT_WATCH_PAIR = "btcidr"

# Inisialisasi Market WS untuk dashboard agar data harga realtime (detik)
mws = MarketDataWS()
mws.start()
mws.subscribe_market_trades(CURRENT_WATCH_PAIR)

_start_time = time.time()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
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
    token = generate_view_token()
    return render_template('index.html', view_token=token)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/x-icon')


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
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins FROM trade_history WHERE reason = 'BACKTEST'"
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

        return jsonify({
            "all_time_pnl": float(live_stats['total_pnl'] or 0) if live_stats else 0,
            "all_time_pnl_dry": float(dry_stats['total_pnl'] or 0) if dry_stats else 0,
            "win_rate": round(win_rate, 2),
            "dry_win_rate": round(dry_win_rate, 2),
            "dry_trades_count": dry_stats['trades'] if dry_stats else 0,
            "total_trades_live": live_stats['trades'] if live_stats else 0,
            "active_positions": len(positions),
            "unrealized_pnl": round(unrealized_pnl, 0),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', ''),
            "latest_llm_insight": stats_dict.get('latest_llm_insight', ''),
            "active_reasoning": combined_reasoning,
            "technicals": tech_data,
            "sectors": sectors,
            "weights": db.get_all_weights(),
            "market_sentiment_index": int(stats_dict.get('market_sentiment_index', 50)),
            "backfill_status": stats_dict.get('backfill_status', 'N/A'),
            "history_since": conn.execute("SELECT strftime('%d/%m %H:%M', MIN(updated_at)) FROM ticker_history").fetchone()[0] or '--/-- --:--',
            "new_listings": db.get_new_listings()
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

@app.route('/api/analyze/<pair>')
def trigger_analysis(pair):
    """Trigger LLM analysis for a specific pair on-demand."""
    # Gunakan key yang sama dengan yang dimonitor oracle.py
    db.save_state("trigger_analysis_pair", pair.lower())
    return jsonify({"status": "requested", "pair": pair})

@app.route('/api/exit/<pair>', methods=['POST'])
@login_required
def manual_exit(pair):
    """Force an emergency exit for a specific pair via dashboard."""
    db.save_state("trigger_exit_pair", pair.lower())
    return jsonify({"status": "exit_requested", "pair": pair})

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

        return jsonify({
            "is_running": bot_alive,
            "dry_run": bool(DRY_RUN),
            "uptime_sec": uptime_sec,
            "last_heartbeat": last_heartbeat,
            "last_trade_at": last_trade_at
        })
    except Exception as e:
        return jsonify({"is_running": False, "dry_run": bool(DRY_RUN), "uptime_sec": 0})
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

@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory(app.static_folder, path)

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
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins FROM trade_history WHERE reason = 'BACKTEST'"
        ).fetchone()

        win_rate = (live_stats['wins'] / live_stats['trades'] * 100) if (live_stats and live_stats['trades'] > 0) else 0
        dry_win_rate = (dry_stats['wins'] / dry_stats['trades'] * 100) if (dry_stats and dry_stats['trades'] > 0) else 0
        dna_win_rate = (dna_stats['wins'] / dna_stats['trades'] * 100) if (dna_stats and dna_stats['trades'] > 0) else 0

        unrealized_pnl = 0.0
        ws_tickers = mws.get_tickers() # Pre-fetch untuk efisiensi
        
        from config import DRY_RUN
        dr_val = 1 if DRY_RUN else 0
        
        for pos in positions:
            # [Senior Audit] Filter unrealized PnL by current mode
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
                dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - dt).total_seconds() < 180:
                    is_stale = False
            except: pass
                
        _last_price = tech_data.get('last_price')
        combined_reasoning = ai_reasoning if (ai_reasoning and not is_stale) else _generate_technical_reasoning(CURRENT_WATCH_PAIR, tech_data, _last_price)

        history_since = conn.execute(
            "SELECT strftime('%d/%m %H:%M', MIN(updated_at)) FROM ticker_history"
        ).fetchone()[0] or '--/-- --:--'

        from config import DRY_RUN
        
        # PnL & Win Rate Selection (Live vs Dry Run)
        if DRY_RUN:
            active_pnl = float(dry_stats['total_pnl'] or 0) if dry_stats else 0
            active_win_rate = dry_win_rate
        else:
            active_pnl = float(live_stats['total_pnl'] or 0) if live_stats else 0
            active_win_rate = win_rate

        return {
            "daily_trades": perf['total_trades'],
            "daily_pnl": active_pnl,
            "win_rate": round(active_win_rate, 2),
            "dry_win_rate": round(dry_win_rate, 2),
            "dry_trades_count": dry_stats['trades'] if dry_stats else 0,
            "total_trades_live": live_stats['trades'] if live_stats else 0,
            "active_positions": len(positions),
            "unrealized_pnl": round(unrealized_pnl, 0),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', ''),
            "latest_llm_insight": stats_dict.get('latest_llm_insight', ''),
            "active_reasoning": combined_reasoning,
            "technicals": tech_data,
            "sectors": db.get_all_latest_sector_sentiment(),
            "weights": db.get_all_weights(),
            "market_sentiment_index": int(stats_dict.get('market_sentiment_index', 50)),
            "backfill_status": stats_dict.get('backfill_status', 'N/A'),
            "history_since": history_since,
            "new_listings": db.get_new_listings(),
            # Bot liveness — digunakan untuk memperbarui badge "CONNECTING" di UI
            "bot_running": True
        }
    finally:
        conn.close()

def _build_radar_payload():
    """Bangun payload radar dengan injeksi harga realtime dari WS."""
    conn = get_db_connection()
    # Ambil snapshot harga terbaru dari WS Buffer (Realtime detik)
    ws_tickers = mws.get_tickers()
    
    try:
        rows = conn.execute(
            "SELECT pair, trend, prediction_prob, ch_5m, ch_15m, ch_1h, volatility, score, signal, history_json, updated_at FROM ticker_history"
        ).fetchall()
        results = []
        for row in rows:
            try:
                pair = row['pair']
                history = json.loads(row['history_json'] or '[]')
                
                # Injeksi harga dari WS jika ada, jika tidak pakai dari DB
                ws_data = ws_tickers.get(pair, {})
                last = float(ws_data.get('last', 0))
                ch_24h = float(ws_data.get('change_24h', 0))
                
                if last <= 0:
                    last = float(history[-1]) if history else 0
                
                # Gunakan titik sebelumnya agar delta harga radar benar-benar mencerminkan momentum terbaru.
                prev = float(history[-2]) if len(history) > 1 else last
                change = ((last - prev) / prev * 100) if prev > 0 else 0
                

                results.append({
                    "pair":       pair,
                    "price":      last,
                    "change":     round(change, 2),
                    "change_24h": round(ch_24h, 2),
                    "vol_idr":    float(ws_data.get("vol_idr", 0)),
                    "ch_5m":      float(row['ch_5m'] or 0),
                    "ch_15m":     float(row['ch_15m'] or 0),
                    "ch_1h":      float(row['ch_1h'] or 0),
                    "volatility": float(row['volatility'] or 0),
                    "score":      float(row['score'] or 0),
                    "prediction": float(row['prediction_prob'] or 0.0),
                    "trend":      row['trend'] or 'N/A',
                    "signal":     row["signal"] or "HOLD",
                    "updated_at": row["updated_at"]
                })
            except Exception:
                continue
        return results
    finally:
        conn.close()

def _build_trades_payload():
    """[Senior Audit] Robust trade payload builder with per-item safety guards."""
    conn = get_db_connection()
    results = []
    try:
        from config import DRY_RUN
        dr_val = 1 if DRY_RUN else 0

        # 1. Fetch Positions (OPEN / PENDING_BUY / EXITING)
        positions = conn.execute("SELECT * FROM positions").fetchall()
        ws_tickers = mws.get_tickers()

        for p in positions:
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

                results.append({
                    "id":          f"pos_{pair}",
                    "pair":        pair,
                    "reason":      display_reason,
                    "pnl_idr":     round(pnl_idr, 0),
                    "pnl_pct":     round(pnl_pct, 2),
                    "entry_price": entry_price,
                    "exit_price":  last_price,
                    "amount_idr":  float(p['amount_idr'] or 0),
                    "amount_coin": amount_crypto,
                    "duration":    0,
                    "created_at":  str(p['timestamp'] or ''),
                    "status":      "OPEN",  # Semua posisi aktif masuk tab OPEN
                    "pos_status":  pos_status,  # Status detail untuk UI
                    "is_manual":   0
                })
            except Exception as e:
                print(f"Error processing position {p.get('pair')}: {e}")

        # 2. Fetch Trade History (CLOSED)
        # [Fix] Tampilkan MANUAL_SYNC (is_manual=1) selalu, tanpa filter dry_run
        # karena ini adalah transaksi nyata dari akun exchange.
        # Untuk trade reguler, filter sesuai mode DRY_RUN.
        trades = conn.execute("""
            SELECT * FROM trade_history 
            WHERE (dry_run = ? OR is_manual = 1) AND reason != 'BACKTEST'
            ORDER BY created_at DESC LIMIT 200
        """, (dr_val,)).fetchall()

        for t in trades:
            try:
                results.append({
                    "id":          int(t['id'] or 0),  # [Fix] Sertakan id DB untuk deduplication di mobile
                    "pair":        t['pair'] or 'UNKNOWN',
                    "reason":      t['reason'] or 'EXIT',
                    "pnl_idr":     float(t['pnl_idr'] or 0),
                    "pnl_pct":     float(t['pnl_pct'] or 0),
                    "entry_price": float(t['entry_price'] or 0),
                    "exit_price":  float(t['exit_price'] or 0),
                    "amount_idr":  float(t['amount_idr'] or 0),
                    "amount_coin": float(t['amount_crypto'] or 0),
                    "duration":    int(t['duration_seconds'] or 0),
                    "created_at":  str(t['created_at']),
                    "status":      'CLOSED',
                    "is_manual":   int(t['is_manual'] or 0)
                })
            except Exception as e:
                print(f"Error processing trade {t.get('id')}: {e}")

        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return results[:100]
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
        print(f"Build trades error: {e}")
        return []

def push_data_loop():
    """Background thread — push semua data via WebSocket setiap 1 detik."""
    while True:
        try:
            socketio.emit('stats_update',  _build_stats_payload())
            socketio.emit('radar_update',  _build_radar_payload())
            socketio.emit('trades_update', _build_trades_payload())
            socketio.emit('logs_update',   _build_logs_payload())

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
        except Exception as e:
            print(f"WS Error: {e}")
        socketio.sleep(1)

# Start background thread
socketio.start_background_task(push_data_loop)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8080, debug=False)
