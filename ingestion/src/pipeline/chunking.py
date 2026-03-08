"""Chunking engine — splits documents into overlapping chunks for embedding and retrieval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

import tiktoken

from src.models import Chunk, IngestedDocument

_encoder = tiktoken.get_encoding("cl100k_base")


@dataclass
class ChunkingConfig:
    """Configuration for the chunking engine."""

    strategy: Literal["semantic", "fixed", "hybrid"] = "semantic"
    max_chunk_tokens: int = 700
    overlap_tokens: int = 150
    respect_boundaries: bool = True
    preserve_headings: bool = True


def _token_len(text: str) -> int:
    """Return the number of tokens in *text* using cl100k_base."""
    return len(_encoder.encode(text))


def _tokens_to_text(tokens: list[int]) -> str:
    """Decode a list of token ids back into a string."""
    return _encoder.decode(tokens)


def _encode(text: str) -> list[int]:
    return _encoder.encode(text)


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"^("
    r"#{1,6}\s+.+"  # Markdown heading: ## Section
    r"|[A-Z][A-Z0-9 ,/&:-]{2,}"  # ALL-CAPS line: INTRODUCTION
    r"|\d+\.[\d.]*\s+\S.{1,}"  # Numbered clause: 14.7 Call Out Payments
    r")$"
)


def _is_heading(line: str) -> bool:
    """Return True if *line* looks like a section heading.

    Matches:
    - Markdown headings (# Foo)
    - ALL-CAPS lines of 3+ chars (e.g. "INTRODUCTION")
    - Numbered clause headings (e.g. "14.7 Call Out Payments", "3. Definitions")
    """
    return bool(_HEADING_RE.match(line.strip()))


# ---------------------------------------------------------------------------
# Sub-clause detection and splitting
# ---------------------------------------------------------------------------

# Matches legal sub-clause markers at the start of a line:
#   (a), (b), (iii), (xiv), (aa), etc.
_SUB_CLAUSE_RE = re.compile(
    r"(?m)^\s*\(([a-z]{1,3}|[ivxlcdm]+)\)\s",
    re.IGNORECASE,
)


def _sub_clause_split(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split *text* at legal sub-clause markers, grouping into max_tokens windows.

    Tries to split at ``(a)``, ``(b)``, ``(i)``, ``(ii)`` etc. boundaries before
    falling back to pure token-window splitting.  When a group of sub-clauses is
    split across two chunks the last sub-clause of the preceding chunk is carried
    into the next chunk as overlap context.
    """
    matches = list(_SUB_CLAUSE_RE.finditer(text))
    if len(matches) < 2:
        # Not enough sub-clause markers — fall back to token windows.
        return _fixed_chunk_raw(text, max_tokens, overlap_tokens)

    # Slice text into segments: optional preamble + one segment per sub-clause.
    boundaries = [m.start() for m in matches]
    segments: list[str] = []

    preamble = text[: boundaries[0]].strip()
    if preamble:
        segments.append(preamble)

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        segment = text[start:end].strip()
        if segment:
            segments.append(segment)

    # Pack segments into token-window chunks, carrying the last segment as overlap.
    chunks: list[str] = []
    current: list[str] = []
    current_tok = 0

    for seg in segments:
        seg_tok = _token_len(seg)

        if seg_tok > max_tokens:
            # Single sub-clause is itself oversized — flush then hard-split it.
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_tok = 0
            chunks.extend(_fixed_chunk_raw(seg, max_tokens, overlap_tokens))
            continue

        separator_tok = 2 if current else 0  # "\n\n" ≈ 2 tokens
        if current_tok + separator_tok + seg_tok > max_tokens and current:
            chunks.append("\n\n".join(current))
            # Carry the last segment into the next chunk as overlap context.
            overlap_seg = current[-1]
            overlap_tok = _token_len(overlap_seg)
            if overlap_tok + seg_tok + 2 <= max_tokens:
                current = [overlap_seg, seg]
                current_tok = overlap_tok + seg_tok + 2
            else:
                current = [seg]
                current_tok = seg_tok
        else:
            current.append(seg)
            current_tok += separator_tok + seg_tok

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text]


# ---------------------------------------------------------------------------
# Semantic chunking
# ---------------------------------------------------------------------------


