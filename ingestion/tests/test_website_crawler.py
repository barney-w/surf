"""Tests for the website crawler connector."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest

from src.connectors.website_crawler import (
    CrawlConfig,
    CrawledPage,
    CrawledPDF,
    CrawlManifest,
    ManifestEntry,
    WebsiteCrawler,
    crawled_page_to_document,
    crawled_pdf_to_document,
    extract_page_content,
    generate_website_doc_id,
    load_manifest_from_blob,
    save_manifest_to_blob,
    section_path_to_tags,
    url_to_domain,
    url_to_section_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> CrawlConfig:
    """Factory with sensible defaults for testing."""
    defaults: dict[str, Any] = {
        "base_url": "https://example.com",
        "sitemap_url": "https://example.com/sitemap.xml",
        "crawl_delay_ms": 0,  # No delay in tests
        "max_concurrent": 5,
        "content_selector": "main, .content, article",
        "exclude_patterns": [],
        "max_retries": 1,
        "timeout_seconds": 5,
    }
    defaults.update(overrides)
    return CrawlConfig(**defaults)


def _make_crawler(config: CrawlConfig | None = None) -> WebsiteCrawler:
    """Build a WebsiteCrawler with a mocked HTTP client."""
    crawler = WebsiteCrawler(config or _make_config())
    crawler._http = MagicMock(spec=httpx.AsyncClient)  # noqa: SLF001
    crawler._semaphore = asyncio.Semaphore(5)  # noqa: SLF001
    return crawler


def _mock_response(
    *,
    status_code: int = 200,
    text: str = "",
    content: bytes = b"",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a fake httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.content = content
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# CrawlConfig tests
# ---------------------------------------------------------------------------


class TestCrawlConfig:
    """Tests for CrawlConfig dataclass and from_env()."""

    def test_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All WEBSITE_* env vars are loaded into the config."""
        monkeypatch.setenv("WEBSITE_BASE_URL", "https://example.com")
        monkeypatch.setenv("WEBSITE_SITEMAP_URL", "https://example.com/custom-sitemap.xml")
        monkeypatch.setenv("WEBSITE_CRAWL_DELAY_MS", "100")
        monkeypatch.setenv("WEBSITE_MAX_CONCURRENT", "10")
        monkeypatch.setenv("WEBSITE_CONTENT_SELECTOR", "div.main")
        monkeypatch.setenv("WEBSITE_EXCLUDE_PATTERNS", "/private/.*,/admin/.*")
        monkeypatch.setenv("WEBSITE_MAX_RETRIES", "5")
        monkeypatch.setenv("WEBSITE_TIMEOUT_SECONDS", "60")

        cfg = CrawlConfig.from_env()
        assert cfg.base_url == "https://example.com"
        assert cfg.sitemap_url == "https://example.com/custom-sitemap.xml"
        assert cfg.crawl_delay_ms == 100
        assert cfg.max_concurrent == 10
        assert cfg.content_selector == "div.main"
        assert cfg.exclude_patterns == ["/private/.*", "/admin/.*"]
        assert cfg.max_retries == 5
        assert cfg.timeout_seconds == 60

    def test_config_from_env_missing_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises ValueError when WEBSITE_BASE_URL is not set."""
        monkeypatch.delenv("WEBSITE_BASE_URL", raising=False)
        with pytest.raises(ValueError, match="WEBSITE_BASE_URL"):
            CrawlConfig.from_env()

    def test_config_default_sitemap(self) -> None:
        """Sitemap URL defaults to {base_url}/sitemap.xml when not provided."""
        cfg = CrawlConfig(base_url="https://example.com")
        assert cfg.sitemap_url == "https://example.com/sitemap.xml"

    def test_config_strips_trailing_slash(self) -> None:
        """Trailing slash on base_url is stripped during __post_init__."""
        cfg = CrawlConfig(base_url="https://example.com/")
        assert cfg.base_url == "https://example.com"
        # Sitemap should also use the stripped URL
        assert cfg.sitemap_url == "https://example.com/sitemap.xml"


# ---------------------------------------------------------------------------
# URL helper tests
# ---------------------------------------------------------------------------


class TestUrlHelpers:
    """Tests for url_to_section_path, url_to_domain, section_path_to_tags, doc ID."""

    def test_url_to_section_path_basic(self) -> None:
        """Standard URL extraction returns the path after the base."""
        result = url_to_section_path(
            "https://example.com/Services/Waste-recycling/Bins",
            "https://example.com",
        )
        assert result == "Services/Waste-recycling/Bins"

    def test_url_to_section_path_with_base_path(self) -> None:
        """When base URL has a path prefix, it is stripped from the result."""
        result = url_to_section_path(
            "https://example.com/city/Services/Water",
            "https://example.com/city",
        )
        assert result == "Services/Water"

    def test_url_to_section_path_root(self) -> None:
        """Root URL returns an empty string."""
        result = url_to_section_path("https://example.com/", "https://example.com")
        assert result == ""

    def test_url_to_domain_services(self) -> None:
        """Various Services/* paths map to 'services' domain."""
        assert url_to_domain("Services/Waste-recycling/Bins") == "services"
        assert url_to_domain("Services/Water-sewerage") == "services"
        assert url_to_domain("Services/Roads-transport-parking/Roadworks") == "services"
        assert url_to_domain("Services/Animals-pets") == "services"

    def test_url_to_domain_planning(self) -> None:
        """Planning-building/* paths map to 'planning' domain."""
        assert url_to_domain("Planning-building") == "planning"
        assert url_to_domain("Planning-building/Development-applications") == "planning"

    def test_url_to_domain_unknown(self) -> None:
        """Unrecognised path falls back to 'general'."""
        assert url_to_domain("Some-random/Path") == "general"
        assert url_to_domain("") == "general"

    def test_section_path_to_tags(self) -> None:
        """Path segments are lowercased and returned as a tag list."""
        tags = section_path_to_tags("Services/Waste-recycling/Residential-bins")
        assert tags == ["services", "waste-recycling", "residential-bins"]

    def test_section_path_to_tags_empty(self) -> None:
        """Empty section path returns an empty list."""
        assert section_path_to_tags("") == []

    def test_generate_website_doc_id_deterministic(self) -> None:
        """Same input always produces the same document ID."""
        id1 = generate_website_doc_id("Services/Waste")
        id2 = generate_website_doc_id("Services/Waste")
        assert id1 == id2
        assert len(id1) == 16

    def test_generate_website_doc_id_differs_for_different_paths(self) -> None:
        """Different paths produce different IDs."""
        id1 = generate_website_doc_id("Services/Waste")
        id2 = generate_website_doc_id("Services/Water")
        assert id1 != id2


# ---------------------------------------------------------------------------
# HTML extraction tests
# ---------------------------------------------------------------------------


class TestHtmlExtraction:
    """Tests for extract_page_content()."""

    def test_extract_strips_nav_footer_script(self) -> None:
        """Nav, footer, and script elements are removed from extracted content."""
        html = """<html><body><main>
            <nav>Navigation</nav>
            <p>Main content</p>
            <footer>Footer info</footer>
            <script>alert('x')</script>
        </main></body></html>"""
        text, _, _, _ = extract_page_content(html, "main")
        assert "Navigation" not in text
        assert "Footer info" not in text
        assert "alert" not in text
        assert "Main content" in text

    def test_extract_page_title(self) -> None:
        """Page title is extracted from the <title> tag."""
        html = "<html><head><title>My Page Title</title></head><body><main>Hi</main></body></html>"
        _, _, title, _ = extract_page_content(html, "main")
        assert title == "My Page Title"

    def test_extract_pdf_links(self) -> None:
        """Anchor tags with .pdf hrefs are collected."""
        html = """<html><body><main>
            <a href="/docs/report.pdf">Report</a>
            <a href="/docs/other.docx">Other</a>
            <a href="/docs/plan.pdf">Plan</a>
        </main></body></html>"""
        _, _, _, pdf_links = extract_page_content(html, "main")
        assert pdf_links == ["/docs/report.pdf", "/docs/plan.pdf"]

    def test_extract_pdf_links_case_insensitive(self) -> None:
        """.PDF and .Pdf extensions are both detected."""
        html = """<html><body><main>
            <a href="/docs/UPPERCASE.PDF">Upper</a>
            <a href="/docs/Mixed.Pdf">Mixed</a>
        </main></body></html>"""
        _, _, _, pdf_links = extract_page_content(html, "main")
        assert len(pdf_links) == 2
        assert "/docs/UPPERCASE.PDF" in pdf_links
        assert "/docs/Mixed.Pdf" in pdf_links

    def test_extract_content_text(self) -> None:
        """Extracted text is clean with no HTML tags."""
        html = """<html><body><main>
            <h1>Welcome</h1>
            <p>This is <strong>important</strong> content.</p>
        </main></body></html>"""
        text, _, _, _ = extract_page_content(html, "main")
        assert "Welcome" in text
        assert "important" in text
        assert "<strong>" not in text
        assert "<p>" not in text

    def test_extract_fallback_to_body(self) -> None:
        """When content_selector matches nothing, falls back to body."""
        html = """<html><body>
            <p>Body content here</p>
        </body></html>"""
        text, _, _, _ = extract_page_content(html, ".nonexistent-selector")
        assert "Body content here" in text

    def test_extract_empty_html(self) -> None:
        """Handles empty/minimal HTML gracefully without raising."""
        text, html_out, title, pdf_links = extract_page_content("", "main")
        assert isinstance(text, str)
        assert isinstance(html_out, str)
        assert isinstance(title, str)
        assert pdf_links == []


# ---------------------------------------------------------------------------
# Sitemap parsing tests
# ---------------------------------------------------------------------------


class TestSitemapParsing:
    """Tests for WebsiteCrawler.parse_sitemap()."""

    async def test_parse_sitemap_basic(self) -> None:
        """Regular sitemap XML yields a sorted list of URLs."""
        sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/page-b</loc></url>
            <url><loc>https://example.com/page-a</loc></url>
        </urlset>"""

        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(  # noqa: SLF001
            return_value=_mock_response(text=sitemap_xml),
        )

        urls = await crawler.parse_sitemap()
        assert urls == [
            "https://example.com/page-a",
            "https://example.com/page-b",
        ]

    async def test_parse_sitemap_index(self) -> None:
        """Sitemap index recursively fetches child sitemaps."""
        index_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
        </sitemapindex>"""

        child_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/child-page</loc></url>
        </urlset>"""

        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001

        async def _fake_get(url: str) -> httpx.Response:
            if "sitemap-1" in url:
                return _mock_response(text=child_xml)
            return _mock_response(text=index_xml)

        crawler._http.get = AsyncMock(side_effect=_fake_get)  # noqa: SLF001
        urls = await crawler.parse_sitemap()
        assert urls == ["https://example.com/child-page"]

    async def test_parse_sitemap_excludes_patterns(self) -> None:
        """URLs matching exclude_patterns are filtered out."""
        sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/public/page</loc></url>
            <url><loc>https://example.com/private/secret</loc></url>
            <url><loc>https://example.com/admin/dashboard</loc></url>
        </urlset>"""

        config = _make_config(exclude_patterns=["/private/", "/admin/"])
        crawler = _make_crawler(config)
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(  # noqa: SLF001
            return_value=_mock_response(text=sitemap_xml),
        )

        urls = await crawler.parse_sitemap()
        assert urls == ["https://example.com/public/page"]


# ---------------------------------------------------------------------------
# Crawl page tests
# ---------------------------------------------------------------------------


class TestCrawlPage:
    """Tests for WebsiteCrawler.crawl_page()."""

    async def test_crawl_page_success(self) -> None:
        """Successful crawl returns a fully populated CrawledPage."""
        html = """<html>
        <head><title>Test Page</title></head>
        <body><main><p>Hello world</p></main></body>
        </html>"""

        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(  # noqa: SLF001
            return_value=_mock_response(
                text=html,
                headers={"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
            ),
        )

        page = await crawler.crawl_page("https://example.com/Services/Test")
        assert page is not None
        assert isinstance(page, CrawledPage)
        assert page.url == "https://example.com/Services/Test"
        assert page.title == "Test Page"
        assert page.section_path == "Services/Test"
        assert "Hello world" in page.content_text
        assert page.last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"
        assert page.crawl_timestamp != ""

    async def test_crawl_page_http_error(self) -> None:
        """HTTP errors cause crawl_page to return None."""
        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(  # noqa: SLF001
            return_value=_mock_response(status_code=404, text="Not Found"),
        )

        page = await crawler.crawl_page("https://example.com/missing")
        assert page is None

    async def test_crawl_page_content_hash(self) -> None:
        """Content hash is SHA256 of the extracted text."""
        html = "<html><body><main><p>Deterministic text</p></main></body></html>"

        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(  # noqa: SLF001
            return_value=_mock_response(text=html),
        )

        page = await crawler.crawl_page("https://example.com/hash-test")
        assert page is not None
        expected_hash = hashlib.sha256(page.content_text.encode()).hexdigest()
        assert page.content_hash == expected_hash


# ---------------------------------------------------------------------------
# PDF deduplication tests
# ---------------------------------------------------------------------------


class TestPdfDeduplication:
    """Tests for PDF deduplication in the full crawl pipeline."""

    async def test_crawl_deduplicates_pdfs(self) -> None:
        """Same PDF linked from two pages results in a single CrawledPDF."""
        sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/page-a</loc></url>
            <url><loc>https://example.com/page-b</loc></url>
        </urlset>"""

        page_html_a = """<html><body><main>
            <a href="https://example.com/docs/shared.pdf">Shared PDF</a>
        </main></body></html>"""

        page_html_b = """<html><body><main>
            <a href="https://example.com/docs/shared.pdf">Same PDF</a>
        </main></body></html>"""

        pdf_content = b"%PDF-1.4 fake content"

        async def _fake_get(url: str) -> httpx.Response:
            if url.endswith("sitemap.xml"):
                return _mock_response(text=sitemap_xml)
            if url.endswith("page-a"):
                return _mock_response(text=page_html_a)
            if url.endswith("page-b"):
                return _mock_response(text=page_html_b)
            if url.endswith(".pdf"):
                return _mock_response(content=pdf_content, text="")
            return _mock_response(status_code=404, text="Not found")

        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(side_effect=_fake_get)  # noqa: SLF001

        pages, pdfs, result = await crawler.crawl()

        assert len(pages) == 2
        assert len(pdfs) == 1
        assert result.pdfs_discovered == 1
        assert result.pdfs_downloaded == 1
        # Both source pages should be recorded
        assert "https://example.com/page-a" in pdfs[0].source_pages
        assert "https://example.com/page-b" in pdfs[0].source_pages
        assert len(pdfs[0].source_pages) == 2

    async def test_crawled_pdf_section_from_first_page(self) -> None:
        """section_path on the CrawledPDF comes from the first linking page.

        parse_sitemap returns sorted URLs, so the alphabetically first URL
        ('Alpha-section/First') is crawled first and its section_path is used.
        """
        sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/Alpha-section/First</loc></url>
            <url><loc>https://example.com/Beta-section/Second</loc></url>
        </urlset>"""

        page_html_first = """<html><body><main>
            <a href="https://example.com/docs/report.pdf">Report</a>
        </main></body></html>"""

        page_html_second = """<html><body><main>
            <a href="https://example.com/docs/report.pdf">Report</a>
        </main></body></html>"""

        async def _fake_get(url: str) -> httpx.Response:
            if url.endswith("sitemap.xml"):
                return _mock_response(text=sitemap_xml)
            if "First" in url:
                return _mock_response(text=page_html_first)
            if "Second" in url:
                return _mock_response(text=page_html_second)
            if url.endswith(".pdf"):
                return _mock_response(content=b"%PDF-content", text="")
            return _mock_response(status_code=404, text="Not found")

        crawler = _make_crawler()
        assert crawler._http is not None  # noqa: SLF001
        crawler._http.get = AsyncMock(side_effect=_fake_get)  # noqa: SLF001

        _, pdfs, _ = await crawler.crawl()

        assert len(pdfs) == 1
        assert pdfs[0].section_path == "Alpha-section/First"


# ---------------------------------------------------------------------------
# Document conversion tests
# ---------------------------------------------------------------------------


def _make_crawled_page(**overrides: Any) -> CrawledPage:
    """Factory for CrawledPage with sensible defaults."""
    defaults: dict[str, Any] = {
        "url": "https://example.com/Services/Waste-recycling/Bins",
        "title": "Residential Bins",
        "section_path": "Services/Waste-recycling/Bins",
        "content_text": "Information about residential bins.",
        "content_html": "<p>Information about residential bins.</p>",
        "content_hash": hashlib.sha256(b"Information about residential bins.").hexdigest(),
        "last_modified": None,
        "pdf_links": [],
        "crawl_timestamp": "2026-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return CrawledPage(**defaults)


def _make_crawled_pdf(**overrides: Any) -> CrawledPDF:
    """Factory for CrawledPDF with sensible defaults."""
    defaults: dict[str, Any] = {
        "url": "https://example.com/docs/waste-guide.pdf",
        "filename": "waste-guide.pdf",
        "title": "waste guide",
        "source_pages": [
            "https://example.com/Services/Waste-recycling/Bins",
            "https://example.com/Services/Waste-recycling/Collection-schedule",
        ],
        "section_path": "Services/Waste-recycling/Bins",
        "content_hash": hashlib.sha256(b"fake-pdf-content").hexdigest(),
        "size_bytes": 1024,
    }
    defaults.update(overrides)
    return CrawledPDF(**defaults)


class TestCrawledPageToDocument:
    """Tests for crawled_page_to_document()."""

    def test_returns_ingested_document(self) -> None:
        """Returns an IngestedDocument with correct source and type."""
        page = _make_crawled_page()
        doc = crawled_page_to_document(page, "https://example.com")

        assert doc.source == "website"
        assert doc.title == "Residential Bins"
        assert doc.content == "Information about residential bins."
        assert doc.raw_path == ""

    def test_metadata_fields(self) -> None:
        """Metadata has correct content_source, document_type, and section_path."""
        page = _make_crawled_page()
        doc = crawled_page_to_document(page, "https://example.com")

        assert doc.metadata.content_source == "website"
        assert doc.metadata.document_type == "web-page"
        assert doc.metadata.section_path == "Services/Waste-recycling/Bins"
        assert doc.metadata.source_url == page.url

    def test_domain_derived_from_section_path(self) -> None:
        """Domain is derived via url_to_domain() from the section path."""
        page = _make_crawled_page(section_path="Services/Waste-recycling/Bins")
        doc = crawled_page_to_document(page, "https://example.com")
        assert doc.metadata.domain == "services"

    def test_domain_fallback_general(self) -> None:
        """Unrecognised section path falls back to 'general' domain."""
        page = _make_crawled_page(section_path="Unknown/Path")
        doc = crawled_page_to_document(page, "https://example.com")
        assert doc.metadata.domain == "general"

    def test_tags_from_section_path(self) -> None:
        """Tags are generated from section path segments."""
        page = _make_crawled_page(section_path="Services/Waste-recycling/Bins")
        doc = crawled_page_to_document(page, "https://example.com")
        assert doc.metadata.tags == ["services", "waste-recycling", "bins"]

    def test_deterministic_id(self) -> None:
        """Document ID is deterministic for the same URL."""
        page = _make_crawled_page()
        doc1 = crawled_page_to_document(page, "https://example.com")
        doc2 = crawled_page_to_document(page, "https://example.com")
        assert doc1.id == doc2.id
        assert len(doc1.id) == 16


class TestCrawledPdfToDocument:
    """Tests for crawled_pdf_to_document()."""

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_extracts_text_and_returns_document(
        self, mock_extract: MagicMock, tmp_path: Path
    ) -> None:
        """Extracts PDF text and returns an IngestedDocument."""
        mock_extract.return_value = "Extracted PDF text content."
        pdf = _make_crawled_pdf()
        pdf_bytes = b"%PDF-1.4 fake content"

        doc = crawled_pdf_to_document(pdf, pdf_bytes, tmp_path)

        assert doc.source == "website"
        assert doc.title == "waste guide"
        assert doc.content == "Extracted PDF text content."
        assert doc.raw_path == ""
        mock_extract.assert_called_once()

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_metadata_fields(self, mock_extract: MagicMock, tmp_path: Path) -> None:
        """Metadata has correct content_source, document_type, and section_path."""
        mock_extract.return_value = "PDF text."
        pdf = _make_crawled_pdf()

        doc = crawled_pdf_to_document(pdf, b"content", tmp_path)

        assert doc.metadata.content_source == "website"
        assert doc.metadata.document_type == "web-pdf"
        assert doc.metadata.section_path == "Services/Waste-recycling/Bins"
        assert doc.metadata.source_url == pdf.url

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_domain_derived_from_section_path(
        self, mock_extract: MagicMock, tmp_path: Path
    ) -> None:
        """Domain is derived via url_to_domain() from the PDF's section path."""
        mock_extract.return_value = "text"
        pdf = _make_crawled_pdf(section_path="Planning-building/Applications")

        doc = crawled_pdf_to_document(pdf, b"content", tmp_path)
        assert doc.metadata.domain == "planning"

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_tags_include_section_and_source_pages(
        self, mock_extract: MagicMock, tmp_path: Path
    ) -> None:
        """Tags combine section_path_to_tags with source page last segments."""
        mock_extract.return_value = "text"
        pdf = _make_crawled_pdf(
            section_path="Services/Waste-recycling",
            source_pages=[
                "https://example.com/Services/Waste-recycling/Bins",
                "https://example.com/Services/Waste-recycling/Collection-schedule",
            ],
        )

        doc = crawled_pdf_to_document(pdf, b"content", tmp_path)

        # Section path tags
        assert "services" in doc.metadata.tags
        assert "waste-recycling" in doc.metadata.tags
        # Source page tags (last segment of each source URL)
        assert "bins" in doc.metadata.tags
        assert "collection-schedule" in doc.metadata.tags

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_no_duplicate_tags(self, mock_extract: MagicMock, tmp_path: Path) -> None:
        """Source page tags that already exist in section tags are not duplicated."""
        mock_extract.return_value = "text"
        # Source page last segment "waste-recycling" overlaps with section tag
        pdf = _make_crawled_pdf(
            section_path="Services/Waste-recycling",
            source_pages=["https://example.com/Services/Waste-recycling"],
        )

        doc = crawled_pdf_to_document(pdf, b"content", tmp_path)
        assert doc.metadata.tags.count("waste-recycling") == 1

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_temp_file_cleaned_up(self, mock_extract: MagicMock, tmp_path: Path) -> None:
        """Temporary PDF file is removed after extraction."""
        mock_extract.return_value = "text"
        pdf = _make_crawled_pdf()

        crawled_pdf_to_document(pdf, b"content", tmp_path)

        temp_file = tmp_path / pdf.filename
        assert not temp_file.exists()

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_temp_file_cleaned_up_on_error(
        self, mock_extract: MagicMock, tmp_path: Path
    ) -> None:
        """Temporary PDF file is removed even if extraction fails."""
        mock_extract.side_effect = ValueError("Corrupt PDF")
        pdf = _make_crawled_pdf()

        with pytest.raises(ValueError, match="Corrupt PDF"):
            crawled_pdf_to_document(pdf, b"content", tmp_path)

        temp_file = tmp_path / pdf.filename
        assert not temp_file.exists()

    @patch("src.connectors.website_crawler.extract_text_from_pdf")
    def test_deterministic_id(self, mock_extract: MagicMock, tmp_path: Path) -> None:
        """Document ID is deterministic for the same PDF URL."""
        mock_extract.return_value = "text"
        pdf = _make_crawled_pdf()

        doc1 = crawled_pdf_to_document(pdf, b"content", tmp_path)
        doc2 = crawled_pdf_to_document(pdf, b"content", tmp_path)
        assert doc1.id == doc2.id
        assert len(doc1.id) == 16


# ---------------------------------------------------------------------------
# CrawlManifest tests
# ---------------------------------------------------------------------------


def _make_manifest(
    pages: dict[str, ManifestEntry] | None = None,
    pdfs: dict[str, ManifestEntry] | None = None,
) -> CrawlManifest:
    """Factory for CrawlManifest with sensible defaults."""
    return CrawlManifest(
        crawl_timestamp="2026-01-01T00:00:00+00:00",
        base_url="https://example.com",
        pages=pages or {},
        pdfs=pdfs or {},
    )


class TestCrawlManifest:
    """Tests for CrawlManifest serialisation and diff logic."""

    def test_json_round_trip(self) -> None:
        """to_json() and from_json() round-trip correctly."""
        manifest = _make_manifest(
            pages={
                "Services/Waste": ManifestEntry(
                    content_hash="abc123", title="Waste", last_crawled="2026-01-01T00:00:00+00:00"
                ),
            },
            pdfs={
                "/docs/guide.pdf": ManifestEntry(
                    content_hash="def456", title="Guide", last_crawled="2026-01-01T00:00:00+00:00"
                ),
            },
        )
        json_str = manifest.to_json()
        restored = CrawlManifest.from_json(json_str)

        assert restored.crawl_timestamp == manifest.crawl_timestamp
        assert restored.base_url == manifest.base_url
        assert "Services/Waste" in restored.pages
        assert restored.pages["Services/Waste"].content_hash == "abc123"
        assert restored.pages["Services/Waste"].title == "Waste"
        assert "/docs/guide.pdf" in restored.pdfs
        assert restored.pdfs["/docs/guide.pdf"].content_hash == "def456"

    def test_diff_pages_new(self) -> None:
        """Pages not in the manifest are identified as new."""
        manifest = _make_manifest()
        pages = [_make_crawled_page(section_path="New/Page")]

        new, changed, removed = manifest.diff_pages(pages)

        assert len(new) == 1
        assert new[0].section_path == "New/Page"
        assert changed == []
        assert removed == []

    def test_diff_pages_changed(self) -> None:
        """Pages with a different content_hash are identified as changed."""
        manifest = _make_manifest(
            pages={
                "Services/Waste": ManifestEntry(
                    content_hash="old_hash",
                    title="Waste",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        pages = [_make_crawled_page(section_path="Services/Waste", content_hash="new_hash")]

        new, changed, removed = manifest.diff_pages(pages)

        assert new == []
        assert len(changed) == 1
        assert changed[0].section_path == "Services/Waste"
        assert removed == []

    def test_diff_pages_unchanged(self) -> None:
        """Pages with matching content_hash are not in any diff list."""
        same_hash = hashlib.sha256(b"same").hexdigest()
        manifest = _make_manifest(
            pages={
                "Services/Waste": ManifestEntry(
                    content_hash=same_hash,
                    title="Waste",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        pages = [_make_crawled_page(section_path="Services/Waste", content_hash=same_hash)]

        new, changed, removed = manifest.diff_pages(pages)

        assert new == []
        assert changed == []
        assert removed == []

    def test_diff_pages_removed(self) -> None:
        """Pages in the manifest but not in current crawl are identified as removed."""
        manifest = _make_manifest(
            pages={
                "Old/Page": ManifestEntry(
                    content_hash="hash",
                    title="Old",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )

        new, changed, removed = manifest.diff_pages([])

        assert new == []
        assert changed == []
        assert removed == ["Old/Page"]

    def test_diff_pages_mixed(self) -> None:
        """New, changed, unchanged, and removed pages in a single diff."""
        manifest = _make_manifest(
            pages={
                "Existing/Changed": ManifestEntry(
                    content_hash="old",
                    title="Changed",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
                "Existing/Same": ManifestEntry(
                    content_hash="same",
                    title="Same",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
                "Existing/Removed": ManifestEntry(
                    content_hash="gone",
                    title="Removed",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        current = [
            _make_crawled_page(section_path="Existing/Changed", content_hash="new"),
            _make_crawled_page(section_path="Existing/Same", content_hash="same"),
            _make_crawled_page(section_path="Brand/New", content_hash="fresh"),
        ]

        new, changed, removed = manifest.diff_pages(current)

        assert [p.section_path for p in new] == ["Brand/New"]
        assert [p.section_path for p in changed] == ["Existing/Changed"]
        assert removed == ["Existing/Removed"]

    def test_diff_pdfs_new(self) -> None:
        """PDFs not in the manifest are identified as new."""
        manifest = _make_manifest()
        pdfs = [_make_crawled_pdf(url="https://example.com/docs/new.pdf")]

        new, changed, removed = manifest.diff_pdfs(pdfs)

        assert len(new) == 1
        assert changed == []
        assert removed == []

    def test_diff_pdfs_changed(self) -> None:
        """PDFs with a different content_hash are identified as changed."""
        manifest = _make_manifest(
            pdfs={
                "/docs/waste-guide.pdf": ManifestEntry(
                    content_hash="old",
                    title="Guide",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        pdfs = [_make_crawled_pdf(content_hash="new_hash")]

        new, changed, removed = manifest.diff_pdfs(pdfs)

        assert new == []
        assert len(changed) == 1
        assert removed == []

    def test_diff_pdfs_unchanged(self) -> None:
        """PDFs with matching content_hash are not in any diff list."""
        same_hash = hashlib.sha256(b"pdf").hexdigest()
        manifest = _make_manifest(
            pdfs={
                "/docs/waste-guide.pdf": ManifestEntry(
                    content_hash=same_hash,
                    title="Guide",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        pdfs = [_make_crawled_pdf(content_hash=same_hash)]

        new, changed, removed = manifest.diff_pdfs(pdfs)

        assert new == []
        assert changed == []
        assert removed == []

    def test_diff_pdfs_removed(self) -> None:
        """PDFs in the manifest but not in current crawl are identified as removed."""
        manifest = _make_manifest(
            pdfs={
                "/docs/old.pdf": ManifestEntry(
                    content_hash="hash",
                    title="Old",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )

        new, changed, removed = manifest.diff_pdfs([])

        assert new == []
        assert changed == []
        assert removed == ["/docs/old.pdf"]

    def test_update_replaces_entries(self) -> None:
        """update() creates/replaces manifest entries from current crawl data."""
        manifest = _make_manifest(
            pages={
                "Old/Page": ManifestEntry(
                    content_hash="old",
                    title="Old",
                    last_crawled="2025-01-01T00:00:00+00:00",
                ),
            },
        )

        pages = [
            _make_crawled_page(section_path="Old/Page", content_hash="updated", title="Updated"),
            _make_crawled_page(section_path="New/Page", content_hash="fresh", title="Fresh"),
        ]
        pdfs = [_make_crawled_pdf(url="https://example.com/docs/report.pdf", content_hash="ph")]

        manifest.update(pages, pdfs)

        assert manifest.pages["Old/Page"].content_hash == "updated"
        assert manifest.pages["Old/Page"].title == "Updated"
        assert manifest.pages["New/Page"].content_hash == "fresh"
        assert "/docs/report.pdf" in manifest.pdfs
        assert manifest.pdfs["/docs/report.pdf"].content_hash == "ph"
        # Timestamp should have been updated
        assert manifest.crawl_timestamp != "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Manifest blob storage tests
# ---------------------------------------------------------------------------


class TestManifestBlob:
    """Tests for load_manifest_from_blob and save_manifest_to_blob."""

    async def test_load_returns_none_when_blob_missing(self) -> None:
        """Returns None when the blob does not exist."""
        from azure.core.exceptions import ResourceNotFoundError

        mock_blob_client = AsyncMock()
        mock_blob_client.download_blob = AsyncMock(side_effect=ResourceNotFoundError("not found"))

        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob_client

        result = await load_manifest_from_blob(mock_container)

        assert result is None
        mock_container.get_blob_client.assert_called_once_with("website/crawl-manifest.json")

    async def test_load_returns_manifest_when_blob_exists(self) -> None:
        """Returns a CrawlManifest when the blob exists and contains valid JSON."""
        manifest = _make_manifest(
            pages={
                "Services/Test": ManifestEntry(
                    content_hash="abc",
                    title="Test",
                    last_crawled="2026-01-01T00:00:00+00:00",
                ),
            },
        )
        json_bytes = manifest.to_json().encode("utf-8")

        mock_download = AsyncMock()
        mock_download.readall = AsyncMock(return_value=json_bytes)

        mock_blob_client = AsyncMock()
        mock_blob_client.download_blob = AsyncMock(return_value=mock_download)

        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob_client

        result = await load_manifest_from_blob(mock_container)

        assert result is not None
        assert result.base_url == "https://example.com"
        assert "Services/Test" in result.pages
        assert result.pages["Services/Test"].content_hash == "abc"

    async def test_save_uploads_json_to_blob(self) -> None:
        """save_manifest_to_blob uploads the manifest JSON to the configured blob."""
        manifest = _make_manifest()

        mock_blob_client = AsyncMock()
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob_client

        await save_manifest_to_blob(manifest, mock_container)

        mock_container.get_blob_client.assert_called_once_with("website/crawl-manifest.json")
        mock_blob_client.upload_blob.assert_called_once()
        uploaded_data = mock_blob_client.upload_blob.call_args[0][0]
        assert '"crawl_timestamp"' in uploaded_data
        assert '"base_url"' in uploaded_data
