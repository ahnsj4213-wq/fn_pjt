"""Operator review of company signups (screen spec SC-O01): a rejection keeps its reason.

- companies.reject_reason: shown to the company on its screens; cleared when the company uploads new evidence
  (a REJECTED company may apply again, which puts it back to PENDING).

Revision ID: 0013
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN reject_reason text CHECK (length(reject_reason) <= 500)")


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN reject_reason")
