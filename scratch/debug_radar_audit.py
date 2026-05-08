
import sys
import os
import datetime
sys.path.append('/root/algovibe')
from app.infrastructure.database import db

def test_radar_audit():
    conn = db._active().get_raw_connection()
    try:
        # Use %% to escape % in LIKE for psycopg2 if needed, 
        # but here we use params to be safe and avoid interpretation issues.
        rows = conn.execute(
            """
            SELECT key, value, updated_at
            FROM bot_state
            WHERE key LIKE %s
               OR key LIKE %s
               OR key LIKE %s
            LIMIT 5
            """,
            ('radar_reco_%', 'gate_skip_%', 'llm_veto_ts_%')
        ).fetchall()
        
        for r in rows:
            print(f"Key: {r['key']}, Type: {type(r['updated_at'])}, Value: {r['updated_at']}")
            ts_val = r['updated_at']
            
            # Simulate server.py logic
            ts_text = str(ts_val or '')
            print(f"Str: {ts_text}")
            try:
                ts = datetime.datetime.fromisoformat(ts_text.replace(' ', 'T'))
                print(f"Parsed: {ts}, Aware: {ts.tzinfo is not None}")
                
                now = datetime.datetime.now()
                print(f"Now (naive): {now}")
                try:
                    diff = (now - ts).total_seconds()
                    print(f"Diff: {diff}")
                except Exception as e:
                    print(f"!!! Subtraction Error: {e}")
                    
            except Exception as e:
                print(f"Error parsing: {e}")
                
    finally:
        conn.close()

if __name__ == "__main__":
    test_radar_audit()
