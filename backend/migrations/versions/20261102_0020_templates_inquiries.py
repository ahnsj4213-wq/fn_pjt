"""Posting templates (screen spec SC-R05) and inquiry messages between a company, applicants and the operator (SC-R15).

A thread belongs to one company. `kind` says who is on the other side: an applicant (`applicant_id`) or the operator.
`company_read_at` against `last_at` gives the company's unread dot; `last_from` says whose message came last.

Revision ID: 0020
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for stmt in """
        CREATE TABLE posting_templates (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
          name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
          job_family text,
          experience_level text,
          employment_type text,
          duties text,
          requirements text,
          preferred text,
          created_by uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_posting_templates_company ON posting_templates (company_id, created_at DESC);

        CREATE TABLE inquiry_threads (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
          kind text NOT NULL CHECK (kind IN ('APPLICANT','OPERATOR')),
          applicant_id uuid REFERENCES users(id) ON DELETE SET NULL,
          applicant_name text,
          posting_id uuid REFERENCES job_postings(id) ON DELETE SET NULL,
          subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 100),
          last_from text NOT NULL CHECK (last_from IN ('COMPANY','OTHER')),
          last_at timestamptz NOT NULL DEFAULT now(),
          company_read_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (kind <> 'APPLICANT' OR applicant_id IS NOT NULL OR applicant_name IS NOT NULL)
        );
        CREATE INDEX ix_inquiry_threads_company ON inquiry_threads (company_id, last_at DESC);

        CREATE TABLE inquiry_messages (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          thread_id uuid NOT NULL REFERENCES inquiry_threads(id) ON DELETE CASCADE,
          from_side text NOT NULL CHECK (from_side IN ('COMPANY','OTHER')),
          sender_label text NOT NULL,
          body text NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_inquiry_messages_thread ON inquiry_messages (thread_id, created_at);
    """.split(";"):
        if stmt.strip().rstrip(";"):
            op.execute(stmt.strip().rstrip(";"))


def downgrade() -> None:
    for t in ("inquiry_messages", "inquiry_threads", "posting_templates"):
        op.execute(f"DROP TABLE {t}")
