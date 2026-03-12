"""Initial schema — conversations, messages, feedback.

Revision ID: 001
Revises:
Create Date: 2026-03-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_active_agent TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations (user_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT NOT NULL,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content         TEXT,
            agent           TEXT,
            response        JSONB,
            attachments     JSONB NOT NULL DEFAULT '[]',
            timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ordinal         INTEGER NOT NULL,
            PRIMARY KEY (conversation_id, id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation_ordinal "
        "ON messages (conversation_id, ordinal)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id              SERIAL PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            message_id      TEXT NOT NULL,
            rating          TEXT NOT NULL CHECK (rating IN ('positive', 'negative')),
            comment         TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_conversation ON feedback (conversation_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feedback")
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversations")
