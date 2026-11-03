"""Evaluation details (screen spec SC-A08): sub-item scores and STAR per answer criterion, and the report-level roll-up
(sub-items, STAR rate, negative expressions, answer time, speaking speed, clarity, pitch) with how it was blended into the
area scores. Older rows keep NULL; the report screen shows the details only when they exist.

Revision ID: 0023
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE answer_evaluations ADD COLUMN detail jsonb")
    op.execute("ALTER TABLE recruiter_reports ADD COLUMN detail jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE recruiter_reports DROP COLUMN detail")
    op.execute("ALTER TABLE answer_evaluations DROP COLUMN detail")
