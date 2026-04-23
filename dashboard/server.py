import os
import sqlite3
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
    # PnL & Daily Trades
    stats = conn.execute("SELECT * FROM bot_state").fetchall()
    stats_dict = {row['key']: row['value'] for row in stats}
    
    # Open Positions
    positions = conn.execute("SELECT * FROM positions").fetchall()
    
    # Daily Performance
    perf = conn.execute("SELECT COUNT(*) as trades, SUM(pnl_idr) as pnl FROM trade_history WHERE date(created_at) = date('now')").fetchone()
    
    conn.close()
    return jsonify({
        "daily_trades": stats_dict.get('daily_trades', 0),
        "total_pnl": perf['pnl'] or 0,
        "active_positions": len(positions),
        "regime": stats_dict.get('market_regime', 'CHOPPY')
    })

@app.route('/api/radar')
def get_radar():
    conn = get_db_connection()
    # Get top 20 tickers with scores
    tickers = conn.execute("SELECT * FROM ticker_history ORDER BY updated_at DESC LIMIT 20").fetchall()
    
    # Get weights to calculate final score
    weights = conn.execute("SELECT * FROM dynamic_weights").fetchall()
    w_dict = {row['feature_name']: row['weight_value'] for row in weights}
    
    # Get sectors
    sectors = conn.execute("SELECT sector, sentiment_score FROM sector_history GROUP BY sector HAVING MAX(timestamp)").fetchall()
    s_dict = {row['sector']: row['sentiment_score'] for row in sectors}
    
    conn.close()
    
    results = []
    import json
    for t in tickers:
        try:
            history = json.loads(t['history_json'])
            last_price = history[-1] if history else 0
            # Dummy score for radar positioning if not in DB yet
            results.append({
                "pair": t['pair'],
                "price": last_price,
                "score": 50 + (len(history) % 50), # Placeholder for radar radius
                "sector": "ALT" # Default
            })
        except: continue
        
    return jsonify(results)

@app.route('/api/weights')
def get_weights():
    conn = get_db_connection()
    weights = conn.execute("SELECT feature_name, weight_value FROM dynamic_weights").fetchall()
    conn.close()
    return jsonify({row['feature_name']: row['weight_value'] for row in weights})

@app.route('/api/logs')
def get_logs():
    conn = get_db_connection()
    logs = conn.execute("SELECT * FROM order_logs ORDER BY created_at DESC LIMIT 10").fetchall()
    conn.close()
    return jsonify([dict(row) for row in logs])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)
