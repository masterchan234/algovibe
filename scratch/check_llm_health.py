import os
import sys
from app.infrastructure.database import db
from app.infrastructure.llm_worker import LLMWorker

# We need to access the singleton instance if possible, 
# but it's in another process usually.
# However, we can check the stats if the main app is running.
# In this environment, we can just try to see if the worker is even active.

worker = LLMWorker.get_instance()
stats = worker.get_stats()
print(f"LLM Worker Stats: {stats}")

# Also check for any recent AI_ANALYSIS events
import sqlite3
try:
    conn = sqlite3.connect('data/algotrade.db')
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT pair, message, created_at FROM system_events WHERE type = 'AI_ANALYSIS' ORDER BY created_at DESC LIMIT 5").fetchall()
    print("\nRecent AI Analysis Events:")
    for r in rows:
        print(f"[{r['created_at']}] {r['pair']}: {r['message']}")
    
    # Check trade_analytics
    dna = conn.execute("SELECT pair, status, llm_verdict, llm_reason FROM trade_analytics ORDER BY entry_timestamp DESC LIMIT 5").fetchall()
    print("\nLatest Trade DNA:")
    for d in dna:
        print(f"{d['pair']}: {d['status']} | {d['llm_verdict']} | {d['llm_reason'][:50]}...")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
