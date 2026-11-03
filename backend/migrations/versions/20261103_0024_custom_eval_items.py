"""Company-added evaluation items (screen spec SC-R07).

A posting's evaluation settings may carry up to five items of its own next to the four fixed areas: a name, a one-line
description and a 상/중/하 scoring criterion each, with a weight like any other area. `custom_items` holds them per
settings version (insert-only like the rest of the row); `w_custom` is the sum of their weights so the database still
checks that all weights add up to 100.

Revision ID: 0024
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for stmt in (
        "ALTER TABLE posting_eval_settings ADD COLUMN custom_items jsonb NOT NULL DEFAULT '[]'",
        "ALTER TABLE posting_eval_settings ADD COLUMN w_custom smallint NOT NULL DEFAULT 0 CHECK (w_custom BETWEEN 0 AND 100)",
        "ALTER TABLE posting_eval_settings DROP CONSTRAINT posting_eval_settings_check",
        "ALTER TABLE posting_eval_settings ADD CONSTRAINT posting_eval_settings_check CHECK "
        "(w_expertise + w_logic + w_attitude + w_delivery + COALESCE(w_coding, 0) + w_custom = 100)",
        "ALTER TABLE posting_eval_settings ADD CONSTRAINT posting_eval_settings_custom_check CHECK "
        "(jsonb_typeof(custom_items) = 'array' AND jsonb_array_length(custom_items) <= 5)",
    ):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in (
        "ALTER TABLE posting_eval_settings DROP CONSTRAINT posting_eval_settings_custom_check",
        "ALTER TABLE posting_eval_settings DROP CONSTRAINT posting_eval_settings_check",
        "ALTER TABLE posting_eval_settings ADD CONSTRAINT posting_eval_settings_check CHECK "
        "(w_expertise + w_logic + w_attitude + w_delivery + COALESCE(w_coding, 0) = 100)",
        "ALTER TABLE posting_eval_settings DROP COLUMN w_custom",
        "ALTER TABLE posting_eval_settings DROP COLUMN custom_items",
    ):
        op.execute(stmt)
