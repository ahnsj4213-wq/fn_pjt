"""Company knowledge base for question generation (ADR-0020).

One row is one searchable chunk of a company's project or technology document. `posting_id` NULL means the whole company;
otherwise only that posting may read it. The composite foreign key keeps a chunk from naming another company's posting.
No HNSW index: every search is limited to one company, an approximate index filters after the scan and can lose that
company's rows, and a company's chunks are few enough for an exact scan.

Revision ID: 0028
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE job_postings ADD CONSTRAINT ux_job_postings_id_company UNIQUE (id, company_id)")
    op.execute("""
        CREATE TABLE company_knowledge (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          company_id uuid NOT NULL REFERENCES companies(id),
          posting_id uuid,
          doc_id uuid NOT NULL,
          chunk_no smallint NOT NULL DEFAULT 1 CHECK (chunk_no >= 1),
          doc_type text NOT NULL CHECK (doc_type IN ('PROJECT','TECH')),
          title text NOT NULL,
          text text NOT NULL CHECK (length(text) BETWEEN 1 AND 1000),
          origin text NOT NULL CHECK (origin IN ('RECRUITER','SYNTHETIC')),
          embedding vector(1024),
          embedding_model text,
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (doc_id, chunk_no),
          FOREIGN KEY (posting_id, company_id) REFERENCES job_postings(id, company_id)
        )
    """)
    op.execute("CREATE INDEX ix_company_knowledge_scope ON company_knowledge (company_id, posting_id)")


def downgrade() -> None:
    op.execute("DROP TABLE company_knowledge")
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT ux_job_postings_id_company")
