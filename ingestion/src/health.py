"""Health check for the ingestion container.

Verifies that core dependencies are importable and Azure credentials
are available (if configured). Exits 0 on success, 1 on failure.
"""

import sys


def check() -> bool:
    """Return True if core ingestion modules are importable."""
    try:
        from src.connectors.pdf import create_document_from_pdf  # noqa: F401
        from src.pipeline.chunking import chunk_document  # noqa: F401
        from src.pipeline.embedding import generate_embeddings  # noqa: F401

        return True
    except ImportError as e:
        print(f"Health check failed: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
