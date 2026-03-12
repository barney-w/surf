"""Integration test: content_source and section_path survive the full pipeline.

Manifest entry → IngestedDocument → Chunks → index dicts.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz  # pyright: ignore[reportMissingTypeStubs]

from src.connectors.pdf import create_document_from_pdf
from src.main import _build_manifest_entry, _chunks_to_dicts
from src.pipeline.chunking import chunk_document


def _create_test_pdf(path: Path) -> None:
    """Write a single-page PDF with enough text to produce at least one chunk."""
    doc = fitz.open()
    page = doc.new_page()
    text = (
        "This is a test policy document about waste management and recycling "
        "services. It covers residential bin collection schedules and guidelines. "
        "Residents must place bins on the kerb by 6am on collection day. "
        "Missed collections can be reported online within 24 hours. "
        "Green-lid bins are for general waste and are collected weekly. "
        "Yellow-lid bins are for recyclable materials collected fortnightly. "
        "Contaminated recycling bins may not be emptied. "
        "Bulky item collections can be booked up to four times per year."
    )
    page.insert_text(  # pyright: ignore[reportUnknownMemberType]
        fitz.Point(72, 72),
        text,
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()


def test_content_source_survives_full_pipeline() -> None:
    """Verify content_source and section_path propagate through every stage."""
    manifest = {
        "test-policy.pdf": {
            "domain": "services",
            "document_type": "policy",
            "content_source": "website",
            "section_path": "Services/Waste-recycling",
            "title": "Waste Policy",
            "source_url": "https://example.com/waste-policy.pdf",
        }
    }

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "test-policy.pdf"
        _create_test_pdf(pdf_path)

        # Stage 1: _build_manifest_entry
        entry = _build_manifest_entry(Path("/tmp/test-policy.pdf"), "services", manifest)
        assert entry["content_source"] == "website"
        assert entry["section_path"] == "Services/Waste-recycling"

        # Stage 2: create_document_from_pdf
        doc = create_document_from_pdf(pdf_path, manifest["test-policy.pdf"])
        assert doc.metadata.content_source == "website"
        assert doc.metadata.section_path == "Services/Waste-recycling"

        # Stage 3: chunk_document
        chunks = chunk_document(doc)
        assert len(chunks) >= 1, f"Expected at least 1 chunk, got {len(chunks)}"
        for chunk in chunks:
            assert chunk.metadata.content_source == "website"
            assert chunk.metadata.section_path == "Services/Waste-recycling"

        # Stage 4: _chunks_to_dicts
        fake_embeddings = [[0.0] * 3072 for _ in chunks]
        index_dicts = _chunks_to_dicts(chunks, fake_embeddings)
        assert len(index_dicts) == len(chunks)
        for d in index_dicts:
            assert d["content_source"] == "website"
            assert d["section_path"] == "Services/Waste-recycling"
            assert d["domain"] == "services"
            assert d["document_type"] == "policy"
            assert d["source_url"] == "https://example.com/waste-policy.pdf"
