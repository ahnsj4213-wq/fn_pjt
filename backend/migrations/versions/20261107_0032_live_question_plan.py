"""question_sets.plan: the slots of a set whose questions are made one at a time during the interview (2026-11-07).

Live composition (ADR-0022): before the interview only the first main question is written; each later one is composed when the
candidate reaches it, from the previous answers, the resume and the company documents. The set keeps the plan it was built from
(area, custom text, mode per seq) so the later questions follow the same slots. NULL = every main question was written up front.

Revision ID: 0032
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE question_sets ADD COLUMN plan jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE question_sets DROP COLUMN plan")
