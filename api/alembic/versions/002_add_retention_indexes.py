"""Add indexes for conversation retention and query performance.

Revision ID: 002
Revises: 001
Create Date: 2026-03-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002"
down_revision: str = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_user_updated "
        "ON conversations (user_id, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_conversations_user_updated")
