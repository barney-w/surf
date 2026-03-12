"""Tests for PDF page counting and text extraction (orchestrator.pdf)."""

import base64

import pymupdf
import pytest

from src.orchestrator.pdf import MAX_DIRECT_PAGES, count_pages, extract_text


def _make_pdf(num_pages: int, text_per_page: str = "Hello world") -> str:
    """Create a minimal PDF with *num_pages* pages and return base64-encoded data."""
    doc = pymupdf.open()
    for _ in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), text_per_page)
    raw = doc.tobytes()
    doc.close()
    return base64.b64encode(raw).decode()


class TestCountPages:
    def test_single_page(self):
        b64 = _make_pdf(1)
        assert count_pages(b64) == 1

    def test_multiple_pages(self):
        b64 = _make_pdf(5)
        assert count_pages(b64) == 5

    def test_invalid_base64_raises(self):
        with pytest.raises(ValueError):
            count_pages("not-valid-base64!!!")

    def test_non_pdf_data_raises(self):
        b64 = base64.b64encode(b"this is not a pdf").decode()
        with pytest.raises(RuntimeError):
            count_pages(b64)


class TestExtractText:
    def test_extracts_text_from_single_page(self):
        b64 = _make_pdf(1, "Test content here")
        text = extract_text(b64)
        assert "Test content here" in text

    def test_extracts_text_from_multiple_pages(self):
        b64 = _make_pdf(3, "Page text")
        text = extract_text(b64)
        assert text.count("Page text") == 3

    def test_truncates_at_max_chars(self):
        # Each page has ~50 chars of text; set max_chars low to trigger truncation.
        b64 = _make_pdf(10, "A" * 50)
        text = extract_text(b64, max_chars=120)
        assert "[Document truncated" in text
        # The extracted text (excluding the truncation notice) should be <= max_chars.
        before_notice = text.split("[Document truncated")[0]
        assert len(before_notice) <= 200  # some margin for newlines

    def test_no_truncation_notice_when_within_budget(self):
        b64 = _make_pdf(2, "Short")
        text = extract_text(b64)
        assert "[Document truncated" not in text


class TestMaxDirectPages:
    def test_constant_is_reasonable(self):
        assert MAX_DIRECT_PAGES == 30


class TestPdfBlockRouting:
    """Test the tier routing logic in builder._prepare_pdf_block."""

    def test_small_pdf_returns_document_block(self):
        from src.orchestrator.builder import _prepare_pdf_block

        b64 = _make_pdf(5)
        block = _prepare_pdf_block(b64)
        assert block["type"] == "document"
        assert block["source"]["media_type"] == "application/pdf"
        assert block["source"]["data"] == b64

    def test_large_pdf_returns_text_block(self):
        from src.orchestrator.builder import _prepare_pdf_block

        b64 = _make_pdf(35)
        block = _prepare_pdf_block(b64)
        assert block["type"] == "text"
        assert "Extracted text from uploaded PDF" in block["text"]
        assert "35 pages" in block["text"]

    def test_boundary_pdf_at_max_pages_is_direct(self):
        from src.orchestrator.builder import _prepare_pdf_block

        b64 = _make_pdf(MAX_DIRECT_PAGES)
        block = _prepare_pdf_block(b64)
        assert block["type"] == "document"

    def test_boundary_pdf_above_max_pages_is_extracted(self):
        from src.orchestrator.builder import _prepare_pdf_block

        b64 = _make_pdf(MAX_DIRECT_PAGES + 1)
        block = _prepare_pdf_block(b64)
        assert block["type"] == "text"

    def test_corrupt_pdf_returns_error_text_block(self):
        from src.orchestrator.builder import _prepare_pdf_block

        b64 = base64.b64encode(b"not a real pdf").decode()
        block = _prepare_pdf_block(b64)
        assert block["type"] == "text"
        assert "could not be processed" in block["text"]
