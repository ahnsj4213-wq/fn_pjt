"""Evaluation areas follow the screen spec (SC-R07, SC-A08): 논리성, 전달력, 표현 및 태도, 전문성.

The fourth area used to be 구체성·관련성 (SPECIFICITY); it is now 표현 및 태도 (ATTITUDE). Only the names change here:
weights and scores keep their columns (renamed). Answer evaluations written before this keep the old code, which the
CHECK still allows; reports show only the current areas.

Revision ID: 0019
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE posting_eval_settings RENAME COLUMN w_specificity TO w_attitude")
    op.execute("ALTER TABLE recruiter_reports RENAME COLUMN score_specificity TO score_attitude")
    op.execute("ALTER TABLE candidate_feedback RENAME COLUMN score_specificity TO score_attitude")
    op.execute("ALTER TABLE answer_evaluations DROP CONSTRAINT answer_evaluations_criterion_check")
    op.execute("""ALTER TABLE answer_evaluations ADD CONSTRAINT answer_evaluations_criterion_check
                  CHECK (criterion IN ('EXPERTISE','LOGIC','ATTITUDE','DELIVERY','SPECIFICITY'))""")


def downgrade() -> None:
    op.execute("ALTER TABLE answer_evaluations DROP CONSTRAINT answer_evaluations_criterion_check")
    op.execute("""ALTER TABLE answer_evaluations ADD CONSTRAINT answer_evaluations_criterion_check
                  CHECK (criterion IN ('EXPERTISE','LOGIC','SPECIFICITY','DELIVERY'))""")
    op.execute("ALTER TABLE candidate_feedback RENAME COLUMN score_attitude TO score_specificity")
    op.execute("ALTER TABLE recruiter_reports RENAME COLUMN score_attitude TO score_specificity")
    op.execute("ALTER TABLE posting_eval_settings RENAME COLUMN w_attitude TO w_specificity")
