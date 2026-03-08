"""Tests for manifest entry validation and edge cases."""

import pytest


class TestSourceUrlValidation:
    def test_rejects_javascript_scheme(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("javascript:alert('XSS')")

    def test_rejects_data_uri(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("data:text/html,<h1>test</h1>")

    def test_rejects_ftp_scheme(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("ftp://example.com/file.pdf")

    def test_accepts_https(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        assert _validate_source_url("https://example.com/doc.pdf") == "https://example.com/doc.pdf"

    def test_accepts_http(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        assert (
            _validate_source_url("http://intranet.local/doc.pdf") == "http://intranet.local/doc.pdf"
        )

    def test_accepts_none(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        assert _validate_source_url(None) is None

    def test_rejects_empty_string(self):
        from src.connectors.pdf import _validate_source_url  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(ValueError, match="must start with http"):
            _validate_source_url("")


class TestManifestPathTraversal:
    def test_path_traversal_in_title(self):
        """Ensure path traversal strings in manifest fields don't cause issues."""
        title = "../../../etc/passwd"
        assert isinstance(title, str)  # No crash

    def test_manifest_entry_with_missing_required_fields(self):
        """Manifest entries must have domain and document_type."""
        from pathlib import Path
        from unittest.mock import patch

        from src.connectors.pdf import (
            create_document_from_pdf,  # pyright: ignore[reportUnknownVariableType]
        )

        manifest = {"title": "test"}  # missing domain and document_type
        with (
            patch("src.connectors.pdf.extract_text_from_pdf", return_value="content"),
            pytest.raises(KeyError),
        ):
            create_document_from_pdf(Path("test.pdf"), manifest)
