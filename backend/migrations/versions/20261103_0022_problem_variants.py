"""Coding problem templates and per-application variants.

A template row (variant_of IS NULL) carries the statement and PUBLIC examples only. When an application reaches the coding
test, coding/variants.py creates a variant row (variant_of = template, variant_for = application) with its own generated
HIDDEN cases, so no hidden test is stored in the repository or shared between applicants. The HIDDEN cases the earlier
seeds put on the templates are removed here.

Revision ID: 0022
"""
from alembic import op
from sqlalchemy import text

from app.coding.templates import TEMPLATES

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for stmt in (
        "ALTER TABLE coding_problems ADD COLUMN template_key text",
        "ALTER TABLE coding_problems ADD COLUMN variant_of uuid REFERENCES coding_problems(id)",
        "ALTER TABLE coding_problems ADD COLUMN variant_for uuid",
        "ALTER TABLE coding_problems ADD COLUMN variant_seed bigint",
        "CREATE UNIQUE INDEX uq_coding_problem_variant_for ON coding_problems(variant_for) WHERE variant_for IS NOT NULL",
        "ALTER TABLE coding_problems ADD CONSTRAINT ck_variant_cols CHECK ((variant_of IS NULL) = (variant_for IS NULL))",
    ):
        conn.execute(text(stmt))
    for t in TEMPLATES.values():
        row = conn.execute(text("SELECT id FROM coding_problems WHERE title = :t AND variant_of IS NULL"), {"t": t.title}).first()
        if row:
            pid = row[0]
            conn.execute(text("UPDATE coding_problems SET template_key = :k, difficulty = :d WHERE id = :p"),
                         {"k": t.key, "d": t.difficulty, "p": pid})
        else:
            pid = conn.execute(text("INSERT INTO coding_problems(title, statement_md, time_limit_ms, memory_mb, difficulty, template_key) "
                                    "VALUES (:t, :s, :ms, :mb, :d, :k) RETURNING id"),
                               {"t": t.title, "s": t.statement, "ms": t.time_limit_ms, "mb": t.memory_mb,
                                "d": t.difficulty, "k": t.key}).scalar_one()
        has_public = conn.execute(text("SELECT 1 FROM coding_testcases WHERE problem_id = :p AND visibility = 'PUBLIC'"), {"p": pid}).first()
        if not has_public:
            for n, inp in enumerate(t.public, start=1):
                conn.execute(text("INSERT INTO coding_testcases(problem_id, ordinal, visibility, input, expected_output) "
                                  "VALUES (:p, :n, 'PUBLIC', :i, :o)"), {"p": pid, "n": n, "i": inp, "o": t.solve(inp)})
    # Templates keep no hidden tests. Cases that an earlier grading already points at stay (the recruiter's result view
    # reads them); nothing that holds a result is deleted.
    conn.execute(text("DELETE FROM coding_testcases WHERE visibility = 'HIDDEN' AND problem_id IN "
                      "(SELECT id FROM coding_problems WHERE variant_of IS NULL) "
                      "AND id NOT IN (SELECT testcase_id FROM coding_test_results)"))


def downgrade() -> None:
    # Variant rows stay as ordinary inactive problems (attempts and results point at them); only the columns go.
    for stmt in (
        "ALTER TABLE coding_problems DROP CONSTRAINT ck_variant_cols",
        "DROP INDEX uq_coding_problem_variant_for",
        "ALTER TABLE coding_problems DROP COLUMN variant_seed, DROP COLUMN variant_for, DROP COLUMN variant_of, DROP COLUMN template_key",
    ):
        op.execute(stmt)
