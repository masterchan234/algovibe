import os
import re

file = "app/application/oracle.py"
with open(file, "r") as f:
    content = f.read()

# 1. Fix `conn = sqlite3.connect` to `with db.get_connection() as conn:` but we will use a different trick to avoid indent issues.
# We can just do: `conn_cm = db.get_connection(); conn = conn_cm.__enter__()` and in `finally: conn_cm.__exit__(None, None, None)`
# But it's easier to just do: `conn_ctx = db.get_connection(); conn = conn_ctx.__enter__()`
# Let's replace:
content = content.replace("conn = sqlite3.connect(db.db_path, timeout=30)", "conn_ctx = db.get_connection()\n            conn = conn_ctx.__enter__()")
content = content.replace("conn.row_factory = sqlite3.Row", "")
# Fix finally close:
content = content.replace("if conn is not None:\n                conn.close()", "if 'conn_ctx' in locals():\n                conn_ctx.__exit__(None, None, None)")

# Fix exception type
content = content.replace("except sqlite3.DatabaseError", "except Exception")

# 2. Fix other isolated sqlite3.connect instances
content = content.replace("import sqlite3", "") # remove imports
# Also, around line 1672:
content = content.replace("conn = sqlite3.connect(db.db_path)", "conn_ctx = db.get_connection()\n            conn = conn_ctx.__enter__()")

with open(file, "w") as f:
    f.write(content)
