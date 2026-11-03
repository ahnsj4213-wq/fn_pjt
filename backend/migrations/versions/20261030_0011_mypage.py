"""My Page (screen spec SC-A09~A11, SC-A15).

- interview_schedules: interview dates a company sent by mail (SC-A15), with the candidate's answer.
  ACCEPTED makes the application show "2차 면접 일정 확정" on 지원 현황 (SC-A09). Rows go with the application.
- resume_files.kind accepts DOCX uploads (SC-A10 "PDF, DOCX를 받습니다").

Revision ID: 0011
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE interview_schedules (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
          company_id uuid NOT NULL,
          candidate_id uuid NOT NULL,
          title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
          starts_at timestamptz NOT NULL,
          place text CHECK (length(place) <= 200),
          status text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED','ACCEPTED','RESCHEDULE_REQUESTED')),
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_schedules_candidate ON interview_schedules(candidate_id, starts_at)")


def downgrade() -> None:
    op.execute("DROP TABLE interview_schedules")
