"""Test postings built from companies' public tech blogs, and archiving of the old test postings (2026-11-13, ADR-0029).

job_postings
- is_virtual: a test posting written from a company's public tech blog. It is NOT a posting the company published; the screens say
  so. Only a virtual posting may be open without a deadline (no invented closing date): the publish check below.
- source_doc_ids: the blog documents (company_knowledge.doc_id) the posting was written from, shown as its sources.
- hidden_by ARCHIVE + close_reason ARCHIVED: an old test posting taken off every applicant screen after a backup. Rows and
  everything that refers to them (applications, answers, evaluations) stay; the operator's "show" cannot bring one back.

company_knowledge
- origin TECH_BLOG: a chunk of a company's public tech blog post; source_url (required for it) and published_at (null when the
  blog does not say) keep the provenance. Another company's search never returns these rows (questions/knowledge.py).

Revision ID: 0035
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE job_postings
                  ADD COLUMN is_virtual boolean NOT NULL DEFAULT false,
                  ADD COLUMN source_doc_ids uuid[] NOT NULL DEFAULT '{}'""")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_check")
    op.execute("""ALTER TABLE job_postings ADD CONSTRAINT job_postings_check CHECK (status = 'DRAFT' OR (
                    title IS NOT NULL AND job_family IS NOT NULL AND current_settings_id IS NOT NULL
                    AND (deadline_at IS NOT NULL OR is_virtual)))""")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_hidden_by_check")
    op.execute("ALTER TABLE job_postings ADD CONSTRAINT job_postings_hidden_by_check CHECK (hidden_by IN ('OPERATOR','SUSPENSION','ARCHIVE'))")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_close_reason_check")
    op.execute("ALTER TABLE job_postings ADD CONSTRAINT job_postings_close_reason_check CHECK (close_reason IN ('MANUAL','DEADLINE','ARCHIVED'))")

    op.execute("""ALTER TABLE company_knowledge
                  ADD COLUMN source_url text,
                  ADD COLUMN published_at timestamptz""")
    op.execute("ALTER TABLE company_knowledge DROP CONSTRAINT company_knowledge_origin_check")
    op.execute("ALTER TABLE company_knowledge ADD CONSTRAINT company_knowledge_origin_check CHECK (origin IN ('RECRUITER','SYNTHETIC','TECH_BLOG'))")
    op.execute("""ALTER TABLE company_knowledge ADD CONSTRAINT company_knowledge_source_ck
                  CHECK (origin <> 'TECH_BLOG' OR (source_url IS NOT NULL AND source_url LIKE 'https://%'))""")


def downgrade() -> None:
    # Fails on purpose while tech blog rows or virtual postings without a deadline exist: remove them first.
    op.execute("ALTER TABLE company_knowledge DROP CONSTRAINT company_knowledge_source_ck")
    op.execute("ALTER TABLE company_knowledge DROP CONSTRAINT company_knowledge_origin_check")
    op.execute("ALTER TABLE company_knowledge ADD CONSTRAINT company_knowledge_origin_check CHECK (origin IN ('RECRUITER','SYNTHETIC'))")
    op.execute("ALTER TABLE company_knowledge DROP COLUMN published_at, DROP COLUMN source_url")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_close_reason_check")
    op.execute("ALTER TABLE job_postings ADD CONSTRAINT job_postings_close_reason_check CHECK (close_reason IN ('MANUAL','DEADLINE'))")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_hidden_by_check")
    op.execute("ALTER TABLE job_postings ADD CONSTRAINT job_postings_hidden_by_check CHECK (hidden_by IN ('OPERATOR','SUSPENSION'))")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT job_postings_check")
    op.execute("""ALTER TABLE job_postings ADD CONSTRAINT job_postings_check CHECK (status = 'DRAFT' OR (
                    title IS NOT NULL AND job_family IS NOT NULL AND deadline_at IS NOT NULL AND current_settings_id IS NOT NULL))""")
    op.execute("ALTER TABLE job_postings DROP COLUMN source_doc_ids, DROP COLUMN is_virtual")
