"""Company notification inbox (screen spec SC-R12): new interview finished or stopped, applicant answer to a mailed
date, a posting closing tomorrow.

One row per event, shared by the recruiters of the company (read state is the company's). `dedupe_key` makes every event
appear once (a posting's deadline alert is keyed by its deadline date, so an extended posting alerts again).

Revision ID: 0017
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notifications (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
          kind text NOT NULL CHECK (kind IN ('DEADLINE','APPLY','SCHEDULE')),
          text text NOT NULL CHECK (length(text) <= 300),
          dedupe_key text NOT NULL,
          application_id uuid REFERENCES applications(id) ON DELETE CASCADE,
          posting_id uuid REFERENCES job_postings(id) ON DELETE CASCADE,
          done_text text CHECK (length(done_text) <= 200),
          read_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (company_id, dedupe_key)
        )
    """)
    op.execute("CREATE INDEX ix_notifications_company ON notifications(company_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE notifications")
