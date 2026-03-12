import base64
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .agent import AgentResponseModel, RoutingMetadata

ALLOWED_ATTACHMENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
    }
)

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_ATTACHMENTS = 5


class Attachment(BaseModel):
    filename: str = Field(max_length=255)
    content_type: str
    data: str  # base64-encoded file content

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        if v not in ALLOWED_ATTACHMENT_TYPES:
            allowed = ", ".join(sorted(ALLOWED_ATTACHMENT_TYPES))
            raise ValueError(f"Unsupported file type: {v}. Allowed: {allowed}")
        return v

    @field_validator("data")
    @classmethod
    def validate_data_size(cls, v: str) -> str:
        try:
            decoded = base64.b64decode(v, validate=True)
        except Exception as exc:
            raise ValueError("Invalid base64-encoded data") from exc
        if len(decoded) > MAX_ATTACHMENT_SIZE:
            limit_mb = MAX_ATTACHMENT_SIZE // (1024 * 1024)
            raise ValueError(f"File exceeds {limit_mb}MB limit")
        return v


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=10000)
    attachments: list[Attachment] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    @field_validator("attachments")
    @classmethod
    def validate_attachment_count(cls, v: list[Attachment]) -> list[Attachment]:
        if len(v) > MAX_ATTACHMENTS:
            raise ValueError(f"Too many attachments: {len(v)} (max {MAX_ATTACHMENTS})")
        return v


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    agent: str
    response: AgentResponseModel
    routing: RoutingMetadata
    created_at: datetime
