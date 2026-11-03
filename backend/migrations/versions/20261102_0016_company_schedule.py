"""Company schedule and mail to applicants (screen spec SC-R11, SC-R14).

- interview_schedules also holds the company's own calendar entries: `kind` INTERVIEW / RESULT / INTERNAL. An entry sent
  by mail to an applicant has application_id, candidate_id, status and a response token (only its SHA-256 is kept); an
  internal entry has none of those (all four are NULL together).
- recruiter_mails: every mail a recruiter sends from an applicant's page (2차 면접 안내, 결과 안내, 직접 작성). The job
  that sends it carries only the id; the address is read at send time, like the other mails.

Revision ID: 0016
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE interview_schedules
          ALTER COLUMN application_id DROP NOT NULL,
          ALTER COLUMN candidate_id DROP NOT NULL,
          ALTER COLUMN status DROP NOT NULL,
          ALTER COLUMN status DROP DEFAULT,
          ADD COLUMN kind text NOT NULL DEFAULT 'INTERVIEW' CHECK (kind IN ('INTERVIEW','RESULT','INTERNAL')),
          ADD COLUMN response_token_hash bytea,
          ADD COLUMN created_by uuid REFERENCES users(id) ON DELETE SET NULL,
          ADD CONSTRAINT interview_schedules_mail_ck CHECK (
            (application_id IS NULL) = (candidate_id IS NULL) AND (application_id IS NULL) = (status IS NULL))
    """)
    op.execute("CREATE INDEX ix_schedules_company ON interview_schedules(company_id, starts_at)")
    op.execute("CREATE UNIQUE INDEX ux_schedules_token ON interview_schedules(response_token_hash) WHERE response_token_hash IS NOT NULL")
    op.execute("""
        CREATE TABLE recruiter_mails (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
          company_id uuid NOT NULL,
          sender_id uuid REFERENCES users(id) ON DELETE SET NULL,
          kind text NOT NULL CHECK (kind IN ('INTERVIEW','RESULT','FREE')),
          subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 200),
          body text NOT NULL CHECK (length(body) <= 5000),
          schedule_id uuid REFERENCES interview_schedules(id) ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_recruiter_mails_app ON recruiter_mails(application_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE recruiter_mails")
    op.execute("DROP INDEX ux_schedules_token")
    op.execute("DROP INDEX ix_schedules_company")
    op.execute("""ALTER TABLE interview_schedules DROP CONSTRAINT interview_schedules_mail_ck, DROP COLUMN created_by,
                  DROP COLUMN response_token_hash, DROP COLUMN kind""")
