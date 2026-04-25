import sqlite3
import os

db_path = 'data/algotrade.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cols = [
        ("ch_5m", "REAL DEFAULT 0.0"),
        ("ch_15m", "REAL DEFAULT 0.0"),
        ("ch_1h", "REAL DEFAULT 0.0")
    ]
    
    for col, type_def in cols:
        try:
            cursor.execute(f"ALTER TABLE ticker_history ADD COLUMN {col} {type_def}")
            print(f"Added column {col}")
        except sqlite3.OperationalError as e:
            print(f"Column {col} already exists or error: {e}")
            
    conn.commit()
    conn.close()
