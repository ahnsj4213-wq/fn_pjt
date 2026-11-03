"""Question numbers behind each strength and improvement (screen spec SC-A08 6: '관련 질문 Q○ 보기').

recruiter_reports.point_refs = {"strengths": [[4, 5], ...], "improvements": [[3], ...]}, in the order of the texts in
`strengths` / `improvements`. Reports written before this have NULL (no links).

Revision ID: 0018
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE recruiter_reports ADD COLUMN point_refs jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE recruiter_reports DROP COLUMN point_refs")
