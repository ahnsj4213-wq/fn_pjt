"""answer_evaluations: evidence by sentence number and the four evidence states (2026-11-09, ADR-0023).

- UNLINKED (근거 미연결): the model scored 3 or more but named no sentence of the answer. The model's score stays and no quote is
  stored; before this a failed citation turned the criterion into 1 point.
- INSUFFICIENT / LOW_CONTENT (내용 부족): the model scored 1~2 and found nothing in the answer to point at. Its score (1 or 2) stays.
- TOO_SHORT, NO_ANSWER: unchanged rules before the model, 1 point. QUOTE_MISMATCH and NO_EVIDENCE stay valid for older runs.

Revision ID: 0033
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE answer_evaluations
          DROP CONSTRAINT answer_evaluations_evidence_status_check,
          DROP CONSTRAINT answer_evaluations_insufficient_reason_check,
          DROP CONSTRAINT answer_evaluations_check1,
          ADD CONSTRAINT answer_evaluations_evidence_status_check
            CHECK (evidence_status IN ('OK','UNLINKED','INSUFFICIENT','NO_ANSWER')),
          ADD CONSTRAINT answer_evaluations_insufficient_reason_check
            CHECK (insufficient_reason IN ('TOO_SHORT','LOW_CONTENT','QUOTE_MISMATCH','NO_EVIDENCE')),
          ADD CONSTRAINT answer_evaluations_rule_score_check
            CHECK (evidence_status IN ('OK','UNLINKED') OR insufficient_reason = 'LOW_CONTENT' OR score = 1),
          ADD CONSTRAINT answer_evaluations_low_content_check
            CHECK (insufficient_reason IS DISTINCT FROM 'LOW_CONTENT' OR score <= 2),
          ADD CONSTRAINT answer_evaluations_unlinked_check
            CHECK (evidence_status <> 'UNLINKED' OR quote IS NULL)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE answer_evaluations
          DROP CONSTRAINT answer_evaluations_unlinked_check,
          DROP CONSTRAINT answer_evaluations_low_content_check,
          DROP CONSTRAINT answer_evaluations_rule_score_check,
          DROP CONSTRAINT answer_evaluations_insufficient_reason_check,
          DROP CONSTRAINT answer_evaluations_evidence_status_check,
          ADD CONSTRAINT answer_evaluations_evidence_status_check CHECK (evidence_status IN ('OK','INSUFFICIENT','NO_ANSWER')),
          ADD CONSTRAINT answer_evaluations_insufficient_reason_check
            CHECK (insufficient_reason IN ('TOO_SHORT','QUOTE_MISMATCH','NO_EVIDENCE')),
          ADD CONSTRAINT answer_evaluations_check1 CHECK (evidence_status = 'OK' OR score = 1)
    """)
