from datetime import datetime

from pydantic import BaseModel, Field

from .agent import AgentResponseModel, RoutingMetadata


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=10000)

class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    agent: str
    response: AgentResponseModel
    routing: RoutingMetadata
    created_at: datetime
