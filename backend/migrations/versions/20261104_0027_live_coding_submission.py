"""A live-coding answer is graded like the separate test (ADR-0017): coding_submissions.kind gains LIVE.

Revision ID: 0027
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE coding_submissions DROP CONSTRAINT coding_submissions_kind_check")
    op.execute("ALTER TABLE coding_submissions ADD CONSTRAINT coding_submissions_kind_check "
               "CHECK (kind IN ('MANUAL','AUTO_TIMEOUT','AUTO_EXPIRED','LIVE'))")


def downgrade() -> None:
    op.execute("ALTER TABLE coding_submissions DROP CONSTRAINT coding_submissions_kind_check")
    op.execute("ALTER TABLE coding_submissions ADD CONSTRAINT coding_submissions_kind_check "
               "CHECK (kind IN ('MANUAL','AUTO_TIMEOUT','AUTO_EXPIRED'))")
