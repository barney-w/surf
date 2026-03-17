"""Website crawler — sitemap-driven content extraction for public websites."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, Tag  # pyright: ignore[reportMissingTypeStubs]

from src.connectors.pdf import extract_text_from_pdf
from src.models import DocumentMetadata, IngestedDocument

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Section-to-domain mapping: longest-prefix-match maps URL paths to domains.
SECTION_TO_DOMAIN: dict[str, str] = {
    "Services/Rates-water": "services",
    "Services/Waste-recycling": "services",
    "Services/Water-sewerage": "services",
    "Services/Roads-transport-parking": "services",
    "Services/Animals-pets": "services",
    "Services/Supporting-our-community": "services",
    "Services/Neighbourhood-issues": "services",
    "Services/Venues-facilities": "services",
    "Services/Safety-security": "services",
    "Services/Report-pay-apply": "services",
    "Services/Emergencies-disasters": "services",
    "Planning-building": "planning",
    "Environment-sustainability": "environment",
    "Invest-do-business": "business",
    "Council": "governance",
    "About-our-city": "about",
    "Things-to-do": "lifestyle",
}


@dataclass
class CrawlConfig:
    """Website crawl configuration. Load from environment via ``from_env()``."""

    base_url: str
    sitemap_url: str = ""  # Defaults to {base_url}/sitemap.xml
    crawl_delay_ms: int = 500
    max_concurrent: int = 5
    content_selector: str = "main, .content, article, #content"
    exclude_patterns: list[str] = field(default_factory=list)
    max_retries: int = 3
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.sitemap_url:
            self.sitemap_url = f"{self.base_url}/sitemap.xml"

    @classmethod
    def from_env(cls) -> CrawlConfig:
        """Build config from WEBSITE_* environment variables."""
        import os

        base_url = os.environ.get("WEBSITE_BASE_URL", "")
        if not base_url:
            raise ValueError("WEBSITE_BASE_URL environment variable is required")

        exclude_raw = os.environ.get("WEBSITE_EXCLUDE_PATTERNS", "")
        exclude = [p.strip() for p in exclude_raw.split(",") if p.strip()] if exclude_raw else []

        return cls(
            base_url=base_url,
            sitemap_url=os.environ.get("WEBSITE_SITEMAP_URL", ""),
            crawl_delay_ms=int(os.environ.get("WEBSITE_CRAWL_DELAY_MS", "500")),
            max_concurrent=int(os.environ.get("WEBSITE_MAX_CONCURRENT", "5")),
            content_selector=os.environ.get(
                "WEBSITE_CONTENT_SELECTOR", "main, .content, article, #content"
            ),
            exclude_patterns=exclude,
            max_retries=int(os.environ.get("WEBSITE_MAX_RETRIES", "3")),
            timeout_seconds=int(os.environ.get("WEBSITE_TIMEOUT_SECONDS", "30")),
        )


@dataclass
class CrawledPage:
    """A single crawled HTML page."""

    url: str
    title: str
    section_path: str  # e.g. "Services/Waste-recycling/Residential-bins"
    content_text: str  # Cleaned extracted text
    content_html: str  # Cleaned HTML (for archival)
    content_hash: str  # SHA256 of content_text
    last_modified: str | None
    pdf_links: list[str] = field(default_factory=list)
    crawl_timestamp: str = ""


@dataclass
class CrawledPDF:
    """A unique PDF discovered during crawl."""

    url: str
    filename: str
    title: str  # From link text or filename
    source_pages: list[str]  # URLs of pages linking to this PDF
    section_path: str  # Derived from primary source page
    content_hash: str  # SHA256 of binary content
    size_bytes: int


@dataclass
class CrawlResult:
    """Summary of a crawl run."""

    pages_crawled: int = 0
    pages_skipped: int = 0
    pdfs_discovered: int = 0
    pdfs_downloaded: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ManifestEntry:
    """A single entry in the crawl manifest."""

    content_hash: str
    title: str
    last_crawled: str  # ISO 8601


@dataclass
class CrawlManifest:
    """Tracks what was crawled on the previous run for change detection."""

    crawl_timestamp: str
    base_url: str
    pages: dict[str, ManifestEntry] = field(default_factory=dict)  # keyed by section_path
    pdfs: dict[str, ManifestEntry] = field(default_factory=dict)  # keyed by URL path

    def to_json(self) -> str:
        """Serialise to JSON string."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> CrawlManifest:
        """Deserialise from JSON string."""
        raw: dict[str, Any] = json.loads(data)
        pages = {k: ManifestEntry(**v) for k, v in raw.get("pages", {}).items()}
        pdfs = {k: ManifestEntry(**v) for k, v in raw.get("pdfs", {}).items()}
        return cls(
            crawl_timestamp=raw["crawl_timestamp"],
            base_url=raw["base_url"],
            pages=pages,
            pdfs=pdfs,
        )

    def diff_pages(
        self, current_pages: list[CrawledPage]
    ) -> tuple[list[CrawledPage], list[CrawledPage], list[str]]:
        """Compare current crawl against manifest.

        Returns:
            (new_pages, changed_pages, removed_section_paths)
        """
        new: list[CrawledPage] = []
        changed: list[CrawledPage] = []
        seen_paths: set[str] = set()

        for page in current_pages:
            seen_paths.add(page.section_path)
            existing = self.pages.get(page.section_path)
            if existing is None:
                new.append(page)
            elif existing.content_hash != page.content_hash:
                changed.append(page)

        removed = [sp for sp in self.pages if sp not in seen_paths]
        return (new, changed, removed)

    def diff_pdfs(
        self, current_pdfs: list[CrawledPDF]
    ) -> tuple[list[CrawledPDF], list[CrawledPDF], list[str]]:
        """Compare current PDFs against manifest.

        Returns:
            (new_pdfs, changed_pdfs, removed_url_paths)
        """
        new: list[CrawledPDF] = []
        changed: list[CrawledPDF] = []
        seen_urls: set[str] = set()

        for pdf in current_pdfs:
            url_path = urlparse(pdf.url).path
            seen_urls.add(url_path)
            existing = self.pdfs.get(url_path)
            if existing is None:
                new.append(pdf)
            elif existing.content_hash != pdf.content_hash:
                changed.append(pdf)

        removed = [up for up in self.pdfs if up not in seen_urls]
        return (new, changed, removed)

    def update(self, pages: list[CrawledPage], pdfs: list[CrawledPDF]) -> None:
        """Update manifest entries from a completed crawl."""
        now = datetime.now(UTC).isoformat()
        self.crawl_timestamp = now

        for page in pages:
            self.pages[page.section_path] = ManifestEntry(
                content_hash=page.content_hash,
                title=page.title,
                last_crawled=now,
            )

        for pdf in pdfs:
            url_path = urlparse(pdf.url).path
            self.pdfs[url_path] = ManifestEntry(
                content_hash=pdf.content_hash,
                title=pdf.title,
                last_crawled=now,
            )


