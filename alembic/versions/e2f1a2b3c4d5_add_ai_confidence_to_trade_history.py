"""add_ai_confidence_to_trade_history

Revision ID: e2f1a2b3c4d5
Revises: a846c2688f1c
Create Date: 2026-05-16 19:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f1a2b3c4d5'
down_revision: Union[str, Sequence[str], None] = 'a846c2688f1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # We use execute because we want to handle the "IF NOT EXISTS" case 
    # since we already ran it manually.
    op.execute("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS ai_confidence DOUBLE PRECISION DEFAULT 0.0;")

def downgrade() -> None:
    op.execute("ALTER TABLE trade_history DROP COLUMN IF EXISTS ai_confidence;")
