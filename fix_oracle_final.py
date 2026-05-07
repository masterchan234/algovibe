import os

file = "app/application/oracle.py"
with open(file, "r") as f:
    content = f.read()

# Reverse the bad replace
bad_replace = "import psycopg2; from config import DATABASE_URL_LIVE; conn = psycopg2.connect(DATABASE_URL_LIVE); from psycopg2.extras import DictCursor; conn.cursor_factory = DictCursor"
content = content.replace(bad_replace, "with db.get_connection() as conn:")

# Fix the hanging 'with db.get_connection() as conn:' at line 264
# We will use regex to fix it.
import re
content = re.sub(r"with db\.get_connection\(\) as conn:\s+((?:#.*?\n\s+)*)row = conn\.execute", r"with db.get_connection() as conn:\n                \1row = conn.execute", content)

# But wait, we need to indent everything under `with`!
# Let's just fix it by removing the `with db.get_connection() as conn:` and replacing it with just `conn = db.get_connection()` ... wait, `db.get_connection()` yields a context manager!
# So `conn = db.get_connection().__enter__()` would work!
# Or we can just run `git checkout app/application/oracle.py` and redo carefully.
