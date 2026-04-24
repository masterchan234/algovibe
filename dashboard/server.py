import os
import sys
import time
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, '/home/chandev/.local/lib/python3.13/site-packages')
import sqlite3
import json
from flask import Flask, jsonify, send_from_directory, Response
from flask_cors import CORS
from engines.database import db

app = Flask(__name__, static_folder='static')
CORS(app)

from config import DB_PATH, DRY_RUN

_start_time = time.time()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/x-icon')

@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory(app.static_folder, path)

@app.route('/api/stats')
def get_stats():
    conn = get_db_connection()
    try:
        # PnL & Daily Trades
        stats = conn.execute("SELECT * FROM bot_state").fetchall()
        stats_dict = {row['key']: row['value'] for row in stats}

        # Open Positions
        positions = conn.execute("SELECT * FROM positions").fetchall()

        # Performance Rekap
        perf = db.get_daily_stats()

        # All time performance for winrate
        all_time = conn.execute(
            "SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins FROM trade_history"
        ).fetchone()
        win_rate = (all_time['wins'] / all_time['trades'] * 100) if all_time['trades'] > 0 else 0

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

        return jsonify({
            "daily_trades": perf['total_trades'],
            "daily_pnl": float(perf['total_pnl_idr']),
            "daily_wins": perf['win_count'],
            "daily_losses": perf['loss_count'],
            "win_rate": round(win_rate, 2),
            "total_trades_all_time": all_time['trades'],
            "active_positions": len(positions),
            "unrealized_pnl": round(unrealized_pnl, 0),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', ''),
            "sectors": sectors
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

            # Durasi posisi
            duration_sec = 0
            try:
                import datetime
                ts = pos['timestamp']
                if ts:
                    opened_at = datetime.datetime.fromisoformat(str(ts).replace('Z', ''))
                    duration_sec = int((datetime.datetime.now() - opened_at).total_seconds())
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
                import datetime
                updated = datetime.datetime.fromisoformat(str(last_state['updated_at']))
                age_sec = (datetime.datetime.now() - updated).total_seconds()
                bot_alive = age_sec < 120  # Anggap alive jika update < 120 detik lalu
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
    except:
        return jsonify([])
    finally:
        conn.close()

@app.route('/api/trades/recent')
def get_recent_trades_api():
    conn = get_db_connection()
    try:
        # Fetch open positions
        positions = conn.execute(
            "SELECT pair, entry_price, amount_crypto, amount_idr, status, timestamp as created_at FROM positions"
        ).fetchall()

        # Fetch recent trades
        trades = db.get_recent_trades(limit=20)

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
        tickers = conn.execute("SELECT * FROM ticker_history").fetchall()
        results = []
        for t in tickers:
            try:
                history = json.loads(t['history_json']) if t['history_json'] else []
                if not history: continue

                last_price = float(history[-1])
                first_price = float(history[0])

                # Hitung perubahan harga dari history
                change = ((last_price - first_price) / first_price * 100) if first_price > 0 else 0

                results.append({
                    "pair": t['pair'],
                    "price": last_price,
                    "change": round(change, 2),
                    "prediction": float(t['prediction_prob'] or 0.0),
                    "trend": t['trend'] or 'N/A'
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=False)
