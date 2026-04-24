import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, '/home/chandev/.local/lib/python3.13/site-packages')
import sqlite3
import json
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from engines.database import db

app = Flask(__name__, static_folder='static')
CORS(app)

from config import DB_PATH

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

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
        perf = db.get_daily_stats() # Gunakan fungsi yang sudah ada di db engine
        
        # All time performance for winrate
        all_time = conn.execute("SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) as wins FROM trade_history").fetchone()
        win_rate = (all_time['wins'] / all_time['trades'] * 100) if all_time['trades'] > 0 else 0
        
        # Sector Sentiment
        sectors = db.get_all_latest_sector_sentiment()
        
        return jsonify({
            "daily_trades": perf['total_trades'],
            "daily_pnl": float(perf['total_pnl_idr']),
            "win_rate": round(win_rate, 2),
            "total_trades_all_time": all_time['trades'],
            "active_positions": len(positions),
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
        positions = conn.execute("SELECT pair, entry_price, amount_crypto, amount_idr, status, timestamp as created_at FROM positions").fetchall()
        
        # Fetch recent trades
        trades = db.get_recent_trades(limit=20)
        
        results = []
        for p in positions:
            results.append({
                "pair": p['pair'] or "UNKNOWN",
                "reason": "OPEN POSITION",
                "pnl_idr": 0, 
                "created_at": p['created_at'] or "1970-01-01T00:00:00",
                "status": "OPEN"
            })
            
        for t in trades:
            results.append({
                "pair": t['pair'] or "UNKNOWN",
                "reason": t['reason'] or "UNKNOWN",
                "pnl_idr": t['pnl_idr'] or 0,
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

@app.route('/api/weights')
def get_weights():
    conn = get_db_connection()
    try:
        weights = conn.execute("SELECT feature_name, weight_value FROM dynamic_weights").fetchall()
        return jsonify({row['feature_name']: row['weight_value'] for row in weights})
    except:
        return jsonify({})
    finally:
        conn.close()

@app.route('/api/logs')
def get_logs():
    conn = get_db_connection()
    try:
        orders = conn.execute("SELECT * FROM order_logs ORDER BY created_at DESC LIMIT 10").fetchall()
        ai_scans = conn.execute("SELECT pair, prediction_prob, trend, updated_at as created_at FROM ticker_history WHERE prediction_prob > 0.5 ORDER BY updated_at DESC LIMIT 10").fetchall()
        
        results = []
        for o in orders:
            results.append({
                "created_at": o['created_at'],
                "pair": o['pair'],
                "side": o['side'],
                "price": o['price'],
                "status": o['status']
            })
            
        for a in ai_scans:
            results.append({
                "created_at": a['created_at'],
                "pair": a['pair'],
                "side": "AI SIGNAL",
                "price": f"Prob {round(a['prediction_prob']*100)}%",
                "status": a['trend']
            })
            
        results.sort(key=lambda x: str(x['created_at']), reverse=True)
        return jsonify(results[:15])
    except Exception as e:
        import traceback
        print("LOG ERROR:", traceback.format_exc())
        return jsonify([])
    finally:
        conn.close()

if __name__ == '__main__':
    # Use standard logger to see errors
    app.run(host='0.0.0.0', port=5005, debug=False)
