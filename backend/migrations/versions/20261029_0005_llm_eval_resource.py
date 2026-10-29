"""Jobs resource LLM_EVAL: BATCH evaluation jobs wait for the CPU evaluation LLM (llm-eval) separately from the
INTERACTIVE jobs on llm-rt (ADR-0014).
Revision ID: 0005
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_resource_check")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT jobs_resource_check "
               "CHECK (resource IN ('LLM','LLM_EVAL','CPU','RUNNER','NONE'))")


def downgrade() -> None:
    op.execute("UPDATE jobs SET resource = 'LLM' WHERE resource = 'LLM_EVAL'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_resource_check")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT jobs_resource_check CHECK (resource IN ('LLM','CPU','RUNNER','NONE'))")
