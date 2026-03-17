"""Tests for the PDF connector."""

import hashlib
from pathlib import Path

import fitz  # pyright: ignore[reportMissingTypeStubs]
import pytest

from src.connectors.pdf import (
    _validate_source_url,  # pyright: ignore[reportPrivateUsage]
    create_document_from_pdf,  # pyright: ignore[reportUnknownVariableType]
    extract_text_from_pdf,
)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a minimal test PDF with known content."""
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello, Surf project!")  # pyright: ignore[reportUnknownMemberType]
    doc.save(str(pdf_path))  # pyright: ignore[reportUnknownMemberType]
    doc.close()
    return pdf_path


@pytest.fixture
def encrypted_pdf(tmp_path: Path) -> Path:
    """Create an encrypted PDF."""
    pdf_path = tmp_path / "encrypted.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Secret content")  # pyright: ignore[reportUnknownMemberType]
    perm = int(fitz.PDF_PERM_ACCESSIBILITY)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownArgumentType]
    encrypt_meth = int(fitz.PDF_ENCRYPT_AES_256)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownArgumentType]
    doc.save(  # pyright: ignore[reportUnknownMemberType]
        str(pdf_path),
        encryption=encrypt_meth,
        owner_pw="owner",
        user_pw="user",
        permissions=perm,
    )
    doc.close()
    return pdf_path


class TestExtractTextFromPdf:
    def test_extracts_expected_content(self, sample_pdf: Path) -> None:
        text = extract_text_from_pdf(sample_pdf)
        assert "Hello, Surf project!" in text

    def test_multi_block_pdf_separates_blocks_with_double_newline(self, tmp_path: Path) -> None:
        """Text blocks from separate positions on the page are joined with \\n\\n."""
        pdf_path = tmp_path / "multi_block.pdf"
        doc = fitz.open()
        page = doc.new_page()
        # Insert two text blocks at clearly separate vertical positions.
        page.insert_text((72, 72), "First block of text.")  # pyright: ignore[reportUnknownMemberType]
        page.insert_text((72, 400), "Second block of text.")  # pyright: ignore[reportUnknownMemberType]
        doc.save(str(pdf_path))  # pyright: ignore[reportUnknownMemberType]
        doc.close()

        text = extract_text_from_pdf(pdf_path)
        assert "First block of text." in text
        assert "Second block of text." in text
        # Blocks should be separated by a double newline.
        assert "\n\n" in text

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            extract_text_from_pdf(tmp_path / "nonexistent.pdf")

    def test_non_pdf_raises(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "file.txt"
        txt_file.write_text("not a pdf")
        with pytest.raises(ValueError, match="Not a PDF"):
            extract_text_from_pdf(txt_file)

    def test_encrypted_pdf_raises(self, encrypted_pdf: Path) -> None:
        with pytest.raises(ValueError, match="encrypted"):
            extract_text_from_pdf(encrypted_pdf)


class TestValidateSourceUrl:
    def test_source_url_rejects_javascript_scheme(self):
        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("javascript:alert(1)")

    def test_source_url_rejects_data_uri(self):
        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("data:text/html,<script>alert(1)</script>")

    def test_source_url_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("file:///etc/passwd")

    def test_source_url_accepts_https(self):
        result = _validate_source_url("https://example.com")
        assert result == "https://example.com"

    def test_source_url_accepts_http(self):
        result = _validate_source_url("http://example.com/doc.pdf")
        assert result == "http://example.com/doc.pdf"

    def test_source_url_accepts_none(self):
        result = _validate_source_url(None)
        assert result is None


class TestCreateDocumentFromPdf:
    def test_document_id_is_deterministic(self, sample_pdf: Path) -> None:
        manifest = {"domain": "hr", "document_type": "policy"}
        doc1 = create_document_from_pdf(sample_pdf, manifest)
        doc2 = create_document_from_pdf(sample_pdf, manifest)
        assert doc1.id == doc2.id

    def test_document_id_matches_expected_hash(self, sample_pdf: Path) -> None:
        manifest = {"domain": "hr", "document_type": "policy"}
        doc = create_document_from_pdf(sample_pdf, manifest)
        expected = hashlib.sha256(f"pdf:{sample_pdf.name}".encode()).hexdigest()[:16]
        assert doc.id == expected

    def test_creates_valid_document(self, sample_pdf: Path) -> None:
        manifest = {
            "domain": "it",
            "document_type": "procedure",
            "title": "Test Doc",
            "author": "Test Author",
            "tags": ["test"],
        }
        doc = create_document_from_pdf(sample_pdf, manifest)
        assert doc.source == "pdf"
        assert doc.title == "Test Doc"
        assert "Hello, Surf project!" in doc.content
        assert doc.metadata.domain == "it"
        assert doc.metadata.document_type == "procedure"
        assert doc.metadata.author == "Test Author"
        assert doc.metadata.tags == ["test"]
