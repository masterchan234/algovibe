#!/usr/bin/env python3
"""
scripts/split_db.py — One-time migration: split algotrade.db → live + dryrun DBs.

Run from project root:
    python3 scripts/split_db.py

Safe to re-run: uses INSERT OR IGNORE, so no duplicate rows.
After verifying, the original algotrade.db is left untouched.
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH, DB_PATH_LIVE, DB_PATH_DRYRUN
from app.infrastructure.database import Database


def copy_rows(src_conn, dst_conn, table: str, where: str = None, params=()):
    try:
        query = f"SELECT * FROM {table}"
        if where:
            query += f" WHERE {where}"
        cur = src_conn.execute(query, params)
        src_cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as e:
        print(f"  {table}: SKIP (source error: {e})")
        return

    if not rows:
        print(f"  {table}: 0 rows")
        return

    # Only insert columns that exist in the destination schema
    try:
        dst_cur = dst_conn.execute(f"SELECT * FROM {table} LIMIT 0")
        dst_cols = {d[0] for d in dst_cur.description}
    except Exception as e:
        print(f"  {table}: SKIP (dst schema error: {e})")
        return

    # Intersect: use only columns present in both src and dst
    common_idx = [i for i, c in enumerate(src_cols) if c in dst_cols]
    common_cols = [src_cols[i] for i in common_idx]
    filtered_rows = [tuple(row[i] for i in common_idx) for row in rows]

    col_names = ", ".join(common_cols)
    placeholders = ", ".join(["?" for _ in common_cols])
    try:
        dst_conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
            filtered_rows,
        )
        dst_conn.commit()
        print(f"  {table}: {len(filtered_rows)} rows → OK")
    except Exception as e:
        print(f"  {table}: INSERT ERROR — {e}")


def main():
    if not os.path.exists(DB_PATH):
        print(f"Source DB not found: {DB_PATH}")
        sys.exit(1)

    print(f"Source  : {DB_PATH}")
    print(f"Live DB : {DB_PATH_LIVE}")
    print(f"Dryrun  : {DB_PATH_DRYRUN}")
    print()

    # --- Init schema on both new DBs ---
    print("Initialising live DB schema...")
    Database(DB_PATH_LIVE)
    print("Initialising dryrun DB schema...")
    Database(DB_PATH_DRYRUN)
    print()

    src = sqlite3.connect(DB_PATH)

    # Shared tables: copied as-is to both DBs
    shared_tables = [
        "bot_state",
        "blacklist",
        "ticker_history",
        "market_depth",
        "dynamic_weights",
        "sector_history",
        "users",
        "system_events",
        "order_logs",      # no dry_run column — copy all to both
        "shadow_orders",   # no dry_run column — copy all to both
    ]

    # Tables that carry a dry_run flag → split by value
    split_tables = [
        "positions",
        "trade_history",
        "trade_analytics",
    ]

    for dst_path, label in [(DB_PATH_LIVE, "LIVE"), (DB_PATH_DRYRUN, "DRYRUN")]:
        print(f"=== Shared tables → {label} ===")
        dst = sqlite3.connect(dst_path)
        for table in shared_tables:
            copy_rows(src, dst, table)
        dst.close()
        print()

    for dst_path, label, where in [
        (DB_PATH_LIVE,   "LIVE",   "dry_run = 0"),
        (DB_PATH_DRYRUN, "DRYRUN", "dry_run = 1"),
    ]:
        print(f"=== Trade tables ({where}) → {label} ===")
        dst = sqlite3.connect(dst_path)
        for table in split_tables:
            copy_rows(src, dst, table, where)
        dst.close()
        print()

    src.close()

    print("Migration complete.")
    print(f"Original DB preserved at: {DB_PATH}")
    print("Restart services to activate new databases.")


if __name__ == "__main__":
    main()
