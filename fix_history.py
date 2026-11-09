import re

with open('dashboard/server.py', 'r') as f:
    content = f.read()

old_func_pattern = r'@app\.route\(\'/api/pnl-history/<tf>\'\)\n#@login_required\ndef get_pnl_history_api\(tf\):.*?finally:\n        conn\.close\(\)'

new_func = """@app.route('/api/pnl-history/<tf>')
#@login_required
def get_pnl_history_api(tf):
    \"\"\"
    Menyusun histori ekuitas (PnL kumulatif) dari database equity_performance.
    \"\"\"
    conn = get_db_connection()
    try:
        import config as cfg
        dr_val = True if _is_dry_run() else False
        eid = cfg.EXCHANGE.lower()
        
        # Configure buckets based on timeframe
        if tf == 'live':
            interval = '-2 hours'
            bucket = '1 minute'
        elif tf == '1h':
            interval = '-1 hours'
            bucket = '1 minute'
        elif tf == '24h':
            interval = '-24 hours'
            bucket = '15 minutes'
        elif tf == '7d':
            interval = '-7 days'
            bucket = '4 hours'
        else:
            interval = '-24 hours'
            bucket = '15 minutes'
            
        rows = conn.execute(f\"\"\"
            SELECT 
                time_bucket(%s, ts) as bucket_time, 
                AVG(realized_pnl_total + unrealized_pnl) as net_pnl 
            FROM equity_performance 
            WHERE ts >= NOW() + (%s)::interval AND dry_run = %s AND exchange = %s
            GROUP BY bucket_time 
            ORDER BY bucket_time ASC
        \"\"\", (bucket, interval, dr_val, eid)).fetchall()
        
        results = []
        for r in rows:
            ts = r['bucket_time']
            dt = ts if isinstance(ts, datetime.datetime) else datetime.datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S%z" if "+" in str(ts) else "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%M:%S") if tf in ('live', '1h') else dt.strftime("%H:%M")
            results.append({
                "time": time_str,
                "pnl": round(float(r['net_pnl'] or 0.0), 4 if eid == 'binance' else 0)
            })
            
        return jsonify(results)
    except Exception as e:
        import traceback
        print(f"PNL HISTORY ERROR: {e}")
        traceback.print_exc()
        return jsonify([])
    finally:
        conn.close()"""

content = re.sub(old_func_pattern, new_func, content, flags=re.DOTALL)

with open('dashboard/server.py', 'w') as f:
    f.write(content)
