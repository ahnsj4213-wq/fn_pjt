"""pgvector: question-bank embeddings become `vector(1024)` (BGE-M3) with an HNSW cosine index, and the near-duplicate
check runs in SQL instead of in Python (QST-01). Needs the pgvector extension (image pgvector/pgvector:pg16).
Embeddings of another size are dropped; they are only a duplicate filter and are recomputed for new questions.
Revision ID: 0004
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE question_bank ALTER COLUMN embedding TYPE vector(1024) USING "
               "CASE WHEN array_length(embedding, 1) = 1024 THEN embedding::vector(1024) END")
    op.execute("CREATE INDEX ix_qbank_embedding ON question_bank USING hnsw (embedding vector_cosine_ops) "
               "WHERE embedding IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_qbank_embedding")
    op.execute("ALTER TABLE question_bank ALTER COLUMN embedding TYPE real[] USING embedding::real[]")
