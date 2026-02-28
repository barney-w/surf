"""Tests for the chunking engine."""

from __future__ import annotations

import tiktoken

from src.models import DocumentMetadata, IngestedDocument
from src.pipeline.chunking import (
    ChunkingConfig,
    _generate_chunk_id,
    _is_heading,
    _sub_clause_split,
    chunk_document,
)

_enc = tiktoken.get_encoding("cl100k_base")


def _token_len(text: str) -> int:
    return len(_enc.encode(text))


def _make_doc(content: str, doc_id: str = "doc-1") -> IngestedDocument:
    return IngestedDocument(
        id=doc_id,
        source="test",
        title="Test Doc",
        content=content,
        metadata=DocumentMetadata(domain="hr", document_type="policy"),
        raw_path="/tmp/test.pdf",
    )


# ---- 1. Short document produces a single chunk ----

def test_short_document_single_chunk() -> None:
    doc = _make_doc("Hello, this is a short paragraph.")
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].content == "Hello, this is a short paragraph."
    assert chunks[0].document_id == "doc-1"
    assert chunks[0].chunk_index == 0


# ---- 2. Long document produces multiple chunks with correct overlap ----

def test_long_document_multiple_chunks_with_overlap() -> None:
    config = ChunkingConfig(strategy="fixed", max_chunk_tokens=50, overlap_tokens=10)
    words = [f"word{i}" for i in range(200)]
    long_text = " ".join(words)
    doc = _make_doc(long_text)
    chunks = chunk_document(doc, config)
    assert len(chunks) >= 3, f"Expected >=3 chunks, got {len(chunks)}"

    # Verify overlap: last overlap_tokens of chunk N should appear at start of chunk N+1
    for i in range(len(chunks) - 1):
        cur_tokens = _enc.encode(chunks[i].content)
        next_tokens = _enc.encode(chunks[i + 1].content)
        overlap_from_cur = cur_tokens[-config.overlap_tokens:]
        overlap_in_next = next_tokens[: config.overlap_tokens]
        assert overlap_from_cur == overlap_in_next, (
            f"Overlap mismatch between chunk {i} and {i + 1}"
        )


# ---- 3. Section headings are preserved and prepended ----

def test_section_headings_preserved() -> None:
    text = (
        "INTRODUCTION\n\n"
        "This is the introduction paragraph.\n\n"
        "SECOND SECTION\n\n"
        "Body of the second section."
    )
    doc = _make_doc(text)
    config = ChunkingConfig(preserve_headings=True, max_chunk_tokens=512)
    chunks = chunk_document(doc, config)

    # The first chunk should have "INTRODUCTION" prepended
    assert chunks[0].content.startswith("INTRODUCTION")
    assert "This is the introduction paragraph." in chunks[0].content

    # The second chunk should have "SECOND SECTION" prepended
    assert chunks[1].content.startswith("SECOND SECTION")
    assert "Body of the second section." in chunks[1].content


# ---- 4. No chunk exceeds max_chunk_tokens ----

def test_no_chunk_exceeds_max_tokens() -> None:
    paragraphs = [
        f"Paragraph {i}. " + ("Some filler text to add tokens. " * 20)
        for i in range(20)
    ]
    text = "\n\n".join(paragraphs)
    doc = _make_doc(text)
    config = ChunkingConfig(max_chunk_tokens=128, overlap_tokens=16)
    chunks = chunk_document(doc, config)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= config.max_chunk_tokens, (
            f"Chunk {chunk.chunk_index} has {chunk.token_count} tokens, "
            f"exceeds max of {config.max_chunk_tokens}"
        )


# ---- 5. Chunk IDs are deterministic ----

def test_chunk_ids_deterministic() -> None:
    doc = _make_doc("Hello world.\n\nSecond paragraph.\n\nThird paragraph.")
    chunks_a = chunk_document(doc)
    chunks_b = chunk_document(doc)
    assert len(chunks_a) == len(chunks_b)
    for a, b in zip(chunks_a, chunks_b):
        assert a.id == b.id


# ---- 6. _generate_chunk_id is a sha256 prefix ----

def test_generate_chunk_id_format() -> None:
    cid = _generate_chunk_id("doc-1", 0)
    assert len(cid) == 16
    int(cid, 16)  # must be valid hex

    # Deterministic
    assert cid == _generate_chunk_id("doc-1", 0)
    # Different index -> different id
    assert cid != _generate_chunk_id("doc-1", 1)


# ---- 7. Metadata is inherited from parent document ----

