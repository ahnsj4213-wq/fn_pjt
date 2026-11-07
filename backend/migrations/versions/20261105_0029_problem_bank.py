"""Problem bank Lv.0~5: level, tags, starter code and source on coding problems; per-application selection (ADR-0021).

Rows are upserted by template_key from the current templates, so a fresh database (where revision 0022 already wrote them all)
and an older one end up the same. Existing postings move to automatic selection, which is what they already were: their problem
was drawn at random by the server. Attempts already started keep the variant they have.

Revision ID: 0029
"""
import json

from alembic import op
from sqlalchemy import text

from app.coding.templates import TEMPLATES

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for stmt in (
        "ALTER TABLE coding_problems ADD COLUMN level smallint CHECK (level BETWEEN 0 AND 5)",
        "ALTER TABLE coding_problems ADD COLUMN tags jsonb NOT NULL DEFAULT '[]'",
        "ALTER TABLE coding_problems ADD COLUMN families jsonb NOT NULL DEFAULT '[]'",
        "ALTER TABLE coding_problems ADD COLUMN starter_code text",
        "ALTER TABLE coding_problems ADD COLUMN source jsonb",
        "CREATE INDEX ix_coding_problems_pool ON coding_problems(level) WHERE is_active AND variant_of IS NULL",
        "ALTER TABLE job_postings ADD COLUMN coding_selection text NOT NULL DEFAULT 'AUTO' CHECK (coding_selection IN ('AUTO', 'FIXED'))",
        "ALTER TABLE job_postings ADD COLUMN coding_difficulty text NOT NULL DEFAULT 'NORMAL' CHECK (coding_difficulty IN ('EASY', 'NORMAL', 'HARD'))",
    ):
        conn.execute(text(stmt))
    for t in TEMPLATES.values():
        row = conn.execute(text("SELECT id FROM coding_problems WHERE template_key = :k AND variant_of IS NULL"), {"k": t.key}).first()
        values = {"k": t.key, "t": t.title, "s": t.statement, "ms": t.time_limit_ms, "mb": t.memory_mb, "d": t.difficulty, "l": t.level,
                  "tags": json.dumps(list(t.tags), ensure_ascii=False), "f": json.dumps(list(t.families)), "c": t.starter,
                  "src": json.dumps(t.source, ensure_ascii=False)}
        if row:
            pid = row[0]
            conn.execute(text("UPDATE coding_problems SET time_limit_ms = :ms, difficulty = :d, level = :l, tags = CAST(:tags AS jsonb), "
                              "families = CAST(:f AS jsonb), starter_code = :c, source = CAST(:src AS jsonb) WHERE id = :p"), {**values, "p": pid})
        else:
            pid = conn.execute(text(
                "INSERT INTO coding_problems(title, statement_md, time_limit_ms, memory_mb, difficulty, template_key, level, tags, families, "
                "starter_code, source) VALUES (:t, :s, :ms, :mb, :d, :k, :l, CAST(:tags AS jsonb), CAST(:f AS jsonb), :c, CAST(:src AS jsonb)) "
                "RETURNING id"), values).scalar_one()
        for n, inp in enumerate(t.public, start=1):
            note = t.notes[n - 1] if n <= len(t.notes) else None
            done = conn.execute(text("SELECT 1 FROM coding_testcases WHERE problem_id = :p AND visibility = 'PUBLIC' AND ordinal = :n"),
                                {"p": pid, "n": n}).first()
            if done:
                conn.execute(text("UPDATE coding_testcases SET note = :note WHERE problem_id = :p AND visibility = 'PUBLIC' AND ordinal = :n"),
                             {"p": pid, "n": n, "note": note})
            else:
                conn.execute(text("INSERT INTO coding_testcases(problem_id, ordinal, visibility, input, expected_output, note) "
                                  "VALUES (:p, :n, 'PUBLIC', :i, :o, :note)"), {"p": pid, "n": n, "i": inp, "o": t.solve(inp), "note": note})


def downgrade() -> None:
    # The new problems stay as rows: applications may point at them. Only the columns go.
    for stmt in (
        "ALTER TABLE job_postings DROP COLUMN coding_difficulty, DROP COLUMN coding_selection",
        "DROP INDEX ix_coding_problems_pool",
        "ALTER TABLE coding_problems DROP COLUMN source, DROP COLUMN starter_code, DROP COLUMN families, DROP COLUMN tags, DROP COLUMN level",
    ):
        op.execute(stmt)
