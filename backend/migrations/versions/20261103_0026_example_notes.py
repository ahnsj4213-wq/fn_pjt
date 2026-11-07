"""Plain-language explanations of the public coding examples (shown under each example on the candidate screens).

`coding_testcases.note` is set for PUBLIC cases only. Templates and the variants already made are filled from the
template's notes by their order (the public cases come first in both).

Revision ID: 0026
"""
from alembic import op
from sqlalchemy import text

from app.coding.templates import TEMPLATES

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE coding_testcases ADD COLUMN note text"))
    for t in TEMPLATES.values():
        for n, note in enumerate(t.notes, start=1):
            conn.execute(text(
                "UPDATE coding_testcases c SET note = :note FROM coding_problems p WHERE p.id = c.problem_id "
                "AND p.template_key = :k AND c.visibility = 'PUBLIC' AND c.ordinal = :n"), {"note": note, "k": t.key, "n": n})


def downgrade() -> None:
    op.execute("ALTER TABLE coding_testcases DROP COLUMN note")
