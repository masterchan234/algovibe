import os

files_to_fix = [
    "app/domain/scoring.py",
    "app/infrastructure/auto_trainer.py",
    "app/application/oracle.py"
]

for file in files_to_fix:
    if not os.path.exists(file):
        continue
    with open(file, "r") as f:
        content = f.read()

    # Generic replace for import sqlite3
    content = content.replace("import sqlite3", "import psycopg2")
    
    # oracle.py specific
    content = content.replace("conn = sqlite3.connect(db.db_path, timeout=30)", "with db.get_connection() as conn:")
    # Replace `conn.row_factory = sqlite3.Row` with nothing
    content = content.replace("conn.row_factory = sqlite3.Row", "")
    content = content.replace("except sqlite3.DatabaseError", "except Exception")
    # auto_trainer.py specific
    content = content.replace("sqlite3.connect(db_path, timeout=30)", "psycopg2.connect(DATABASE_URL_LIVE)")
    content = content.replace("sqlite3.connect(DB_PATH, timeout=30)", "psycopg2.connect(DATABASE_URL_LIVE)")
    
    with open(file, "w") as f:
        f.write(content)

print("Done fixing sqlite occurrences.")
