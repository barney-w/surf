"""CSV connector for creating IngestedDocument instances.

Named ``csv_parser`` to avoid shadowing the stdlib ``csv`` module.
"""

from __future__ import annotations

import csv
import hashlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from src.models import DocumentMetadata, IngestedDocument

_SAFE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _validate_source_url(url: str | None) -> str | None:
    if url is None:
        return None
    if not _SAFE_URL_RE.match(url):
        raise ValueError(f"Invalid source_url: must start with http:// or https://, got: {url!r}")
    return url


def extract_text_from_csv(file_path: Path) -> str:
    """Extract text from a CSV file, converting each row to a readable string.

    Each row is rendered as ``"Column1: value1, Column2: value2, ..."``.  The
    column headers are preserved in every row so that chunked content remains
    self-describing.

    Args:
        file_path: Path to the CSV file.

    Returns:
        All rows joined with double newlines.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not a CSV file.
    """
    if not file_path.exists():
        msg = f"File not found: {file_path}"
        raise FileNotFoundError(msg)

    if file_path.suffix.lower() != ".csv":
        msg = f"Not a CSV file: {file_path}"
        raise ValueError(msg)

    rows: list[str] = []
    with file_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            parts = [f"{col}: {val}" for col, val in row.items() if val]
            if parts:
                rows.append(", ".join(parts))

    return "\n\n".join(rows)


def _generate_document_id(file_path: Path) -> str:
    """Generate a deterministic document ID from the file path."""
    return hashlib.sha256(f"csv:{file_path.name}".encode()).hexdigest()[:16]


def create_document_from_csv(file_path: Path, manifest_entry: dict[str, Any]) -> IngestedDocument:
    """Create an IngestedDocument from a CSV file and its manifest entry.

    Args:
        file_path: Path to the CSV file.
        manifest_entry: Dictionary with document metadata. Expected keys:
            - domain (str): Document domain, e.g. "hr", "it", "governance"
            - document_type (str): e.g. "policy", "procedure", "agreement"
            - title (str, optional): Document title. Defaults to file stem.
            - raw_path (str, optional): Blob storage path. Defaults to str(file_path).
            - content_source (str, optional): e.g. "website", "sharepoint". Defaults to "".
            - section_path (str, optional): URL/folder path. Defaults to "".
            - version, effective_date, expiry_date, author, source_url, tags (optional)

    Returns:
        An IngestedDocument populated with the extracted CSV content and metadata.
    """
    content = extract_text_from_csv(file_path)
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
        source="csv",
        title=manifest_entry.get("title", file_path.stem),
        content=content,
        metadata=metadata,
        raw_path=manifest_entry.get("raw_path", str(file_path)),
    )
