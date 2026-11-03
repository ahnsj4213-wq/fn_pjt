"""RAG example corpus for question generation (ai-architecture 6.1, TRD 7-4 ③).

question_examples holds reference interview questions (the team's spreadsheets, converted to
app/questions/data/question_examples.jsonl). They are shown to T2/T3 as tone/level examples and are never asked
as they are, so they live apart from question_bank (whose rows are picked into interviews). Rows are loaded and
embedded by the `index_question_examples` job, not here: embeddings need the AI service.

Revision ID: 0010
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE question_examples (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          kind text NOT NULL CHECK (kind IN ('PERSONALITY','COLLABORATION','COMPANY','JOB','RESUME')),
          field text,
          text text NOT NULL,
          text_norm_hash bytea NOT NULL,
          origin text,
          embedding vector(1024),
          embedding_model text,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (kind, text_norm_hash)
        )
    """)
    op.execute("CREATE INDEX ix_qexamples_embedding ON question_examples USING hnsw (embedding vector_cosine_ops) "
               "WHERE embedding IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE question_examples")
