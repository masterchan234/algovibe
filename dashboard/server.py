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
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO
from engines.database import db
from engines.ws_market import MarketDataWS
from engines.technicals import get_technical_summary, derive_trade_signal
import ccxt

# Exchange instance untuk public data (OHLCV)
exchange = ccxt.indodax()

app = Flask(__name__, static_folder='static')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

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

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/x-icon')


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
            "daily_trades": perf['total_trades'],
            "daily_pnl": float(live_stats['total_pnl'] or 0) if live_stats else 0,
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

@app.route('/api/trades/recent')
def get_recent_trades_api():
    conn = get_db_connection()
    try:
        # Fetch open positions
        positions = conn.execute(
            "SELECT pair, entry_price, amount_crypto, amount_idr, status, timestamp as created_at FROM positions"
        ).fetchall()

        # Fetch recent trades (EXCLUDE BACKTEST)
        trades = conn.execute(
            "SELECT pair, reason, pnl_idr, entry_price, amount_idr, created_at FROM trade_history WHERE reason != 'BACKTEST' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()

        results = []
        for p in positions:
            results.append({
                "pair": p['pair'] or "UNKNOWN",
                "reason": "OPEN POSITION",
                "pnl_idr": 0,
                "entry_price": float(p['entry_price'] or 0),
                "amount_idr": float(p['amount_idr'] or 0),
                "created_at": p['created_at'] or "1970-01-01T00:00:00",
                "status": "OPEN"
            })

        for t in trades:
            results.append({
                "pair": t['pair'] or "UNKNOWN",
                "reason": t['reason'] or "UNKNOWN",
                "pnl_idr": t['pnl_idr'] or 0,
                "entry_price": float(t.get('entry_price') or 0),
                "amount_idr": float(t.get('amount_idr') or 0),
                "created_at": t['created_at'] or "1970-01-01T00:00:00",
                "status": "CLOSED"
            })

        # Sort combined results by created_at descending
        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return jsonify(results[:20])
    except Exception as e:
        import traceback
        print("API ERROR:", traceback.format_exc())
        return jsonify([])
    finally:
        conn.close()

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
        for pos in positions:
            try:
                row = conn.execute("SELECT history_json FROM ticker_history WHERE pair = ?", (pos['pair'],)).fetchone()
                if row and row['history_json']:
                    history = json.loads(row['history_json'])
                    ep = float(pos['entry_price'] or 0)
                    if history and ep > 0:
                        unrealized_pnl += (float(history[-1]) - ep) * float(pos['amount_crypto'] or 0)
            except Exception:
                continue

        tech_data = _get_pair_technicals(CURRENT_WATCH_PAIR)
        ai_reasoning = db.get_state(f"llm_reasoning_{CURRENT_WATCH_PAIR}")
        _last_price = tech_data.get('last_price')
        combined_reasoning = ai_reasoning if ai_reasoning else _generate_technical_reasoning(CURRENT_WATCH_PAIR, tech_data, _last_price)

        history_since = conn.execute(
            "SELECT strftime('%d/%m %H:%M', MIN(updated_at)) FROM ticker_history"
        ).fetchone()[0] or '--/-- --:--'

        return {
            "daily_trades": perf['total_trades'],
            "daily_pnl": float(live_stats['total_pnl'] or 0) if live_stats else 0,
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
            "SELECT pair, trend, prediction_prob, ch_5m, ch_15m, ch_1h, volatility, score, history_json FROM ticker_history"
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
                
                prev = float(history[-1]) if history else last
                change = ((last - prev) / prev * 100) if prev > 0 else 0
                
                # Sinyal teknikal tetap pakai history (untuk trend)
                tech = get_technical_summary(history)
                sig  = derive_trade_signal(tech)

                results.append({
                    "pair":       pair,
                    "price":      last,
                    "change":     round(change, 2),
                    "change_24h": round(ch_24h, 2),
                    "ch_5m":      float(row['ch_5m'] or 0),
                    "ch_15m":     float(row['ch_15m'] or 0),
                    "ch_1h":      float(row['ch_1h'] or 0),
                    "volatility": float(row['volatility'] or 0),
                    "score":      float(row['score'] or 0),
                    "prediction": float(row['prediction_prob'] or 0.0),
                    "trend":      row['trend'] or 'N/A',
                    "tech_verdict":    sig['verdict'],
                    "tech_confidence": sig['confidence'],
                    "tech_reason":     sig['reason'],
                })
            except Exception:
                continue
        return results
    finally:
        conn.close()

def _build_trades_payload():
    """Ambil trade history + open positions dengan live PnL untuk Execution Ledger."""
    conn = get_db_connection()
    try:
        positions = conn.execute(
            "SELECT pair, entry_price, amount_crypto, amount_idr, timestamp as created_at FROM positions"
        ).fetchall()
        trades = conn.execute(
            "SELECT pair, reason, pnl_idr, pnl_pct, entry_price, exit_price, amount_idr, amount_crypto, duration_seconds, created_at FROM trade_history WHERE reason != 'BACKTEST' ORDER BY created_at DESC LIMIT 30"
        ).fetchall()

        results = []

        # Posisi terbuka — hitung live unrealized PnL dari harga terakhir
        for p in positions:
            entry_price  = float(p['entry_price'] or 0)
            amount_crypto = float(p['amount_crypto'] or 0)
            last_price   = entry_price  # fallback
            pnl_idr      = 0.0
            pnl_pct      = 0.0
            try:
                row = conn.execute(
                    "SELECT history_json FROM ticker_history WHERE pair = ?", (p['pair'],)
                ).fetchone()
                if row and row['history_json']:
                    history = json.loads(row['history_json'])
                    if history and entry_price > 0:
                        last_price = float(history[-1])
                        pnl_idr   = (last_price - entry_price) * amount_crypto
                        pnl_pct   = ((last_price - entry_price) / entry_price) * 100
            except Exception:
                pass

            results.append({
                "pair":        p['pair'] or 'UNKNOWN',
                "reason":      'OPEN POSITION',
                "pnl_idr":     round(pnl_idr, 0),
                "pnl_pct":     round(pnl_pct, 3),
                "entry_price": entry_price,
                "last_price":  last_price,
                "amount_idr":  float(p['amount_idr'] or 0),
                "amount_crypto": amount_crypto,
                "duration":    0, # Computed on frontend
                "created_at":  str(p['created_at'] or ''),
                "status":      'OPEN'
            })

        # Trade history (sudah ditutup)
        for t in trades:
            results.append({
                "pair":        t['pair'] or 'UNKNOWN',
                "reason":      t['reason'] or 'AI',
                "pnl_idr":     float(t['pnl_idr'] or 0),
                "pnl_pct":     float(t['pnl_pct'] or 0),
                "entry_price": float(t['entry_price'] or 0),
                "last_price":  float(t['exit_price'] or 0),
                "amount_idr":  float(t['amount_idr'] or 0),
                "amount_crypto": float(t['amount_crypto'] or 0),
                "duration":    int(t['duration_seconds'] or 0),
                "created_at":  str(t['created_at'] or ''),
                "status":      'CLOSED'
            })

        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return results[:30]
    except Exception:
        return []
    finally:
        conn.close()

def _build_logs_payload():
    """Ambil log sinyal AI dan oracle langsung dari DB."""
    conn = get_db_connection()
    try:
        ai_scans = conn.execute("""
            SELECT pair, prediction_prob, trend, updated_at as created_at
            FROM ticker_history WHERE prediction_prob > 0.4
            ORDER BY updated_at DESC LIMIT 10
        """).fetchall()

        oracle_data = conn.execute("""
            SELECT key, value, updated_at as created_at FROM bot_state
            WHERE key IN ('market_regime', 'regime_leader', 'latest_whale', 'latest_fake_pump')
            ORDER BY updated_at DESC
        """).fetchall()

        results = []
        for a in ai_scans:
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
        level_map = {'latest_whale': 'ALERT', 'latest_fake_pump': 'WARN'}
        for o in oracle_data:
            val = o['value'] or ''
            results.append({
                "created_at": str(o['created_at']),
                "pair":  val.split('|')[0].upper() if '|' in val else 'GLOBAL',
                "side":  icon_map.get(o['key'], '🔮 ORACLE'),
                "price": val.split('|')[1] if '|' in val else val,
                "status": "DETECTED",
                "level": level_map.get(o['key'], 'INFO')
            })

        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return results[:25]
    except Exception:
        return []
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
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
