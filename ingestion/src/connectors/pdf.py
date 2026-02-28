"""PDF connector for extracting text and creating IngestedDocument instances."""

import hashlib
from pathlib import Path

import fitz

from src.models import DocumentMetadata, IngestedDocument


def extract_text_from_pdf(file_path: Path) -> str:
    """Extract text from all pages of a PDF file using PyMuPDF.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Concatenated text from all pages.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not a PDF or is encrypted.
    """
    if not file_path.exists():
        msg = f"File not found: {file_path}"
        raise FileNotFoundError(msg)

    if file_path.suffix.lower() != ".pdf":
        msg = f"Not a PDF file: {file_path}"
        raise ValueError(msg)

    doc = fitz.open(file_path)
    try:
        if doc.is_encrypted:
            msg = f"PDF is encrypted and cannot be read: {file_path}"
            raise ValueError(msg)

        pages: list[str] = []
        for page in doc:
            # Extract text blocks sorted top-to-bottom, left-to-right.
            # block format: (x0, y0, x1, y1, text, block_no, block_type)
            # block_type 0 = text, 1 = image
            blocks = page.get_text("blocks")
            text_blocks = [
                b[4].strip()
                for b in sorted(blocks, key=lambda b: (b[1], b[0]))
                if b[6] == 0 and b[4].strip()
            ]
            if text_blocks:
                pages.append("\n\n".join(text_blocks))

        return "\n\n".join(pages)
    finally:
        doc.close()


def _generate_document_id(file_path: Path) -> str:
    """Generate a deterministic document ID from the file path."""
    return hashlib.sha256(f"pdf:{file_path.name}".encode()).hexdigest()[:16]


def create_document_from_pdf(file_path: Path, manifest_entry: dict) -> IngestedDocument:
    """Create an IngestedDocument from a PDF file and its manifest entry.

    Args:
        file_path: Path to the PDF file.
        manifest_entry: Dictionary with document metadata. Expected keys:
            - domain (str): Document domain, e.g. "hr", "it", "governance"
            - document_type (str): e.g. "policy", "procedure", "agreement"
            - title (str, optional): Document title. Defaults to file stem.
            - raw_path (str, optional): Blob storage path. Defaults to str(file_path).
            - version, effective_date, expiry_date, author, source_url, tags (optional)

    Returns:
        An IngestedDocument populated with extracted text and metadata.
    """
    content = extract_text_from_pdf(file_path)
    doc_id = _generate_document_id(file_path)

    metadata = DocumentMetadata(
        domain=manifest_entry["domain"],
        document_type=manifest_entry["document_type"],
        version=manifest_entry.get("version"),
        effective_date=manifest_entry.get("effective_date"),
        expiry_date=manifest_entry.get("expiry_date"),
        author=manifest_entry.get("author"),
        source_url=manifest_entry.get("source_url"),
        tags=manifest_entry.get("tags", []),
    )

    return IngestedDocument(
        id=doc_id,
        source="pdf",
        title=manifest_entry.get("title", file_path.stem),
        content=content,
        metadata=metadata,
        raw_path=manifest_entry.get("raw_path", str(file_path)),
    )
