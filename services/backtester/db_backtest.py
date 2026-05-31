"""
services/backtester/db_backtest.py
===================================
Thin database helper khusus untuk database `algovibe` (backtesting).
Terpisah dari app.infrastructure.database yang mengelola algovibe_live/dryrun.
"""
import os
import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras

logger = logging.getLogger("backtester.db")

DATABASE_URL_BACKTEST = os.getenv(
    "DATABASE_URL_BACKTEST",
    "postgresql://algovibe:secretpassword@timescaledb:5432/algovibe",
)


def get_connection():
    """Return a new psycopg2 connection with RealDictCursor factory."""
    conn = psycopg2.connect(DATABASE_URL_BACKTEST, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


@contextmanager
def transaction() -> Generator:
    """Context manager: yields cursor (RealDictCursor), commits on exit, rolls back on error."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
