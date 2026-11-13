"""Retire the question bank (2026-11-13, ADR-0031): the table is renamed, kept as a backup, and read or written by nothing.

question_bank -> question_bank_backup_20261113
- The rows stay exactly as they were (seed questions and AI questions stored before today), with their indexes.
- interview_questions.bank_question_id keeps pointing at them (PostgreSQL moves the foreign key with the table), so question
  sets composed earlier still show where a question came from.
- No code reads or writes the table: questions are made from the company's documents, the posting and the resume, and what
  generation cannot make is made by code from the posting or the resume (source TEMPLATE, ADR-0029 era); nothing is stored
  for reuse any more. Downgrade renames the table back; the code of this revision would still not use it.

Revision ID: 0036
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

BACKUP = "question_bank_backup_20261113"


def upgrade() -> None:
    op.execute(f"ALTER TABLE question_bank RENAME TO {BACKUP}")
    op.execute(f"COMMENT ON TABLE {BACKUP} IS 'Retired question bank (ADR-0031, 2026-11-13): backup only, not used by the application'")


def downgrade() -> None:
    op.execute(f"COMMENT ON TABLE {BACKUP} IS NULL")
    op.execute(f"ALTER TABLE {BACKUP} RENAME TO question_bank")
