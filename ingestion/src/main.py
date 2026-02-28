"""Surf document ingestion pipeline — Click CLI orchestration."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import click
from dotenv import load_dotenv

# Load .env from the repo root (one level up from the ingestion package) so
# Azure credentials are available without manually sourcing the file.
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

from src.connectors.pdf import create_document_from_pdf
from src.pipeline.chunking import ChunkingConfig, chunk_document
from src.pipeline.embedding import generate_embeddings
from src.pipeline.indexing import create_or_update_index, upload_chunks

if TYPE_CHECKING:
    from src.models import Chunk, IngestedDocument


def _discover_files(path: Path, source: str) -> list[Path]:
    """Find all ingestible files at *path* (single file or directory).

    Args:
        path: A file path or directory to scan.
        source: The source type (e.g. ``"pdf"``), used to filter by extension.

    Returns:
        Sorted list of matching file paths.
    """
    extension_map: dict[str, str] = {"pdf": ".pdf"}
    ext = extension_map.get(source, f".{source}")

    if path.is_file():
        if path.suffix.lower() == ext:
            return [path]
        return []

    return sorted(path.rglob(f"*{ext}"))


def _load_manifest(manifest_path: str | None) -> dict[str, dict]:
    """Load a manifest JSON file.

    The manifest maps file names to metadata overrides.  If no manifest is
    provided an empty dict is returned so callers can fall back to defaults.

    Args:
        manifest_path: Path to a JSON file, or ``None``.

    Returns:
        Dictionary keyed by file name.
    """
    if manifest_path is None:
        return {}
    with open(manifest_path) as fh:
        data = json.load(fh)
    # Support both list-of-dicts (keyed by "filename") and dict-keyed formats
    if isinstance(data, list):
        return {entry["filename"]: entry for entry in data}
    return data


def _build_manifest_entry(
    file_path: Path, domain: str, manifest: dict[str, dict]
) -> dict:
    """Return a manifest-style dict for *file_path*.

    If the file appears in *manifest* its entry is returned (with *domain*
    injected if missing).  Otherwise a sensible default is constructed.
    """
    entry = manifest.get(file_path.name, {})
    entry.setdefault("domain", domain)
    entry.setdefault("document_type", "policy")
    entry.setdefault("title", file_path.stem)
    return entry


def _parse_file(source: str, file_path: Path, manifest_entry: dict) -> IngestedDocument:
    """Parse a single file into an IngestedDocument.

    Args:
        source: Source type (``"pdf"``).
        file_path: Path to the file.
        manifest_entry: Metadata dict for the file.

    Returns:
        An IngestedDocument with extracted text and metadata.
    """
    if source == "pdf":
        return create_document_from_pdf(file_path, manifest_entry)
    msg = f"Unsupported source type: {source}"
    raise ValueError(msg)


def _chunks_to_dicts(
    chunks: list[Chunk], embeddings: list[list[float]]
) -> list[dict]:
    """Convert Chunk objects + embeddings into dicts suitable for indexing."""
    results: list[dict] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        doc: dict = {
            "id": chunk.id,
            "document_id": chunk.document_id,
            "domain": chunk.metadata.domain,
            "document_type": chunk.metadata.document_type,
            "title": chunk.document_title or chunk.section_heading or "",
            "section_heading": chunk.section_heading or "",
            "content": chunk.content,
            "content_vector": embedding,
            "chunk_index": chunk.chunk_index,
            "source_url": chunk.metadata.source_url or "",
            "effective_date": (
                chunk.metadata.effective_date
                if isinstance(chunk.metadata.effective_date, str)
                else chunk.metadata.effective_date.isoformat()
                if chunk.metadata.effective_date
                else None
            ),
            "metadata": json.dumps(asdict(chunk.metadata)),
        }
        results.append(doc)
    return results


def _validate_chunks(chunks: list, file_name: str) -> None:
    """Warn about chunks that show signs of poor splitting.

    Checks for:
    - Chunks shorter than 50 tokens (likely a stray heading or page artefact)
    - Chunks whose content ends with ':' or ',' (probable mid-sentence cut)
    - Chunks whose body starts with a lower-case letter (possible mid-sentence start)
    """
    for chunk in chunks:
        text = chunk.content.strip()
        if not text:
            continue
        if chunk.token_count < 50:
            click.echo(
                f"  WARN [{file_name}] chunk {chunk.chunk_index}: very short "
                f"({chunk.token_count} tokens): {text[:80]!r}",
                err=True,
            )
        if text[-1] in (":", ","):
            click.echo(
                f"  WARN [{file_name}] chunk {chunk.chunk_index}: ends mid-sentence "
                f"({text[-60:]!r})",
                err=True,
            )
        # Check body text (strip leading heading if present)
        body = text
        if chunk.section_heading and body.startswith(chunk.section_heading):
            body = body[len(chunk.section_heading) :].lstrip()
        if body and body[0].islower():
            click.echo(
                f"  WARN [{file_name}] chunk {chunk.chunk_index}: may start "
                f"mid-sentence ({body[:80]!r})",
                err=True,
            )


def _resolve_index_name(explicit: str | None = None) -> str:
    """Resolve the search index name from args/env with stable precedence."""
    if explicit:
        return explicit
    return (
        os.environ.get("AZURE_SEARCH_INDEX_NAME")
        or os.environ.get("AZURE_SEARCH_INDEX")
        or "surf-index"
    )


async def _embed_and_index(chunks: list[Chunk], embed_batch_size: int = 16) -> int:
    """Generate embeddings and upload chunks to the search index.

    Returns:
        Number of successfully uploaded documents.
    """
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from openai import AzureOpenAI

    credential = DefaultAzureCredential()

    openai_client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        azure_ad_token_provider=lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
        api_version="2024-02-01",
    )

    texts = [c.content for c in chunks]

    def _report(batch_num: int, total_batches: int) -> None:
        click.echo(f"  Embedding batch {batch_num}/{total_batches}...")

    embeddings = await generate_embeddings(
        texts, openai_client, batch_size=embed_batch_size, progress_callback=_report
    )

    search_client = SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=_resolve_index_name(),
        credential=credential,
    )

    chunk_dicts = _chunks_to_dicts(chunks, embeddings)
    return await upload_chunks(search_client, chunk_dicts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """Surf document ingestion pipeline."""


@cli.command()
@click.option("--source", type=click.Choice(["pdf"]), required=True)
@click.option("--path", type=click.Path(exists=True), required=True)
@click.option("--domain", required=True, help="Document domain (hr, it, governance)")
@click.option(
    "--manifest", type=click.Path(exists=True), default=None, help="Manifest JSON file"
)
@click.option("--dry-run", is_flag=True, help="Parse and chunk only, don't embed or index")
@click.option(
    "--chunk-size",
    default=700,
    show_default=True,
    help="Maximum tokens per chunk.",
)
@click.option(
    "--overlap",
    default=150,
    show_default=True,
    help="Token overlap between consecutive chunks.",
)
@click.option(
    "--embed-batch-size",
    default=16,
    show_default=True,
    help="Number of chunks per embedding API call.",
)
def ingest(
    source: str,
    path: str,
    domain: str,
    manifest: str | None,
    dry_run: bool,
    chunk_size: int,
    overlap: int,
    embed_batch_size: int,
) -> None:
    """Ingest documents into the knowledge base."""
    target = Path(path)
    config = ChunkingConfig(max_chunk_tokens=chunk_size, overlap_tokens=overlap)

    # 1. Load manifest (if provided)
    manifest_data = _load_manifest(manifest)

    # 2. Find all files at path (single file or directory)
    files = _discover_files(target, source)
    if not files:
        click.echo(f"No {source} files found at {target}")
        sys.exit(1)

    click.echo(f"Found {len(files)} file(s) to process.")

    # 3-4. For each file: parse -> create IngestedDocument -> chunk
    documents: list[IngestedDocument] = []
    all_chunks: list[Chunk] = []
    errors: list[str] = []

    for file_path in files:
        try:
            entry = _build_manifest_entry(file_path, domain, manifest_data)
            doc = _parse_file(source, file_path, entry)
            documents.append(doc)

            chunks = chunk_document(doc, config)
            _validate_chunks(chunks, file_path.name)
            all_chunks.extend(chunks)
            click.echo(f"  Parsed {file_path.name}: {len(chunks)} chunk(s)")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{file_path.name}: {exc}")
            click.echo(f"  ERROR {file_path.name}: {exc}", err=True)

    # 5-6. Generate embeddings and upload (unless dry_run)
    if not dry_run and all_chunks:
        try:
            click.echo(f"Generating embeddings ({embed_batch_size} chunks/batch) and uploading to index...")
            uploaded = asyncio.run(_embed_and_index(all_chunks, embed_batch_size=embed_batch_size))
            click.echo(f"Indexed {uploaded} chunk(s).")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Embedding/indexing: {exc}")
            click.echo(f"  ERROR Embedding/indexing failed: {exc}", err=True)
    elif dry_run:
        click.echo("Dry-run mode: skipping embedding and indexing.")

    # 7. Print summary
    click.echo("\n--- Ingestion Summary ---")
    click.echo(f"Files processed : {len(documents)}")
    click.echo(f"Chunks created  : {len(all_chunks)}")
    click.echo(f"Errors          : {len(errors)}")
    if errors:
        click.echo("\nErrors:")
        for err in errors:
            click.echo(f"  - {err}")


@cli.command()
@click.option(
    "--index-name",
    default=None,
    help="Override index name (defaults to AZURE_SEARCH_INDEX_NAME/AZURE_SEARCH_INDEX/surf-index).",
)
def init_index(index_name: str | None) -> None:
    """Create or update the Azure AI Search index schema."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexClient

        credential = DefaultAzureCredential()
        index_client = SearchIndexClient(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            credential=credential,
        )
        resolved_index_name = _resolve_index_name(index_name)
        create_or_update_index(index_client, resolved_index_name)
        click.echo(f"Index ready: {resolved_index_name}")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Failed to initialize index: {exc}", err=True)
        sys.exit(1)


@cli.command()
def status() -> None:
    """Show index statistics."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexClient

        credential = DefaultAzureCredential()
        index_client = SearchIndexClient(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            credential=credential,
        )
        index_name = _resolve_index_name()
        stats = index_client.get_index_statistics(index_name)
        click.echo("Index statistics:")
        click.echo(f"  Index name     : {index_name}")
        click.echo(f"  Document count : {stats.get('document_count', 'N/A')}")
        click.echo(f"  Storage size   : {stats.get('storage_size', 'N/A')} bytes")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Failed to retrieve index stats: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--domain", required=True)
def reindex(domain: str) -> None:
    """Re-index all documents for a domain."""
    click.echo(f"Re-indexing domain: {domain}")
    click.echo("Not yet implemented -- requires document store integration.")


if __name__ == "__main__":
    cli()
