from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .agent import AgentResponseModel


class MessageRecord(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str | None = None
    agent: str | None = None
    response: AgentResponseModel | None = None
    timestamp: datetime


class FeedbackRecord(BaseModel):
    message_id: str
    rating: Literal["positive", "negative"]
    comment: str | None = None


class ConversationMetadata(BaseModel):
    last_active_agent: str | None = None
    message_count: int = 0
    feedback: list[FeedbackRecord] = []


class ConversationDocument(BaseModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRecord] = []
    metadata: ConversationMetadata
