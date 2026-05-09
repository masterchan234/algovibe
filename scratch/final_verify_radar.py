
import sys
import os
import datetime
sys.path.append('/root/algovibe')
from app.infrastructure.database import db

def test_radar_audit_final():
    conn = db._active().get_raw_connection()
    try:
        sql = """
            SELECT key, value, updated_at
            FROM bot_state
            WHERE key LIKE %s
               OR key LIKE %s
               OR key LIKE %s
        """
        params = ('radar_reco_%', 'gate_skip_%', 'llm_veto_ts_%')
        print(f"Executing query with params: {params}")
        rows = conn.execute(sql, params).fetchall()
        print(f"Success! Fetched {len(rows)} rows.")
        
        now = datetime.datetime.now()
        for r in rows:
            ts = r['updated_at']
            if ts and not isinstance(ts, datetime.datetime):
                ts = datetime.datetime.fromisoformat(str(ts).replace(' ', 'T'))
            if ts and ts.tzinfo:
                ts = ts.replace(tzinfo=None)
            
            if ts:
                diff = (now - ts).total_seconds()
                print(f"Key: {r['key']}, Freshness: {diff}s")
                
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    test_radar_audit_final()
