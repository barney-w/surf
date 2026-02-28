import json
import logging
import re

from pydantic import ValidationError

from src.models.agent import AgentResponseModel, Source

logger = logging.getLogger(__name__)

# Matches a full === SOURCE N === ... === END SOURCE N === block (multi-line).
_SOURCE_BLOCK_RE = re.compile(
    r"=== SOURCE \d+ ===\s*(.*?)\s*=== END SOURCE \d+ ===",
    re.DOTALL,
)

# Named-value lines inside a source block header (before CONTENT:).
_TITLE_RE = re.compile(r'title:\s*"([^"]*)"')
_SECTION_RE = re.compile(r'section:\s*"([^"]*)"')
_DOCID_RE = re.compile(r'document_id:\s*"([^"]*)"')
_RELEVANCE_RE = re.compile(r"relevance:\s*([0-9.]+)")
_URL_RE = re.compile(r'url:\s*"([^"]*)"')
_SNIPPET_RE = re.compile(r'snippet:\s*"([^"]*)"')


def extract_sources(text: str) -> list[Source]:
    """Extract Source objects from text containing === SOURCE N === blocks."""
    sources = []
    for m in _SOURCE_BLOCK_RE.finditer(text):
        src = _parse_source_block(m.group(1))
        if src:
            sources.append(src)
    return sources


def _deduplicate_sources(sources: list[Source]) -> list[Source]:
    """Deduplicate sources by document_id, keeping the highest-confidence entry.

    When the same document contributes multiple chunks (e.g. top_k=8 on a large
    document), we surface it as a single source card.  The highest-confidence
    entry wins; section headings from other entries are merged into it when the
    winning entry has no section.
    """
    if not sources:
        return sources

    best: dict[str, Source] = {}
    for src in sources:
        key = src.document_id
        if key not in best or src.confidence > best[key].confidence:
            best[key] = src
        elif best[key].section is None and src.section:
            # Carry over a non-null section from a lower-confidence duplicate.
            best[key] = best[key].model_copy(update={"section": src.section})

    # Preserve original ordering (first occurrence of each document_id).
    seen: set[str] = set()
    result: list[Source] = []
    for src in sources:
        if src.document_id not in seen:
            seen.add(src.document_id)
            result.append(best[src.document_id])
    return result


def _parse_source_block(block_body: str) -> Source | None:
    """Extract a Source from the text inside a source block, or return None."""
    # Only look at the header section (before CONTENT:)
    header = block_body.split("CONTENT:", 1)[0]
    doc_id_m = _DOCID_RE.search(header)
    if not doc_id_m:
        return None
    title_m = _TITLE_RE.search(header)
    section_m = _SECTION_RE.search(header)
    relevance_m = _RELEVANCE_RE.search(header)
    url_m = _URL_RE.search(header)
    snippet_m = _SNIPPET_RE.search(header)
    try:
        return Source(
            title=title_m.group(1) if title_m else "",
            section=section_m.group(1) if section_m else None,
            document_id=doc_id_m.group(1),
            confidence=float(relevance_m.group(1)) if relevance_m else 0.5,
            url=url_m.group(1) if url_m else None,
            snippet=snippet_m.group(1) if snippet_m else None,
        )
    except Exception:
        return None


def _sanitize_agent_response(model: AgentResponseModel) -> AgentResponseModel:
    """Remove any leaked === SOURCE === blocks from the message field.

    If the message contains source blocks AND the sources list is empty, the
    blocks are parsed into Source objects so the UI can render them as cards.
    """
    if "=== SOURCE" not in model.message:
        # Still deduplicate sources even when the message is clean.
        if model.sources:
            deduped = _deduplicate_sources(model.sources)
            if len(deduped) != len(model.sources):
                return model.model_copy(update={"sources": deduped})
        return model

    recovered_sources: list[Source] = []
    for m in _SOURCE_BLOCK_RE.finditer(model.message):
        src = _parse_source_block(m.group(1))
        if src:
            recovered_sources.append(src)

    clean_message = _SOURCE_BLOCK_RE.sub("", model.message).strip()
    if not clean_message:
        # Synthesize a brief message from retrieved snippets rather than a
        # generic fallback.  Use the first non-empty snippet as context.
        snippets = [s.snippet for s in recovered_sources if s.snippet]
        if snippets:
            clean_message = snippets[0].rstrip(".")
            if len(snippets) > 1:
                clean_message += f" (and {len(snippets) - 1} more relevant section(s))."
            else:
                clean_message += "."
        else:
            clean_message = "Relevant documents were found. Please see the sources below."

    new_sources = _deduplicate_sources(model.sources if model.sources else recovered_sources)

    return model.model_copy(update={"message": clean_message, "sources": new_sources})


def parse_agent_output(raw_text: str, agent_name: str) -> AgentResponseModel:
    """Parse agent output into an AgentResponseModel.

    Domain agents are instructed to output structured JSON. The agent framework
    may emit this as a text event rather than a typed value event, so we attempt
    to parse the raw text as an AgentResponseModel before falling back to
    treating it as plain prose.
    """
    stripped = raw_text.strip()

    # Try JSON parse — handles both clean JSON and ```json ... ``` fenced blocks
    json_candidate = stripped
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Strip opening fence (```json or ```) and closing fence
        inner = lines[1:-1] if len(lines) > 2 else lines[1:]
        json_candidate = "\n".join(inner).strip()

    # Fast path: buffer starts with JSON object (single-agent case).
    if json_candidate.startswith("{"):
        try:
            data = json.loads(json_candidate)
            return _sanitize_agent_response(AgentResponseModel.model_validate(data))
        except (json.JSONDecodeError, ValidationError):
            logger.debug("parse_agent_output: JSON parse failed for agent=%s", agent_name)

    # Slow path: in the two-phase flow the buffer may start with the search-agent's
    # echoed text followed by the synthesise agent's JSON.  Find the LAST '{' that
    # begins a top-level JSON object and try to parse from there.
    last_brace = json_candidate.rfind("\n{")
    if last_brace != -1:
        candidate = json_candidate[last_brace:].lstrip()
        try:
            data = json.loads(candidate)
            return _sanitize_agent_response(AgentResponseModel.model_validate(data))
        except (json.JSONDecodeError, ValidationError):
            logger.debug("parse_agent_output: late-JSON parse failed for agent=%s", agent_name)

    # Plain-text fallback
    return _sanitize_agent_response(AgentResponseModel(
        message=raw_text,
        sources=[],
        confidence="medium",
        ui_hint="text",
        follow_up_suggestions=[],
    ))
