"""Operator moderation (screen spec SC-O02): hide a posting, suspend a company.

- job_postings.hidden_at / hidden_by: a hidden posting leaves the main page and takes no new applications; applicants who
  already applied keep their access. hidden_by is OPERATOR (hidden by hand) or SUSPENSION (hidden because the company
  was suspended), so lifting a suspension restores only what the suspension hid.
- companies.suspended_at: a suspended company cannot publish; its published postings are hidden.

Revision ID: 0015
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE job_postings ADD COLUMN hidden_at timestamptz,
                  ADD COLUMN hidden_by text CHECK (hidden_by IN ('OPERATOR','SUSPENSION')),
                  ADD CONSTRAINT job_postings_hidden_ck CHECK ((hidden_at IS NULL) = (hidden_by IS NULL))""")
    op.execute("ALTER TABLE companies ADD COLUMN suspended_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN suspended_at")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_hidden_ck, DROP COLUMN hidden_by, DROP COLUMN hidden_at")
