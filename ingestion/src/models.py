from dataclasses import dataclass, field
from datetime import date


@dataclass
class DocumentMetadata:
    domain: str  # "hr", "it", "governance"
    document_type: str  # "policy", "procedure", "agreement"
    version: str | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    author: str | None = None
    source_url: str | None = None
    tags: list[str] = field(default_factory=lambda: [])


@dataclass
class IngestedDocument:
    id: str  # Deterministic hash of source + path
    source: str  # "pdf", "sharepoint", "objective"
    title: str
    content: str  # Extracted full text
    metadata: DocumentMetadata
    raw_path: str  # Blob storage path to original


@dataclass
class Chunk:
    id: str  # Hash of document_id + chunk_index
    document_id: str
    chunk_index: int
    content: str
    metadata: DocumentMetadata  # Inherited from parent document
    section_heading: str | None = None
    token_count: int = 0
    document_title: str = ""  # Carried from IngestedDocument.title for index citation
