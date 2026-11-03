"""Original coding problem set (difficulty 하/중) loaded from app/coding/problem_set.json.

Companies no longer pick a problem: the server assigns one when a posting turns coding on (postings/service.py).
Re-running is safe: a title that already exists is skipped. Built by scripts/build_coding_problems.py.

Revision ID: 0021
"""
import json
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[2] / "app" / "coding" / "problem_set.json"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE coding_problems ADD COLUMN difficulty text CHECK (difficulty IN ('하', '중', '상'))"))
    conn.execute(text("UPDATE coding_problems SET difficulty = '하' WHERE title = '구간 합 구하기'"))
    for p in json.loads(DATA.read_text(encoding="utf-8")):
        if p.get("existing") or conn.execute(text("SELECT 1 FROM coding_problems WHERE title = :t"), {"t": p["title"]}).first():
            continue
        pid = conn.execute(text("INSERT INTO coding_problems(title, statement_md, time_limit_ms, memory_mb, difficulty) "
                                "VALUES (:t, :s, :ms, :mb, :d) RETURNING id"),
                           {"t": p["title"], "s": p["statement"], "ms": p["time_limit_ms"], "mb": p["memory_mb"],
                            "d": p["difficulty"]}).scalar_one()
        for n, c in enumerate(p["cases"], start=1):
            conn.execute(text("INSERT INTO coding_testcases(problem_id, ordinal, visibility, input, expected_output) "
                              "VALUES (:p, :n, :v, :i, :o)"),
                         {"p": pid, "n": n, "v": c["visibility"], "i": c["input"], "o": c["output"]})


def downgrade() -> None:
    conn = op.get_bind()
    for p in json.loads(DATA.read_text(encoding="utf-8")):
        if p.get("existing"):
            continue
        # Only problems no posting or attempt uses can go; the others stay (no data loss).
        conn.execute(text("DELETE FROM coding_testcases WHERE problem_id IN (SELECT id FROM coding_problems WHERE title = :t "
                          "AND id NOT IN (SELECT problem_id FROM coding_attempts) "
                          "AND id NOT IN (SELECT coding_problem_id FROM job_postings WHERE coding_problem_id IS NOT NULL))"), {"t": p["title"]})
        conn.execute(text("DELETE FROM coding_problems WHERE title = :t AND id NOT IN (SELECT problem_id FROM coding_testcases) "
                          "AND id NOT IN (SELECT problem_id FROM coding_attempts) "
                          "AND id NOT IN (SELECT coding_problem_id FROM job_postings WHERE coding_problem_id IS NOT NULL)"), {"t": p["title"]})
    conn.execute(text("ALTER TABLE coding_problems DROP COLUMN difficulty"))
