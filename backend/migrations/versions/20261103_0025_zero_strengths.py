"""A report may have no strengths.

An interview where nothing was answered has nothing to praise; the report says so instead of inventing a strength.
Strengths are 0~3 now, improvements stay 1~3 (the report always tells the candidate what to do next).

Revision ID: 0025
"""
from alembic import op
from sqlalchemy import text

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def _drop_points_check() -> None:
    conn = op.get_bind()
    names = conn.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'recruiter_reports'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) LIKE '%jsonb_array_length(strengths)%'")).scalars().all()
    for n in names:
        conn.execute(text(f'ALTER TABLE recruiter_reports DROP CONSTRAINT "{n}"'))


def upgrade() -> None:
    _drop_points_check()
    op.execute("ALTER TABLE recruiter_reports ADD CONSTRAINT recruiter_reports_points_check CHECK "
               "(jsonb_array_length(strengths) BETWEEN 0 AND 3 AND jsonb_array_length(improvements) BETWEEN 1 AND 3)")


def downgrade() -> None:
    _drop_points_check()
    op.execute("ALTER TABLE recruiter_reports ADD CONSTRAINT recruiter_reports_points_check CHECK "
               "(jsonb_array_length(strengths) BETWEEN 1 AND 3 AND jsonb_array_length(improvements) BETWEEN 1 AND 3)")
