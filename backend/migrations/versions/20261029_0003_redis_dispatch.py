"""Redis dispatch (ADR-0013): when a QUEUED job was last handed to Redis.

The jobs table stays the durable record and the outbox: enqueue still commits with the state change that caused it
(7.2), and the dispatcher re-sends anything Redis may have lost.
Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN dispatched_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP COLUMN dispatched_at")
