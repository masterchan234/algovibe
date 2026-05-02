import sqlite3
import json

try:
    conn = sqlite3.connect('data/algotrade.db')
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT pair, status, llm_verdict, llm_reason FROM trade_analytics ORDER BY entry_timestamp DESC LIMIT 10").fetchall()
    
    print(f"{'PAIR':<12} | {'STATUS':<10} | {'VERDICT':<8} | {'REASON'}")
    print("-" * 80)
    for r in rows:
        reason = r['llm_reason'][:50] + "..." if len(r['llm_reason']) > 50 else r['llm_reason']
        print(f"{r['pair']:<12} | {r['status']:<10} | {r['llm_verdict']:<8} | {reason}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