async def load_manifest_from_blob(
    container_client: Any, blob_name: str = "website/crawl-manifest.json"
) -> CrawlManifest | None:
    """Load the previous crawl manifest from blob storage. Returns None if not found."""
    from azure.core.exceptions import ResourceNotFoundError

    blob_client = container_client.get_blob_client(blob_name)
    try:
        download = await blob_client.download_blob()
        data = await download.readall()
        return CrawlManifest.from_json(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ResourceNotFoundError:
        return None


async def save_manifest_to_blob(
    manifest: CrawlManifest,
    container_client: Any,
    blob_name: str = "website/crawl-manifest.json",
) -> None:
    """Save the crawl manifest to blob storage."""
    blob_client = container_client.get_blob_client(blob_name)
    await blob_client.upload_blob(manifest.to_json(), overwrite=True)


async def delete_website_documents(
    search_client: Any, section_paths: list[str]
) -> int:
    """Delete all chunks for website documents matching the given section paths.

    For each section_path, computes the document_id via generate_website_doc_id()
    and deletes all chunks with that document_id from the index.

    Returns count of deleted documents.
    """
    total_deleted = 0
    for section_path in section_paths:
        doc_id = generate_website_doc_id(section_path)
        results = search_client.search(
            search_text="*",
            filter=f"document_id eq '{doc_id}'",
            select=["id"],
        )
        chunk_ids: list[str] = [r["id"] for r in results]  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        if chunk_ids:
            search_client.delete_documents(
                documents=[{"id": cid} for cid in chunk_ids]
            )
            total_deleted += len(chunk_ids)
            logger.info(
                "Deleted %d chunk(s) for document %s (section: %s)",
                len(chunk_ids),
                doc_id,
                section_path,
            )
    return total_deleted


def url_to_section_path(url: str, base_url: str) -> str:
    """Extract the section path from a full URL relative to the base.

    Example: "https://example.com/Services/Waste-recycling/Bins"
             with base "https://example.com"
             returns "Services/Waste-recycling/Bins"
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    base_parsed = urlparse(base_url)
    base_path = base_parsed.path.strip("/")
    if base_path and path.startswith(base_path):
        path = path[len(base_path) :].lstrip("/")
    return path


def url_to_domain(section_path: str) -> str:
    """Map a section path to a domain using longest-prefix match."""
    for prefix, domain in sorted(SECTION_TO_DOMAIN.items(), key=lambda x: -len(x[0])):
        if section_path.startswith(prefix):
            return domain
    return "general"


def section_path_to_tags(section_path: str) -> list[str]:
    """Convert a section path to a list of lowercase tags.

    "Services/Waste-recycling/Residential-bins"
    → ["services", "waste-recycling", "residential-bins"]
    """
    if not section_path:
        return []
    return [segment.lower() for segment in section_path.split("/") if segment]


def generate_website_doc_id(url_path: str) -> str:
    """Generate a deterministic document ID from a URL path."""
    return hashlib.sha256(f"website:{url_path}".encode()).hexdigest()[:16]


def crawled_page_to_document(page: CrawledPage, base_url: str) -> IngestedDocument:
    """Convert a CrawledPage to an IngestedDocument for the pipeline.

    Sets:
    - id: deterministic from URL path via generate_website_doc_id()
    - source: "website"
    - title: page.title
    - content: page.content_text
    - metadata.domain: derived via url_to_domain(page.section_path)
    - metadata.document_type: "web-page"
    - metadata.content_source: "website"
    - metadata.section_path: page.section_path
    - metadata.source_url: page.url
    - metadata.tags: section_path_to_tags(page.section_path)
    - raw_path: "" (no blob storage path for pages)
    """
    url_path = url_to_section_path(page.url, base_url)
    doc_id = generate_website_doc_id(url_path)
    domain = url_to_domain(page.section_path)
    tags = section_path_to_tags(page.section_path)

    metadata = DocumentMetadata(
        domain=domain,
        document_type="web-page",
        content_source="website",
        section_path=page.section_path,
        source_url=page.url,
        tags=tags,
    )

    return IngestedDocument(
        id=doc_id,
        source="website",
        title=page.title,
        content=page.content_text,
        metadata=metadata,
        raw_path="",
    )


def crawled_pdf_to_document(
    pdf: CrawledPDF, pdf_content: bytes, temp_dir: Path
) -> IngestedDocument:
    """Convert a CrawledPDF to an IngestedDocument.

    Writes the PDF bytes to a temp file, extracts text via extract_text_from_pdf(),
    then builds the IngestedDocument.

    Sets:
    - id: deterministic from PDF URL path via generate_website_doc_id()
    - source: "website"
    - title: pdf.title
    - content: extracted text from PDF
    - metadata.domain: derived via url_to_domain(pdf.section_path)
    - metadata.document_type: "web-pdf"
    - metadata.content_source: "website"
    - metadata.section_path: pdf.section_path
    - metadata.source_url: pdf.url
    - metadata.tags: section_path_to_tags(pdf.section_path) + source page tags
    - raw_path: "" (no blob storage path yet)
    """
    temp_path = temp_dir / pdf.filename
    try:
        temp_path.write_bytes(pdf_content)
        content = extract_text_from_pdf(temp_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    parsed_url = urlparse(pdf.url)
    url_path = parsed_url.path.strip("/")
    doc_id = generate_website_doc_id(url_path)
    domain = url_to_domain(pdf.section_path)

    # Combine section path tags with source page tags (last path segment of each URL)
    tags = section_path_to_tags(pdf.section_path)
    for source_url in pdf.source_pages:
        parsed_source = urlparse(source_url)
        source_path = parsed_source.path.strip("/")
        if source_path:
            last_segment = source_path.rsplit("/", maxsplit=1)[-1].lower()
            if last_segment and last_segment not in tags:
                tags.append(last_segment)

    metadata = DocumentMetadata(
        domain=domain,
        document_type="web-pdf",
        content_source="website",
        section_path=pdf.section_path,
        source_url=pdf.url,
        tags=tags,
    )

    return IngestedDocument(
        id=doc_id,
        source="website",
        title=pdf.title,
        content=content,
        metadata=metadata,
        raw_path="",
    )


# ---------------------------------------------------------------------------
# HTML content extraction
# ---------------------------------------------------------------------------

_STRIP_ELEMENTS = {"nav", "header", "footer", "script", "style", "noscript", "iframe", "svg"}


def extract_page_content(html: str, content_selector: str) -> tuple[str, str, str, list[str]]:
    """Extract clean text, cleaned HTML, title, and PDF links from raw HTML.

    Args:
        html: Raw HTML string.
        content_selector: CSS selector for the main content area.

    Returns:
        Tuple of (text_content, cleaned_html, page_title, pdf_links).
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract title
    title_tag = soup.find("title")
    page_title: str = ""
    if isinstance(title_tag, Tag):
        page_title = title_tag.get_text(strip=True)

    # Find main content element via comma-separated selectors
    content_element: Tag | BeautifulSoup | None = None
    for selector in content_selector.split(","):
        selector = selector.strip()
        if selector:
            content_element = soup.select_one(selector)
            if content_element is not None:
                break

    if content_element is None:
        body = soup.body
        content_element = body if body is not None else soup

    # Strip unwanted elements
    for tag_name in _STRIP_ELEMENTS:
        for el in content_element.find_all(tag_name):  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(el, Tag):
                el.decompose()

    # Collect PDF links (case-insensitive .pdf suffix)
    pdf_links: list[str] = []
    for a_tag in content_element.find_all("a", href=True):  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        if isinstance(a_tag, Tag):
            href: object = a_tag.get("href", "")  # pyright: ignore[reportUnknownMemberType]
            if isinstance(href, str) and href.lower().endswith(".pdf"):
                pdf_links.append(href)

    # Extract cleaned text and HTML
    # NOTE: bs4 stubs are incomplete — get_text returns str at runtime.
    text_content = str(content_element.get_text(separator="\n", strip=True))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    cleaned_html = str(content_element)  # pyright: ignore[reportUnknownArgumentType]

    return (text_content, cleaned_html, page_title, pdf_links)


# ---------------------------------------------------------------------------
# WebsiteCrawler
# ---------------------------------------------------------------------------

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class WebsiteCrawler:
    """Sitemap-driven website crawler with rate limiting and concurrency control."""

    def __init__(self, config: CrawlConfig) -> None:
        self._config = config
        self._http: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None

    async def _ensure_http(self) -> httpx.AsyncClient:
        """Lazy-init the HTTP client."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout_seconds),
                follow_redirects=True,
                headers={"User-Agent": "SurfBot/1.0 (knowledge-indexer)"},
            )
            self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
        return self._http

    async def _get(self, url: str) -> httpx.Response:
        """GET with semaphore rate limiting and retry."""
        client = await self._ensure_http()
        assert self._semaphore is not None  # noqa: S101

        for attempt in range(self._config.max_retries + 1):
            async with self._semaphore:
                await asyncio.sleep(self._config.crawl_delay_ms / 1000)
                resp = await client.get(url)

            if resp.status_code in {429, 503}:
                if attempt >= self._config.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "")
                wait = (
                    int(retry_after)
                    if retry_after.isdigit()
                    else min(2**attempt, 60)
                )
                logger.warning(
                    "HTTP %s for %s — retrying in %ss (attempt %s/%s)",
                    resp.status_code,
                    url,
                    wait,
                    attempt + 1,
                    self._config.max_retries,
                )
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        # Should be unreachable, but satisfies type checker
        msg = f"Exhausted retries for {url}"
        raise httpx.HTTPError(msg)

    async def parse_sitemap(self) -> list[str]:
        """Fetch and parse sitemap.xml, returning a list of page URLs.

        Handles both sitemap index files and regular sitemaps.
        Filters URLs through exclude_patterns.
        """
        urls = await self._parse_sitemap_url(self._config.sitemap_url)

        # Filter out excluded patterns
        if self._config.exclude_patterns:
            compiled = [re.compile(p) for p in self._config.exclude_patterns]
            urls = [u for u in urls if not any(rx.search(u) for rx in compiled)]

        return sorted(set(urls))

    async def _parse_sitemap_url(self, sitemap_url: str) -> list[str]:
        """Recursively parse a single sitemap URL."""
        resp = await self._get(sitemap_url)
        root = ElementTree.fromstring(resp.text)  # noqa: S314

        # Check if this is a sitemap index
        sitemap_tags = root.findall(f"{_SITEMAP_NS}sitemap")
        if sitemap_tags:
            all_urls: list[str] = []
            for sm in sitemap_tags:
                loc = sm.find(f"{_SITEMAP_NS}loc")
                if loc is not None and loc.text:
                    child_urls = await self._parse_sitemap_url(loc.text.strip())
                    all_urls.extend(child_urls)
            return all_urls

        # Regular sitemap — collect <url><loc> values
        urls: list[str] = []
        for url_el in root.findall(f"{_SITEMAP_NS}url"):
            loc = url_el.find(f"{_SITEMAP_NS}loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())
        return urls

    async def crawl_page(self, url: str) -> CrawledPage | None:
        """Crawl a single page, returning a CrawledPage or None on failure."""
        try:
            resp = await self._get(url)
        except httpx.HTTPError as exc:
            logger.warning("Failed to crawl %s: %s", url, exc)
            return None

        text, html_content, title, pdf_links = extract_page_content(
            resp.text, self._config.content_selector
        )

        # Resolve relative PDF links to absolute
        pdf_links = [urljoin(url, link) for link in pdf_links]

        section_path = url_to_section_path(url, self._config.base_url)
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        crawl_ts = datetime.now(UTC).isoformat()

        last_modified = resp.headers.get("Last-Modified")

        return CrawledPage(
            url=url,
            title=title,
            section_path=section_path,
            content_text=text,
            content_html=html_content,
            content_hash=content_hash,
            last_modified=last_modified,
            pdf_links=pdf_links,
            crawl_timestamp=crawl_ts,
        )

    async def download_pdf(self, url: str) -> tuple[bytes, str] | None:
        """Download a PDF, returning (content_bytes, content_hash) or None on failure."""
        try:
            resp = await self._get(url)
        except httpx.HTTPError as exc:
            logger.warning("Failed to download PDF %s: %s", url, exc)
            return None

        content_bytes = resp.content
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        return (content_bytes, content_hash)

    async def crawl(
        self, *, pages_only: bool = False
    ) -> tuple[list[CrawledPage], list[CrawledPDF], CrawlResult]:
        """Run the full crawl pipeline.

        1. Parse sitemap to get URLs.
        2. Crawl all pages concurrently (semaphore-limited).
        3. Collect and deduplicate PDF links across all pages.
        4. If not pages_only, download all unique PDFs.
        5. Return (pages, pdfs, result).
        """
        result = CrawlResult()

        # 1. Parse sitemap
        try:
            page_urls = await self.parse_sitemap()
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            result.errors.append(f"Sitemap parse failed: {exc}")
            return ([], [], result)

        logger.info("Sitemap yielded %d URLs to crawl", len(page_urls))

        # 2. Crawl all pages concurrently
        tasks = [self.crawl_page(url) for url in page_urls]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        pages: list[CrawledPage] = []
        for i, raw in enumerate(raw_results):
            if isinstance(raw, BaseException):
                result.errors.append(f"Error crawling {page_urls[i]}: {raw}")
                result.pages_skipped += 1
            elif raw is None:
                result.pages_skipped += 1
            else:
                pages.append(raw)
                result.pages_crawled += 1

        # 3. Collect and deduplicate PDF links
        pdf_map: dict[str, list[str]] = {}  # url -> list of source page URLs
        pdf_first_section: dict[str, str] = {}  # url -> section_path from first page
        for page in pages:
            for pdf_url in page.pdf_links:
                if pdf_url not in pdf_map:
                    pdf_map[pdf_url] = []
                    pdf_first_section[pdf_url] = page.section_path
                pdf_map[pdf_url].append(page.url)

        result.pdfs_discovered = len(pdf_map)

        # 4. Download PDFs
        pdfs: list[CrawledPDF] = []
        if not pages_only and pdf_map:
            logger.info("Downloading %d unique PDFs", len(pdf_map))
            pdf_urls = list(pdf_map.keys())
            dl_tasks = [self.download_pdf(u) for u in pdf_urls]
            dl_results = await asyncio.gather(*dl_tasks, return_exceptions=True)

            for pdf_url, dl_result in zip(pdf_urls, dl_results, strict=True):
                if isinstance(dl_result, BaseException):
                    result.errors.append(f"PDF download error {pdf_url}: {dl_result}")
                    continue
                if dl_result is None:
                    continue

                content_bytes, content_hash = dl_result
                parsed = urlparse(pdf_url)
                filename = parsed.path.rsplit("/", maxsplit=1)[-1] or "unknown.pdf"

                pdfs.append(
                    CrawledPDF(
                        url=pdf_url,
                        filename=filename,
                        title=filename.removesuffix(".pdf").replace("-", " ").replace("_", " "),
                        source_pages=pdf_map[pdf_url],
                        section_path=pdf_first_section[pdf_url],
                        content_hash=content_hash,
                        size_bytes=len(content_bytes),
                    )
                )
                result.pdfs_downloaded += 1

        logger.info(
            "Crawl complete: %d pages, %d PDFs (%d errors)",
            result.pages_crawled,
            result.pdfs_downloaded,
            len(result.errors),
        )
        return (pages, pdfs, result)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
