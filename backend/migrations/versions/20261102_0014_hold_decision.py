"""Recruiter decision HOLD (screen spec SC-R09: 합격 / 보류 / 불합격).

HOLD is the recruiter's own "wait" state. The candidate keeps seeing "검토 중": results are published only for
PASS and FAIL (app/applications/router.py). From any decided state the recruiter may move to any other; a decision
never goes back to UNDECIDED (decided_at stays set, history keeps every step).

Revision ID: 0014
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

NEW = [("UNDECIDED", "HOLD"), ("HOLD", "PASS"), ("HOLD", "FAIL"), ("PASS", "HOLD"), ("FAIL", "HOLD")]


def _swap_check(table: str, column: str, values: str) -> None:
    """Column-level CHECKs got PostgreSQL's automatic name <table>_<column>_check in migration 0001."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check CHECK ({column} IN ({values}))")


def upgrade() -> None:
    _swap_check("applications", "decision_status", "'UNDECIDED','PASS','FAIL','HOLD'")
    _swap_check("decision_history", "to_status", "'PASS','FAIL','HOLD'")
    rows = ",".join(f"('DECISION','{f}','{t}')" for f, t in NEW)
    op.execute(f"INSERT INTO state_transitions(machine, from_state, to_state) VALUES {rows}")


def downgrade() -> None:
    op.execute("DELETE FROM state_transitions WHERE machine = 'DECISION' AND (from_state = 'HOLD' OR to_state = 'HOLD')")
    _swap_check("applications", "decision_status", "'UNDECIDED','PASS','FAIL'")
    _swap_check("decision_history", "to_status", "'PASS','FAIL'")
