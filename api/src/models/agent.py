from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    section: str | None = None
    document_id: str
    url: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    snippet: str | None = None


class AgentResponseModel(BaseModel):
    """
    Canonical response shape for all domain agents.
    Named AgentResponseModel to avoid collision with agent_framework.AgentResponse.
    """

    model_config = ConfigDict(extra="forbid")

    message: str
    sources: list[Source] = []
    confidence: Literal["high", "medium", "low"]
    ui_hint: Literal["text", "table", "card", "list", "steps", "warning"] = "text"
    # structured_data uses str values only — dict[str, Any] is banned by strict
    # response_format schema requirements (gpt-5.1+). Use str-typed values and
    # deserialise on the client side if richer types are needed.
    structured_data: dict[str, str] | None = None
    follow_up_suggestions: list[str] = []


class RoutingMetadata(BaseModel):
    routed_by: str
    primary_agent: str
    secondary_suggestion: str | None = None


# ---------------------------------------------------------------------------
# Enriched API types — used by the SSE streaming endpoint (/chat/stream).
# These match the surf-kit AgentResponse TypeScript type exactly.
# AgentResponseModel (above) is what agents produce internally; these are
# what the API sends to clients after enrichment.
# ---------------------------------------------------------------------------


class ConfidenceBreakdown(BaseModel):
    overall: Literal["high", "medium", "low"]
    retrieval_quality: float = Field(ge=0.0, le=1.0)
    source_authority: float = Field(ge=0.0, le=1.0)
    answer_groundedness: float = Field(ge=0.0, le=1.0)
    recency: float = Field(ge=0.0, le=1.0)
    reasoning: str


class VerificationResult(BaseModel):
    status: Literal["passed", "flagged", "failed"]
    flags: list[str] = []
    claims_checked: int = Field(ge=0)
    claims_verified: int = Field(ge=0)


class EnrichedAgentResponse(BaseModel):
    """Full response shape emitted in the SSE /chat/stream endpoint."""

    message: str
    sources: list[Source] = []
    confidence: ConfidenceBreakdown
    verification: VerificationResult
    ui_hint: Literal["text", "table", "card", "list", "steps", "warning"] = "text"
    structured_data: dict[str, str] | None = None
    follow_up_suggestions: list[str] = []


def enrich_agent_response(model: AgentResponseModel) -> EnrichedAgentResponse:
    """Derive ConfidenceBreakdown and VerificationResult from an AgentResponseModel.

    These fields are computed from the agent's simple confidence string and source
    list. The breakdown dimensions are approximations — retrieval_quality and
    source_authority are derived from source confidence scores, while
    answer_groundedness maps from the overall confidence level. Recency is a
    fixed conservative value until document date metadata is available.
    """
    sources = model.sources
    level = model.confidence

    if sources:
        avg = sum(s.confidence for s in sources) / len(sources)
        floor = {"high": 0.7, "medium": 0.5, "low": 0.3}[level]
        retrieval_quality = max(avg, floor)
        source_authority = avg
    else:
        defaults = {"high": 0.75, "medium": 0.55, "low": 0.3}
        retrieval_quality = defaults[level]
        source_authority = defaults[level]

    groundedness = {"high": 0.9, "medium": 0.65, "low": 0.35}[level]

    n = len(sources)
    if not sources:
        reasoning = "No source documents found. Response is based on general knowledge."
    elif level == "high":
        s = "s" if n > 1 else ""
        reasoning = f"Answer is well-supported by {n} source document{s} with strong relevance."
    elif level == "medium":
        s = "s" if n > 1 else ""
        reasoning = (
            f"Answer is partially supported by {n} source document{s}."
            " Some details may require verification."
        )
    else:
        reasoning = (
            "Limited source support found."
            " Response may not reflect current organisational policies."
        )

    confidence_breakdown = ConfidenceBreakdown(
        overall=level,
        retrieval_quality=round(retrieval_quality, 2),
        source_authority=round(source_authority, 2),
        answer_groundedness=groundedness,
        recency=0.9,
        reasoning=reasoning,
    )

    n_sources = max(len(sources), 1)
    if level == "high":
        verification = VerificationResult(
            status="passed",
            flags=[],
            claims_checked=n_sources,
            claims_verified=n_sources,
        )
    elif level == "medium":
        verification = VerificationResult(
            status="flagged",
            flags=["Some claims could not be fully verified against available documents"],
            claims_checked=n_sources,
            claims_verified=max(n_sources - 1, 0),
        )
    else:
        verification = VerificationResult(
            status="failed",
            flags=[
                "Insufficient source material to verify claims",
                "Response is based on general knowledge rather than organisational documents",
            ],
            claims_checked=1,
            claims_verified=0,
        )

    return EnrichedAgentResponse(
        message=model.message,
        sources=model.sources,
        confidence=confidence_breakdown,
        verification=verification,
        ui_hint=model.ui_hint,
        structured_data=model.structured_data,
        follow_up_suggestions=model.follow_up_suggestions,
    )
