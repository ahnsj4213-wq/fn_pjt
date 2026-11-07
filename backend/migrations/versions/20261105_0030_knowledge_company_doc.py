"""A third kind of company document: what the company is (ADR-0020), for posting drafts and motivation questions.

Revision ID: 0030
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_knowledge DROP CONSTRAINT company_knowledge_doc_type_check")
    op.execute("ALTER TABLE company_knowledge ADD CONSTRAINT company_knowledge_doc_type_check CHECK (doc_type IN ('PROJECT','TECH','COMPANY'))")


def downgrade() -> None:
    op.execute("DELETE FROM company_knowledge WHERE doc_type = 'COMPANY'")
    op.execute("ALTER TABLE company_knowledge DROP CONSTRAINT company_knowledge_doc_type_check")
    op.execute("ALTER TABLE company_knowledge ADD CONSTRAINT company_knowledge_doc_type_check CHECK (doc_type IN ('PROJECT','TECH'))")
