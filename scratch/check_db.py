import sys
import os
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.infrastructure.database import db
from contextlib import _GeneratorContextManager

print(f"db.get_raw_connection type: {type(db.get_raw_connection())}")
conn = db.get_raw_connection()
if isinstance(conn, _GeneratorContextManager):
    print("WARNING: get_raw_connection returns a context manager but it's used as a direct connection!")
else:
    print("get_raw_connection returns a direct connection wrapper.")

try:
    print(f"Has execute: {hasattr(conn, 'execute')}")
    conn.close()
    print("Closed.")
except Exception as e:
    print(f"Error: {e}")
