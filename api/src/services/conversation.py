import uuid
from datetime import UTC, datetime

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential

from src.config.settings import Settings
from src.models.conversation import (
    ConversationDocument,
    ConversationMetadata,
    FeedbackRecord,
    MessageRecord,
)


class ConversationService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: CosmosClient | None = None
        self._container = None

    async def initialize(self) -> None:
        """Initialize Cosmos client. Called during app startup."""
        credential = DefaultAzureCredential()
        self._client = CosmosClient(self._settings.cosmos_endpoint, credential=credential)
        database = self._client.get_database_client(self._settings.cosmos_database_name)
        self._container = database.get_container_client(self._settings.cosmos_container_name)

    async def close(self) -> None:
        """Close Cosmos client. Called during app shutdown."""
        if self._client:
            await self._client.close()

    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> ConversationDocument | None:
        """Load a conversation by ID. Returns None if not found."""
        try:
            item = await self._container.read_item(
                item=conversation_id, partition_key=user_id
            )
            return ConversationDocument(**item)
        except CosmosResourceNotFoundError:
            return None

    async def create_conversation(self, user_id: str) -> ConversationDocument:
        """Create a new empty conversation."""
        now = datetime.now(UTC)
        doc = ConversationDocument(
            id=str(uuid.uuid4()),
            user_id=user_id,
            created_at=now,
            updated_at=now,
            messages=[],
            metadata=ConversationMetadata(),
        )
        await self._container.create_item(body=doc.model_dump(mode="json"))
        return doc

    async def add_message(
        self, conversation_id: str, user_id: str, message: MessageRecord
    ) -> None:
        """Append a message to an existing conversation."""
        now = datetime.now(UTC)
        operations = [
            {"op": "add", "path": "/messages/-", "value": message.model_dump(mode="json")},
            {"op": "incr", "path": "/metadata/message_count", "value": 1},
            {"op": "set", "path": "/updated_at", "value": now.isoformat()},
        ]
        await self._container.patch_item(
            item=conversation_id, partition_key=user_id, patch_operations=operations
        )

    async def update_last_active_agent(
        self, conversation_id: str, user_id: str, agent_name: str
    ) -> None:
        """Update the last_active_agent field on conversation metadata."""
        now = datetime.now(UTC)
        operations = [
            {"op": "set", "path": "/metadata/last_active_agent", "value": agent_name},
            {"op": "set", "path": "/updated_at", "value": now.isoformat()},
        ]
        await self._container.patch_item(
            item=conversation_id, partition_key=user_id, patch_operations=operations
        )

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        """Delete a conversation. Returns True if deleted, False if not found."""
        try:
            await self._container.delete_item(
                item=conversation_id, partition_key=user_id
            )
            return True
        except CosmosResourceNotFoundError:
            return False

    async def add_feedback(
        self, conversation_id: str, user_id: str, feedback: FeedbackRecord
    ) -> None:
        """Add feedback for a specific message in a conversation."""
        now = datetime.now(UTC)
        operations = [
            {
                "op": "add",
                "path": "/metadata/feedback/-",
                "value": feedback.model_dump(mode="json"),
            },
            {"op": "set", "path": "/updated_at", "value": now.isoformat()},
        ]
        await self._container.patch_item(
            item=conversation_id, partition_key=user_id, patch_operations=operations
        )
