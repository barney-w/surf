"""Metadata utilities — manifest loading and helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path


def load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    """Load a document manifest JSON file.

    The manifest is expected to be a JSON file containing a list of objects,
    each describing a document to ingest (path, domain, document_type, etc.).

    Args:
        manifest_path: Path to the manifest JSON file.

    Returns:
        A list of document descriptor dicts.

    Raises:
        FileNotFoundError: If *manifest_path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        TypeError: If the top-level JSON value is not a list.
    """
    raw = manifest_path.read_text(encoding="utf-8")
    data: Any = json.loads(raw)
    if not isinstance(data, list):
        msg = f"Manifest must be a JSON array, got {type(data).__name__}"
        raise TypeError(msg)
    return cast("list[dict[str, Any]]", data)
