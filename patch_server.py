import re

with open('dashboard/server.py', 'r') as f:
    content = f.read()

old_block = """    ws_tickers = mws.get_tickers()
    try:
        rows = conn.execute(
            "SELECT pair, trend, prediction_prob, ch_5m, ch_15m, ch_1h, volatility, score, history_json, updated_at "
            "FROM ticker_history WHERE score > 0"
        ).fetchall()
        results = []
        for row in rows:
            try:
                history = json.loads(row['history_json'] or '[]')
                if not history: continue
                last = float(history[-1])
                prev = float(history[-2]) if len(history) > 1 else last
                change = ((last - prev) / prev) * 100 if prev > 0 else 0
                
                pair = row['pair']
                ws_data = ws_tickers.get(pair, {}) or ws_tickers.get(pair.upper(), {})
                ch_24h = float(ws_data.get('change_24h') or 0)
                if abs(ch_24h) < 1e-9:
                    ch_24h = float(row['ch_1h'] or 0)

                results.append({
                    "pair": pair,
                    "price": last,
                    "change": round(change, 2),
                    "ch_5m": float(row['ch_5m'] or 0),
                    "ch_15m": float(row['ch_15m'] or 0),
                    "ch_1h": float(row['ch_1h'] or 0),
                    "ch_24h": round(ch_24h, 2),
                    "volatility": float(row['volatility'] or 0),
                    "score": float(row['score'] or 0),
                    "prediction": float(row['prediction_prob'] or 0.0),
                    "trend": row['trend'] if row['trend'] not in ('BACKFILLED', None, '') else 'N/A'
                })
            except: continue
        return jsonify(results)"""

new_block = """    ws_tickers = mws.get_tickers()
    try:
        rows = conn.execute(
            "SELECT pair, trend, prediction_prob, ch_5m, ch_15m, ch_1h, volatility, score, history_json, updated_at "
            "FROM ticker_history"
        ).fetchall()
        
        history_map = {row['pair'].lower(): dict(row) for row in rows}
        results = []
        
        for k, v in ws_tickers.items():
            pair = k.lower()
            last = float(v.get('last') or 0)
            if last == 0: continue
            
            row = history_map.get(pair, {})
            score = float(row.get('score') or 0)
            
            history = json.loads(row.get('history_json') or '[]')
            prev = float(history[-2]) if len(history) > 1 else last
            change = ((last - prev) / prev) * 100 if prev > 0 else 0
            
            trend = row.get('trend')
            if trend in ('BACKFILLED', None, ''):
                trend = "UP" if float(v.get('change_24h') or 0) > 0 else "DOWN"

            results.append({
                "pair": pair.upper(),
                "price": last,
                "change": round(change, 2),
                "ch_5m": float(row.get('ch_5m') or 0),
                "ch_15m": float(row.get('ch_15m') or 0),
                "ch_1h": float(row.get('ch_1h') or 0),
                "ch_24h": round(float(v.get('change_24h') or 0), 2),
                "volatility": float(row.get('volatility') or 0),
                "score": score,
                "prediction": float(row.get('prediction_prob') or 0.0),
                "trend": trend
            })
            
        return jsonify(results)"""

if old_block in content:
    with open('dashboard/server.py', 'w') as f:
        f.write(content.replace(old_block, new_block))
    print("Patched successfully!")
else:
    print("Block not found!")
    # fallback to find the function and replace it fully
    import ast
    print("Attempting regex replacement...")
    # ...

