from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .agent import AgentResponseModel


class AttachmentRecord(BaseModel):
    filename: str
    content_type: str
    size: int


class MessageRecord(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str | None = None
    agent: str | None = None
    response: AgentResponseModel | None = None
    attachments: list[AttachmentRecord] = []
    timestamp: datetime


class FeedbackRecord(BaseModel):
    message_id: str
    rating: Literal["positive", "negative"]
    comment: str | None = None


class ConversationMetadata(BaseModel):
    last_active_agent: str | None = None
    message_count: int = 0
    feedback: list[FeedbackRecord] = []


class ConversationSummary(BaseModel):
    """Lightweight summary of a conversation for list views."""

    id: str
    title: str  # First user message truncated to 80 chars
    last_message_preview: str | None = None  # Last message truncated to 120 chars
    updated_at: datetime
    last_active_agent: str | None = None
    message_count: int = 0


class ConversationDocument(BaseModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRecord] = []
    metadata: ConversationMetadata
