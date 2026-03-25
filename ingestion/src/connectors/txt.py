"""Plain-text connector for creating IngestedDocument instances."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from src.models import DocumentMetadata, IngestedDocument

_SAFE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Extensions treated as plain text.
_TEXT_EXTENSIONS = {".txt", ".md"}


def _validate_source_url(url: str | None) -> str | None:
    if url is None:
        return None
    if not _SAFE_URL_RE.match(url):
        raise ValueError(f"Invalid source_url: must start with http:// or https://, got: {url!r}")
    return url


def extract_text_from_txt(file_path: Path) -> str:
    """Read the full text content of a plain-text or Markdown file.

    Args:
        file_path: Path to the text file.

    Returns:
        The file content as a string.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is not a recognised text format.
    """
    if not file_path.exists():
        msg = f"File not found: {file_path}"
        raise FileNotFoundError(msg)

    if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
        msg = f"Not a recognised text file: {file_path}"
        raise ValueError(msg)

    return file_path.read_text(encoding="utf-8")


def _generate_document_id(file_path: Path) -> str:
    """Generate a deterministic document ID from the file path."""
    return hashlib.sha256(f"txt:{file_path.name}".encode()).hexdigest()[:16]


def create_document_from_txt(file_path: Path, manifest_entry: dict[str, Any]) -> IngestedDocument:
    """Create an IngestedDocument from a plain-text file and its manifest entry.

    Args:
        file_path: Path to the text file (.txt or .md).
        manifest_entry: Dictionary with document metadata. Expected keys:
            - domain (str): Document domain, e.g. "hr", "it", "governance"
            - document_type (str): e.g. "policy", "procedure", "agreement"
            - title (str, optional): Document title. Defaults to file stem.
            - raw_path (str, optional): Blob storage path. Defaults to str(file_path).
            - content_source (str, optional): e.g. "website", "sharepoint". Defaults to "".
            - section_path (str, optional): URL/folder path. Defaults to "".
            - version, effective_date, expiry_date, author, source_url, tags (optional)

    Returns:
        An IngestedDocument populated with the file content and metadata.
    """
    content = extract_text_from_txt(file_path)
    doc_id = _generate_document_id(file_path)

    metadata = DocumentMetadata(
        domain=manifest_entry["domain"],
        document_type=manifest_entry["document_type"],
        version=manifest_entry.get("version"),
        effective_date=manifest_entry.get("effective_date"),
        expiry_date=manifest_entry.get("expiry_date"),
        author=manifest_entry.get("author"),
        source_url=_validate_source_url(manifest_entry.get("source_url")),
        tags=manifest_entry.get("tags", []),
        content_source=manifest_entry.get("content_source", ""),
        section_path=manifest_entry.get("section_path", ""),
    )

    return IngestedDocument(
        id=doc_id,
        source="txt",
        title=manifest_entry.get("title", file_path.stem),
        content=content,
        metadata=metadata,
        raw_path=manifest_entry.get("raw_path", str(file_path)),
    )
