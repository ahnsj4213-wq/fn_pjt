"""Difficulty (하/중/상) on coding problems. The problems themselves are templates, created by revision 0022.

Revision ID: 0021
"""
from alembic import op
from sqlalchemy import text

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE coding_problems ADD COLUMN difficulty text CHECK (difficulty IN ('하', '중', '상'))"))


def downgrade() -> None:
    op.execute("ALTER TABLE coding_problems DROP COLUMN difficulty")
