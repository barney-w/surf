"""Fixtures for the eval test suite."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from .report import write_report

logger = logging.getLogger(__name__)

# Disable auth so the API doesn't require JWT tokens
os.environ.setdefault("AUTH_ENABLED", "false")

API_URL = os.environ.get("EVAL_API_URL", "http://localhost:8090")
REQUEST_TIMEOUT = 200.0  # seconds — LLM calls can be slow


@pytest.fixture(scope="session")
def api_url() -> str:
    return API_URL


@pytest.fixture(scope="session")
def api_client() -> Generator[httpx.Client, None, None]:
    """Sync httpx client for sending chat requests."""
    with httpx.Client(base_url=API_URL, timeout=REQUEST_TIMEOUT) as client:
        yield client


@pytest.fixture(scope="session")
def anthropic_client() -> anthropic.Anthropic:
    """Anthropic client for LLM-as-judge calls."""
    return anthropic.Anthropic()


@pytest.fixture(scope="session")
def rag_available(api_client: httpx.Client) -> bool:
    """Probe RAG availability by sending a known query and checking for sources."""
    try:
        resp = api_client.post(
            "/api/v1/chat",
            json={"message": "What is the leave policy?"},
        )
        if resp.status_code == 200:
            data = resp.json()
            sources = data.get("response", {}).get("sources", [])
            available = len(sources) > 0
            label = "available" if available else "unavailable"
            logger.info("RAG probe: %s (%d sources)", label, len(sources))
            return available
    except Exception:
        logger.warning("RAG probe failed", exc_info=True)
    return False


@pytest.fixture(scope="session")
def dataset() -> list[dict[str, Any]]:
    """Load the eval dataset from JSON."""
    path = Path(__file__).parent / "dataset.json"
    with open(path) as f:
        data: list[dict[str, Any]] = json.load(f)
    return data


class ResultsCollector:
    """Accumulates per-query results across the test session."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def add(self, result: dict[str, Any]) -> None:
        self.results.append(result)


@pytest.fixture(scope="session")
def results_collector() -> ResultsCollector:
    return ResultsCollector()


@pytest.fixture(scope="session", autouse=True)
def write_report_on_finish(
    results_collector: ResultsCollector,
    api_url: str,
    rag_available: bool,
) -> Generator[None, None, None]:
    """Session finaliser: write the JSON report after all tests complete."""
    yield
    if not results_collector.results:
        return

    path = write_report(
        results_collector.results,
        api_url=api_url,
        rag_available=rag_available,
    )
    logger.info("Eval report written to %s", path)

    total = len(results_collector.results)
    routing_ok = sum(1 for r in results_collector.results if r.get("routing_correct"))
    scores = [r["weighted_score"] for r in results_collector.results if "weighted_score" in r]
    relevance = [
        r["response_relevance"] for r in results_collector.results if "response_relevance" in r
    ]

    print(f"\n{'=' * 60}")
    print(f"EVAL REPORT: {path}")
    print(f"{'=' * 60}")
    print(f"  Queries:          {total}")
    if total:
        pct = routing_ok / total * 100
        print(f"  Routing accuracy: {routing_ok}/{total} ({pct:.0f}%)")
    if relevance:
        print(f"  Mean relevance:   {sum(relevance) / len(relevance):.1f}/5")
    if scores:
        print(f"  Overall score:    {sum(scores) / len(scores):.1f}/100")
    print(f"  RAG available:    {rag_available}")
    print(f"{'=' * 60}\n")
