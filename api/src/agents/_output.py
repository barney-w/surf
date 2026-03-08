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


def _normalise_structured_data(model: AgentResponseModel) -> AgentResponseModel:
    """Ensure structured_data is None when empty and ui_hint is consistent.

    LLMs sometimes emit structured_data as "" or "{}" instead of null, or set
    ui_hint to a non-"text" value without providing any structured_data.  This
    normalises both fields so the frontend never renders an empty card.
    """
    sd = model.structured_data
    ui = model.ui_hint
    updates: dict[str, str | None] = {}

    # Normalise empty structured_data to None.
    if sd is not None:
        stripped = sd.strip()
        if stripped in ("", "{}", "null", "None"):
            updates["structured_data"] = None
            sd = None

    # If structured_data is None, ui_hint must be "text".
    if sd is None and ui != "text":
        updates["ui_hint"] = "text"

    # If ui_hint is "text" but structured_data is present, clear it.
    if ui == "text" and sd is not None:
        updates["structured_data"] = None

    return model.model_copy(update=updates) if updates else model


def _sanitize_agent_response(model: AgentResponseModel) -> AgentResponseModel:
    """Remove any leaked === SOURCE === blocks from the message field.

    If the message contains source blocks AND the sources list is empty, the
    blocks are parsed into Source objects so the UI can render them as cards.
    Also normalises structured_data / ui_hint consistency.
    """
    if "=== SOURCE" not in model.message:
        # Still deduplicate sources even when the message is clean.
        if model.sources:
            deduped = _deduplicate_sources(model.sources)
            if len(deduped) != len(model.sources):
                model = model.model_copy(update={"sources": deduped})
        return _normalise_structured_data(model)

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

    result = model.model_copy(update={"message": clean_message, "sources": new_sources})
    return _normalise_structured_data(result)


def _extract_json_object(text: str) -> str | None:
    """Find the first top-level JSON object in text using bracket matching.

    LLMs sometimes emit free text before or after the JSON object.
    This robustly extracts the outermost { ... } block.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_agent_output(raw_text: str, agent_name: str) -> AgentResponseModel:
    """Parse agent output into an AgentResponseModel.

    Domain agents are instructed to output structured JSON. The agent framework
    may emit this as a text event rather than a typed value event, so we attempt
    to parse the raw text as an AgentResponseModel before falling back to
    treating it as plain prose.

    Handles common LLM quirks: free text before/after JSON, markdown fenced
    blocks, and JSON not starting at the beginning of a line.
    """
    stripped = raw_text.strip()

    # Strip markdown fences if present.
    json_candidate = stripped
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines[1:]
        json_candidate = "\n".join(inner).strip()

    # Fast path: buffer is a clean JSON object.
    if json_candidate.startswith("{"):
        try:
            data = json.loads(json_candidate)
            return _sanitize_agent_response(AgentResponseModel.model_validate(data))
        except (json.JSONDecodeError, ValidationError):
            logger.debug("parse_agent_output: direct JSON parse failed for agent=%s", agent_name)

    # Robust path: find the JSON object anywhere in the text.
    # LLMs often emit commentary before the JSON block.
    json_str = _extract_json_object(json_candidate)
    if json_str:
        try:
            data = json.loads(json_str)
            return _sanitize_agent_response(AgentResponseModel.model_validate(data))
        except (json.JSONDecodeError, ValidationError):
            logger.debug("parse_agent_output: extracted JSON parse failed for agent=%s", agent_name)

    # Plain-text fallback
    logger.warning(
        "parse_agent_output: no valid JSON found for agent=%s, using plain-text fallback",
        agent_name,
    )
    return _sanitize_agent_response(
        AgentResponseModel(
            message=raw_text,
            sources=[],
            confidence="medium",
            ui_hint="text",
            follow_up_suggestions=[],
        )
    )
