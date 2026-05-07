import os

file = "dashboard/server.py"
with open(file, "r") as f:
    content = f.read()

# 1. Generic imports
content = content.replace("import sqlite3", "import psycopg2")

# 2. Replaces for `active_db`
content = content.replace("conn = sqlite3.connect(active_db.db_path, timeout=30)", "conn = active_db.get_raw_connection()")
content = content.replace("conn.row_factory = sqlite3.Row\n", "")

# 3. Replaces for `live_conn`
# We can just use `active_db._live.get_raw_connection()` since active_db is the MultiDatabase implementation!
content = content.replace("live_conn = sqlite3.connect(cfg.DB_PATH_LIVE, timeout=30)", "live_conn = active_db._live.get_raw_connection()")
content = content.replace("live_conn.row_factory = sqlite3.Row\n", "")

# 4. Handle ? bindings
# There's a few queries with `?` in server.py? I will do `.replace("?", "%s")`.
# But `?` is used in URLs like `req.args.get('?')`? No, let's carefully replace `?` in queries.
# Since we can just replace '?' with '%s', I will do it. Let's make sure there are no other ?
content = content.replace("= ?", "= %s")
content = content.replace("IN (?, ?, ?)", "IN (%s, %s, %s)")
content = content.replace("LIKE ?", "LIKE %s")

with open(file, "w") as f:
    f.write(content)