def _semantic_chunk(text: str, config: ChunkingConfig) -> list[tuple[str, str | None]]:
    """Split on paragraph / section boundaries.

    Returns a list of ``(content, section_heading)`` pairs.
    """
    paragraphs = re.split(r"\n{2,}", text.strip())
    if not paragraphs:
        return []

    results: list[tuple[str, str | None]] = []
    current_heading: str | None = None
    current_parts: list[str] = []
    current_tokens = 0

    heading_budget = 0  # tokens consumed by the heading prefix

    def _flush() -> None:
        nonlocal current_parts, current_tokens, heading_budget
        if not current_parts:
            return
        body = "\n\n".join(current_parts)
        results.append((body, current_heading))
        current_parts = []
        current_tokens = 0
        heading_budget = _heading_token_cost(current_heading, config)

    def _heading_token_cost(heading: str | None, cfg: ChunkingConfig) -> int:
        if heading and cfg.preserve_headings:
            return _token_len(heading + "\n\n")
        return 0

    heading_budget = _heading_token_cost(current_heading, config)

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Detect headings.
        # Case 1: entire paragraph is a heading line (standalone heading).
        # Case 2: paragraph starts with a heading line followed by body text
        #         (common in PDFs where the clause title and first sentence aren't
        #         separated by a blank line).  In this case update the heading and
        #         process the remaining body text under it.
        first_line = para.split("\n", 1)[0]
        if _is_heading(first_line):
            rest = para[len(first_line) :].strip()
            if not rest:
                # Standalone heading — flush and switch section.
                _flush()
                current_heading = first_line.strip()
                heading_budget = _heading_token_cost(current_heading, config)
                continue
            else:
                # Embedded heading — flush, update heading, then fall through
                # to process *rest* as the first paragraph under the new section.
                _flush()
                current_heading = first_line.strip()
                heading_budget = _heading_token_cost(current_heading, config)
                para = rest

        para_tokens = _token_len(para)
        available = config.max_chunk_tokens - heading_budget

        # If single paragraph exceeds max tokens, use sub-clause-aware splitting.
        if para_tokens > available and not current_parts:
            max_tok = config.max_chunk_tokens - heading_budget
            sub_chunks = _sub_clause_split(para, max_tok, config.overlap_tokens)
            for sc in sub_chunks:
                results.append((sc, current_heading))
            continue

        # If adding this paragraph would exceed the limit, flush first
        if current_tokens + para_tokens + (2 if current_parts else 0) > available:
            _flush()

        joiner_tokens = 2 if current_parts else 0  # "\n\n" between paras ≈ 2 tokens
        current_parts.append(para)
        current_tokens += para_tokens + joiner_tokens

    _flush()
    return results


# ---------------------------------------------------------------------------
# Fixed-size chunking (token windows with overlap)
# ---------------------------------------------------------------------------


def _fixed_chunk_raw(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split *text* into fixed-size token windows with overlap.  Returns strings."""
    tokens = _encode(text)
    if not tokens:
        return []
    if len(tokens) <= max_tokens:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_text = _tokens_to_text(tokens[start:end])
        chunks.append(chunk_text)
        if end >= len(tokens):
            break
        start = end - overlap_tokens
    return chunks


def _fixed_chunk(text: str, config: ChunkingConfig) -> list[tuple[str, str | None]]:
    """Split into fixed-size token windows with overlap.  Returns (content, None) pairs."""
    raw = _fixed_chunk_raw(text, config.max_chunk_tokens, config.overlap_tokens)
    return [(c, None) for c in raw]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _generate_chunk_id(document_id: str, chunk_index: int) -> str:
    """Deterministic chunk ID: first 16 hex chars of sha256(document_id:chunk_index)."""
    return hashlib.sha256(f"{document_id}:{chunk_index}".encode()).hexdigest()[:16]


def chunk_document(document: IngestedDocument, config: ChunkingConfig | None = None) -> list[Chunk]:
    """Split an *IngestedDocument* into a list of *Chunk* objects."""
    if config is None:
        config = ChunkingConfig()

    text = document.content
    if not text or not text.strip():
        return []

    # Choose strategy
    if config.strategy == "semantic":
        raw_chunks = _semantic_chunk(text, config)
    elif config.strategy == "fixed":
        raw_chunks = _fixed_chunk(text, config)
    elif config.strategy == "hybrid":
        # Hybrid: semantic first, then fixed-split any oversized chunks.
        raw_chunks = _semantic_chunk(text, config)
        final: list[tuple[str, str | None]] = []
        for content, heading in raw_chunks:
            if _token_len(content) > config.max_chunk_tokens:
                for sub in _fixed_chunk_raw(
                    content, config.max_chunk_tokens, config.overlap_tokens
                ):
                    final.append((sub, heading))
            else:
                final.append((content, heading))
        raw_chunks = final
    else:
        raw_chunks = _semantic_chunk(text, config)

    # Build Chunk objects
    chunks: list[Chunk] = []
    for idx, (content, heading) in enumerate(raw_chunks):
        # Prepend heading when configured
        full_text = f"{heading}\n\n{content}" if config.preserve_headings and heading else content

        chunk = Chunk(
            id=_generate_chunk_id(document.id, idx),
            document_id=document.id,
            chunk_index=idx,
            content=full_text,
            metadata=document.metadata,
            section_heading=heading if config.preserve_headings else None,
            token_count=_token_len(full_text),
            document_title=document.title,
        )
        chunks.append(chunk)

    return chunks
