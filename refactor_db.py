import sys
import re

with open("app/infrastructure/database.py", "r") as f:
    content = f.read()

# 1. Imports
content = content.replace("import sqlite3", "import psycopg2\nfrom psycopg2.pool import ThreadedConnectionPool\nfrom psycopg2.extras import DictCursor")

# 2. Config imports
content = content.replace("from config import DB_PATH", "from config import DATABASE_URL_LIVE, DATABASE_URL_DRYRUN")

# 3. Connection manager & wrapper
wrapper_code = """
class ConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn
    def cursor(self):
        return self.conn.cursor(cursor_factory=DictCursor)
    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params or ())
        return cur
    def commit(self):
        self.conn.commit()
    def rollback(self):
        self.conn.rollback()
    def close(self):
        pass # Handled by pool

class Database:
    def __init__(self, db_url=DATABASE_URL_LIVE):
        self.db_url = db_url
        self.pool = ThreadedConnectionPool(1, 20, self.db_url)
        self._init_db()

    @contextmanager
    def get_connection(self):
        raw_conn = self.pool.getconn()
        conn = ConnectionWrapper(raw_conn)
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            self.pool.putconn(raw_conn)
"""

# Replace from `class Database:` down to `_init_db`
# Need regex to match class Database up to _init_db
pattern = re.compile(r"class Database:.*?def _init_db\(self\):", re.DOTALL)
content = pattern.sub(wrapper_code.strip() + "\n\n    def _init_db(self):", content)

# 4. Remove integrity check and backup methods completely
content = re.sub(r"def _run_integrity_check\(self\) -> None:.*?def create_snapshot\(self\) -> str \| None:.*?(?=    @contextmanager)", "", content, flags=re.DOTALL)

# 5. Fix SQL syntax
# Replace all '?' with '%s'
# But only inside strings that are SQL queries. The easiest way is to just replace '?' with '%s' where there are queries.
# Wait, replacing all '?' might break other strings or comments.
# Let's replace `?` with `%s` globally, but only if it's not preceded by something that makes it a regex or ternary. In Python there are no ternaries with ?.
content = content.replace("?", "%s")

# Fix SQLite specific syntaxes
content = content.replace("datetime('now', '-24 hours')", "NOW() - INTERVAL '24 hours'")
content = content.replace("datetime('now', 'localtime')", "CURRENT_TIMESTAMP")
content = content.replace("datetime('now')", "CURRENT_TIMESTAMP")

content = content.replace("INSERT OR IGNORE INTO bot_state (key, value) VALUES ('dry_run', %s)", "INSERT INTO bot_state (key, value) VALUES ('dry_run', %s) ON CONFLICT (key) DO NOTHING")
content = content.replace("INSERT OR IGNORE INTO dynamic_weights", "INSERT INTO dynamic_weights") # It's okay if it fails or we just do ON CONFLICT (feature_name) DO NOTHING
content = content.replace("ON CONFLICT (feature_name) DO NOTHING", "") # clean up
content = content.replace("INSERT INTO dynamic_weights (feature_name, weight_value) VALUES", "INSERT INTO dynamic_weights (feature_name, weight_value) VALUES") # wait, we can just replace INSERT OR IGNORE
content = content.replace("INSERT OR IGNORE INTO", "INSERT INTO") # Wait, if we replace INSERT OR IGNORE INTO with INSERT INTO ... ON CONFLICT DO NOTHING, we need to know the ON CONFLICT key.
# It's better to just use standard try/except for these initialization inserts, but they are already wrapped in `try: except: pass`. So we can just use `INSERT INTO`.
content = content.replace("INSERT INTO bot_state (key, value) VALUES ('dry_run', %s)", "INSERT INTO bot_state (key, value) VALUES ('dry_run', %s) ON CONFLICT DO NOTHING")

# Fix `INSERT OR REPLACE INTO shadow_orders` -> Postgres `ON CONFLICT (pair) DO UPDATE SET ...`
content = content.replace("INSERT OR REPLACE INTO shadow_orders (pair, order_id, price, amount, status) VALUES (%s, %s, %s, %s, 'OPEN')", 
                          "INSERT INTO shadow_orders (pair, order_id, price, amount, status) VALUES (%s, %s, %s, %s, 'OPEN') ON CONFLICT (pair) DO UPDATE SET order_id=EXCLUDED.order_id, price=EXCLUDED.price, amount=EXCLUDED.amount, status=EXCLUDED.status")

# Fix `INSERT OR REPLACE INTO blacklist`
content = content.replace("INSERT OR REPLACE INTO blacklist (pair, reason) VALUES (%s, %s)",
                          "INSERT INTO blacklist (pair, reason) VALUES (%s, %s) ON CONFLICT (pair) DO UPDATE SET reason=EXCLUDED.reason")

# Fix `INSERT OR REPLACE INTO bot_state`
content = content.replace("INSERT OR REPLACE INTO bot_state (key, value) VALUES (%s, %s)",
                          "INSERT INTO bot_state (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP")

# Fix `INSERT OR REPLACE INTO ticker_history`
content = content.replace("INSERT OR REPLACE INTO ticker_history (pair, history_json, prediction_prob, trend, signal, ch_5m, ch_15m, ch_1h, volatility, score)",
                          "INSERT INTO ticker_history (pair, history_json, prediction_prob, trend, signal, ch_5m, ch_15m, ch_1h, volatility, score)")
content = content.replace("VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", 
                          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (pair) DO UPDATE SET history_json=EXCLUDED.history_json, prediction_prob=EXCLUDED.prediction_prob, trend=EXCLUDED.trend, signal=EXCLUDED.signal, ch_5m=EXCLUDED.ch_5m, ch_15m=EXCLUDED.ch_15m, ch_1h=EXCLUDED.ch_1h, volatility=EXCLUDED.volatility, score=EXCLUDED.score, updated_at=CURRENT_TIMESTAMP")


with open("app/infrastructure/database.py", "w") as f:
    f.write(content)
