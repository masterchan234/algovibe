"""add_intelligence_reports

Revision ID: a1b2c3d4e5f6
Revises: e2f1a2b3c4d5
Create Date: 2026-05-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e2f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS intelligence_reports (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        excerpt TEXT,
        content TEXT,
        category TEXT DEFAULT 'GENERAL',
        author TEXT DEFAULT 'ALGOVIBE_ORACLE',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_intelligence_reports_category_ts
        ON intelligence_reports(category, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intelligence_reports;")
