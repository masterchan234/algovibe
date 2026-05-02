import sqlite3

try:
    conn = sqlite3.connect('data/algotrade.db')
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT pair, message FROM system_events WHERE type = 'ANALYSIS' ORDER BY created_at DESC LIMIT 10").fetchall()
    
    for r in rows:
        print(f"[{r['pair']}] {r['message']}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
