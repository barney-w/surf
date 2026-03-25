"""PDF analysis utilities — page counting and text extraction.

Provides tier-based routing for PDF attachments:
- Tier 1 (direct vision): PDFs <= MAX_DIRECT_PAGES sent as document content blocks
- Tier 2 (text extraction): Larger PDFs get text extracted and sent as text blocks
"""

import base64
import logging

import pymupdf

logger = logging.getLogger(__name__)

# Maximum raw (decoded) PDF size to prevent decompression bombs.
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB

# PDFs at or below this page count are sent directly to the LLM as document blocks.
MAX_DIRECT_PAGES = 30

# Approximate token budget for extracted text. One token ≈ 4 chars on average.
_MAX_TEXT_CHARS = 80_000 * 4  # ~80K tokens


def count_pages(data_b64: str) -> int:
    """Return the number of pages in a base64-encoded PDF."""
    raw = base64.b64decode(data_b64, validate=True)
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)}MB size limit")
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        return len(doc)


def extract_text(data_b64: str, *, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Extract plain text from a base64-encoded PDF, truncated to *max_chars*.

    Pages are extracted sequentially until the character budget is exhausted.
    A truncation notice is appended if the text was cut short.
    """
    raw = base64.b64decode(data_b64, validate=True)
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)}MB size limit")
    parts: list[str] = []
    total = 0
    truncated = False

    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            page_text = page.get_text()  # type: ignore[union-attr]
            if total + len(page_text) > max_chars:
                remaining = max_chars - total
                if remaining > 0:
                    parts.append(page_text[:remaining])
                truncated = True
                logger.info(
                    "PDF text extraction truncated at page %d/%d (%d chars)",
                    i + 1,
                    len(doc),
                    max_chars,
                )
                break
            parts.append(page_text)
            total += len(page_text)

    text = "\n".join(parts)
    if truncated:
        text += (
            "\n\n[Document truncated — only the first portion of this PDF is shown. "
            "Ask the user to specify page ranges if they need information from later sections.]"
        )
    return text
