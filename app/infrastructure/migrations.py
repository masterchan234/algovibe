import os
from alembic import command
from alembic.config import Config
from utils.logger import log_message

def run_migrations(db_url):
    """Apply all migrations to the database using Alembic."""
    alembic_cfg = Config("alembic.ini")
    log_message(f"[START] Running migrations for {db_url}...")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")

def sync_schema_both_dbs():
    from config import DATABASE_URL_LIVE, DATABASE_URL_DRYRUN
    run_migrations(DATABASE_URL_LIVE)
    run_migrations(DATABASE_URL_DRYRUN)

if __name__ == "__main__":
    sync_schema_both_dbs()
