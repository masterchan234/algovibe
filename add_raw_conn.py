import sys

with open("app/infrastructure/database.py", "r") as f:
    content = f.read()

# Add get_raw_connection to database.py
if "def get_raw_connection" not in content:
    wrapper_code = """
class ConnectionWrapper:
    def __init__(self, conn, pool=None):
        self.conn = conn
        self.pool = pool
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
        if self.pool:
            self.pool.putconn(self.conn)
"""
    content = content.replace("""class ConnectionWrapper:
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
        pass # Handled by pool""", wrapper_code)
    
    content = content.replace("conn = ConnectionWrapper(raw_conn)", "conn = ConnectionWrapper(raw_conn, self.pool)")

    # add get_raw_connection
    get_raw = """
    def get_raw_connection(self):
        raw = self.pool.getconn()
        return ConnectionWrapper(raw, self.pool)
"""
    content = content.replace("def get_connection(self):", get_raw + "\n    @contextmanager\n    def get_connection(self):")

with open("app/infrastructure/database.py", "w") as f:
    f.write(content)

