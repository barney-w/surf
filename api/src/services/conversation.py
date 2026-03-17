import json
import logging
import re
import uuid
from datetime import UTC, datetime

import asyncpg

from src.config.settings import Settings
from src.models.conversation import (
    ConversationDocument,
    ConversationMetadata,
    FeedbackRecord,
    MessageRecord,
)

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ConversationService:
    """PostgreSQL-backed conversation persistence."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """Create connection pool and run migrations."""
        ssl_ctx = "require" if self._settings.postgres_ssl else None
        self._pool = await asyncpg.create_pool(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            database=self._settings.postgres_database,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            ssl=ssl_ctx,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
        logger.info("PostgreSQL connection pool created")

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()

    def _get_pool(self) -> asyncpg.Pool:
        """Return the connection pool, raising if not initialised."""
        if self._pool is None:
            raise RuntimeError("ConversationService not initialised. Call initialize() first.")
        return self._pool

    async def health_check(self) -> bool:
        """Check database connectivity."""
        try:
            async with self._get_pool().acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> ConversationDocument | None:
        """Load a conversation by ID. Returns None if not found or user mismatch."""
        if not _UUID_RE.match(conversation_id):
            return None

        async with self._get_pool().acquire() as conn:
            conv_row = await conn.fetchrow(
                "SELECT id, user_id, created_at, updated_at, last_active_agent "
                "FROM conversations WHERE id = $1 AND user_id = $2",
                uuid.UUID(conversation_id),
                user_id,
            )
            if conv_row is None:
                return None

            msg_rows = await conn.fetch(
                "SELECT id, role, content, agent, response, attachments, timestamp, ordinal "
                "FROM messages WHERE conversation_id = $1 ORDER BY ordinal",
                uuid.UUID(conversation_id),
            )

            feedback_rows = await conn.fetch(
                "SELECT message_id, rating, comment FROM feedback WHERE conversation_id = $1",
                uuid.UUID(conversation_id),
            )

        messages = []
        for row in msg_rows:
            response_data = None
            if row["response"]:
                raw_resp = row["response"]
                response_data = json.loads(raw_resp) if isinstance(raw_resp, str) else raw_resp
            raw_att = row["attachments"]
            attachments_data = json.loads(raw_att) if isinstance(raw_att, str) else raw_att
            messages.append(
                MessageRecord(
                    id=row["id"],
                    role=row["role"],
                    content=row["content"],
                    agent=row["agent"],
                    response=response_data,
                    attachments=attachments_data,
                    timestamp=row["timestamp"],
                )
            )

        feedback_list = [
            FeedbackRecord(
                message_id=row["message_id"],
                rating=row["rating"],
                comment=row["comment"],
            )
            for row in feedback_rows
        ]

        return ConversationDocument(
            id=str(conv_row["id"]),
            user_id=conv_row["user_id"],
            created_at=conv_row["created_at"],
            updated_at=conv_row["updated_at"],
            messages=messages,
            metadata=ConversationMetadata(
                last_active_agent=conv_row["last_active_agent"],
                message_count=len(messages),
                feedback=feedback_list,
            ),
        )

    async def create_conversation(self, user_id: str) -> ConversationDocument:
        """Create a new empty conversation."""
        now = datetime.now(UTC)
        conv_id = uuid.uuid4()

        async with self._get_pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4)",
                conv_id,
                user_id,
                now,
                now,
            )

        return ConversationDocument(
            id=str(conv_id),
            user_id=user_id,
            created_at=now,
            updated_at=now,
            messages=[],
            metadata=ConversationMetadata(),
        )

    async def add_message(self, conversation_id: str, user_id: str, message: MessageRecord) -> None:
        """Append a message to an existing conversation."""
        conv_uuid = uuid.UUID(conversation_id)
        now = datetime.now(UTC)

        async with self._get_pool().acquire() as conn:  # noqa: SIM117
            async with conn.transaction():
                # Verify ownership
                owner = await conn.fetchval(
                    "SELECT user_id FROM conversations WHERE id = $1 FOR UPDATE",
                    conv_uuid,
                )
                if owner != user_id:
                    raise ValueError("Conversation not found or access denied")

                # Get next ordinal
                max_ordinal = await conn.fetchval(
                    "SELECT COALESCE(MAX(ordinal), 0) FROM messages WHERE conversation_id = $1",
                    conv_uuid,
                )

                response_json = None
                if message.response:
                    response_json = json.dumps(message.response.model_dump(mode="json"))

                attachments_json = json.dumps(
                    [a.model_dump(mode="json") for a in message.attachments]
                )

                await conn.execute(
                    "INSERT INTO messages (id, conversation_id, role, content, agent, "
                    "response, attachments, timestamp, ordinal) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    message.id,
                    conv_uuid,
                    message.role,
                    message.content,
                    message.agent,
                    response_json,
                    attachments_json,
                    message.timestamp,
                    max_ordinal + 1,
                )

                await conn.execute(
                    "UPDATE conversations SET updated_at = $1 WHERE id = $2",
                    now,
                    conv_uuid,
                )

    async def update_last_active_agent(
        self, conversation_id: str, user_id: str, agent_name: str
    ) -> None:
        """Update the last_active_agent field on a conversation."""
        conv_uuid = uuid.UUID(conversation_id)
        now = datetime.now(UTC)

        async with self._get_pool().acquire() as conn:
            result = await conn.execute(
                "UPDATE conversations SET last_active_agent = $1, updated_at = $2 "
                "WHERE id = $3 AND user_id = $4",
                agent_name,
                now,
                conv_uuid,
                user_id,
            )
            if result == "UPDATE 0":
                raise ValueError("Conversation not found or access denied")

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        """Delete a conversation. Returns True if deleted, False if not found."""
        if not _UUID_RE.match(conversation_id):
            return False

        async with self._get_pool().acquire() as conn:
            result = await conn.execute(
                "DELETE FROM conversations WHERE id = $1 AND user_id = $2",
                uuid.UUID(conversation_id),
                user_id,
            )
            return result == "DELETE 1"

    async def cleanup_expired_conversations(self, ttl_days: int) -> int:
        """Delete conversations older than ttl_days. Returns count deleted."""
        async with self._get_pool().acquire() as conn:
            result = await conn.execute(
                "DELETE FROM conversations WHERE updated_at < now() - make_interval(days => $1)",
                ttl_days,
            )
            # result is like "DELETE 42"
            count = int(result.split()[-1])
            if count > 0:
                logger.info(
                    "Cleaned up %d expired conversations (ttl=%d days)", count, ttl_days
                )
            return count

    async def add_feedback(
        self, conversation_id: str, user_id: str, feedback: FeedbackRecord
    ) -> None:
        """Add feedback for a specific message in a conversation."""
        conv_uuid = uuid.UUID(conversation_id)

        async with self._get_pool().acquire() as conn:  # noqa: SIM117
            async with conn.transaction():
                # Verify ownership
                owner = await conn.fetchval(
                    "SELECT user_id FROM conversations WHERE id = $1",
                    conv_uuid,
                )
                if owner != user_id:
                    raise ValueError("Conversation not found or access denied")

                await conn.execute(
                    "INSERT INTO feedback (conversation_id, message_id, rating, comment) "
                    "VALUES ($1, $2, $3, $4)",
                    conv_uuid,
                    feedback.message_id,
                    feedback.rating,
                    feedback.comment,
                )
