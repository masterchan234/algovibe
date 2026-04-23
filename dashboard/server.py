import os
import sqlite3
import json
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

DB_PATH = '/root/projects/crypto/algotrade/data/algotrade.db'

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
        
        # Daily Performance
        perf = conn.execute("SELECT COUNT(*) as trades, SUM(pnl_idr) as pnl FROM trade_history WHERE date(created_at) = date('now')").fetchone()
        
        return jsonify({
            "daily_trades": int(stats_dict.get('daily_trades', 0)),
            "total_pnl": float(perf['pnl'] or 0),
            "active_positions": len(positions),
            "regime": stats_dict.get('market_regime', 'CHOPPY'),
            "regime_leader": stats_dict.get('regime_leader', ''),
            "latest_whale": stats_dict.get('latest_whale', ''),
            "latest_fake_pump": stats_dict.get('latest_fake_pump', '')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
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
                
                # Hitung perubahan harga dari history (Proxy untuk momentum)
                change = ((last_price - first_price) / first_price * 100) if first_price > 0 else 0
                
                # Score 0-100 (Sederhana: makin besar kenaikan, makin ke tengah)
                score = min(100, max(0, 50 + (change * 10)))
                
                results.append({
                    "pair": t['pair'],
                    "price": last_price,
                    "score": score,
                    "change": round(change, 2),
                    "sector": "ALT" 
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
        logs = conn.execute("SELECT * FROM order_logs ORDER BY created_at DESC LIMIT 10").fetchall()
        return jsonify([dict(row) for row in logs])
    except:
        return jsonify([])
    finally:
        conn.close()

if __name__ == '__main__':
    # Use standard logger to see errors
    app.run(host='0.0.0.0', port=5005, debug=False)
