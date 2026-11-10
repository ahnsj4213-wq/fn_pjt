"""interview_questions.source: TEMPLATE (2026-11-10).

Questions of the common areas and the ones a failed generation used to take from the question bank are made by code from the
resume or the posting (questions/compose.py). BANK and BANK_FALLBACK stay valid for the question sets already written.

Revision ID: 0034
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE interview_questions DROP CONSTRAINT iq_source")
    op.execute("""ALTER TABLE interview_questions ADD CONSTRAINT iq_source
                  CHECK (source IN ('BANK','AI','CUSTOM','BANK_FALLBACK','PROBLEM','TEMPLATE'))""")


def downgrade() -> None:
    op.execute("UPDATE interview_questions SET source = 'BANK_FALLBACK' WHERE source = 'TEMPLATE'")
    op.execute("ALTER TABLE interview_questions DROP CONSTRAINT iq_source")
    op.execute("""ALTER TABLE interview_questions ADD CONSTRAINT iq_source
                  CHECK (source IN ('BANK','AI','CUSTOM','BANK_FALLBACK','PROBLEM'))""")