def test_metadata_inherited() -> None:
    meta = DocumentMetadata(domain="hr", document_type="policy", author="Alice")
    doc = IngestedDocument(
        id="doc-42",
        source="azure-blob",
        title="My Doc",
        content="Some text.",
        metadata=meta,
        raw_path="/tmp/doc.pdf",
    )
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].metadata.domain == "hr"
    assert chunks[0].metadata.document_type == "policy"
    assert chunks[0].metadata.author == "Alice"


# ---- 8. Headings not prepended when preserve_headings=False ----

def test_headings_not_prepended_when_disabled() -> None:
    text = "INTRODUCTION\n\nBody text here."
    doc = _make_doc(text)
    config = ChunkingConfig(preserve_headings=False)
    chunks = chunk_document(doc, config)
    assert len(chunks) >= 1
    assert not chunks[0].content.startswith("INTRODUCTION")


# ---- 9. Default chunk size and overlap ----

def test_default_config_values() -> None:
    config = ChunkingConfig()
    assert config.max_chunk_tokens == 700
    assert config.overlap_tokens == 150


# ---- 10. Numbered clause headings are detected ----

def test_numbered_clause_heading_detected() -> None:
    assert _is_heading("14.7 Call Out Payments")
    assert _is_heading("3. Definitions")
    assert _is_heading("3.2.1 Annual Leave Entitlements")


def test_sub_clause_marker_not_detected_as_heading() -> None:
    # Sub-clause markers like "(g) Some text" must NOT be treated as section headings —
    # they should stay grouped with their surrounding content.
    assert not _is_heading("(g) Where the Agreed Call Outs are exceeded")
    assert not _is_heading("(iii) The calculation shall be as follows:")


def test_numbered_clause_heading_splits_section() -> None:
    text = (
        "14.7 Call Out Payments\n\n"
        "An employee called out shall be paid a minimum of three hours.\n\n"
        "15.1 Overtime\n\n"
        "Overtime rates apply after 38 hours per week."
    )
    doc = _make_doc(text)
    config = ChunkingConfig(preserve_headings=True, max_chunk_tokens=512)
    chunks = chunk_document(doc, config)

    assert chunks[0].section_heading == "14.7 Call Out Payments"
    assert chunks[1].section_heading == "15.1 Overtime"


# ---- 11. Sub-clause splitting keeps related sub-clauses together ----

def test_sub_clause_split_keeps_clauses_together() -> None:
    # Build a paragraph with sub-clauses that together fit within 1500 tokens.
    sub_clauses = "\n".join([
        "(a) First sub-clause with some content describing entitlements.",
        "(b) Second sub-clause with additional payment conditions.",
        "(c) Third sub-clause outlining calculation methodology.",
        "(d) Fourth sub-clause about appeal processes.",
    ])
    chunks = _sub_clause_split(sub_clauses, max_tokens=500, overlap_tokens=50)
    # All sub-clauses together are well under 500 tokens — should be one chunk.
    assert len(chunks) == 1
    assert "(a)" in chunks[0]
    assert "(d)" in chunks[0]


def test_sub_clause_split_splits_at_clause_boundaries() -> None:
    # Build sub-clauses that individually are small but together exceed max_tokens.
    # Each "filler" paragraph is ~40 tokens; 5 sub-clauses × 40 = ~200 tokens.
    filler = "word " * 35  # ~35 tokens per sub-clause
    sub_clauses = "\n".join([
        f"(a) {filler}",
        f"(b) {filler}",
        f"(c) {filler}",
        f"(d) {filler}",
        f"(e) {filler}",
    ])
    chunks = _sub_clause_split(sub_clauses, max_tokens=80, overlap_tokens=20)
    # Should split somewhere — each chunk must be within the token limit.
    assert len(chunks) > 1
    for chunk in chunks:
        assert _token_len(chunk) <= 80, f"Chunk exceeds limit: {_token_len(chunk)} tokens"


def test_sub_clause_split_no_mid_sentence_cut() -> None:
    # A full legal-style clause: (g) premise, then calculation detail.
    text = (
        "(g) Where the Agreed Call Outs in sub-clause (k) are exceeded at the end "
        "of the financial year, all Employees in their respective Business Unit shall "
        "be entitled to an additional payment. This additional payment shall be "
        "calculated as follows: Base rate multiplied by excess call out hours at "
        "time and a half, plus any applicable weekend loading as defined in Schedule 3."
    )
    # Should stay as one chunk — it's a single sub-clause well under 1500 tokens.
    chunks = _sub_clause_split(text, max_tokens=1500, overlap_tokens=300)
    assert len(chunks) == 1
    assert "calculated as follows" in chunks[0]
    assert "Schedule 3" in chunks[0]
