"""JSON report writer for eval results."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).parent / "reports"


def write_report(
    results: list[dict[str, Any]],
    *,
    api_url: str,
    rag_available: bool,
) -> Path:
    """Write a timestamped JSON report and return the file path."""
    REPORTS_DIR.mkdir(exist_ok=True)

    total = len(results)
    routing_correct_count = sum(1 for r in results if r.get("routing_correct", False))
    relevance_scores = [r["response_relevance"] for r in results if "response_relevance" in r]
    weighted_scores = [r["weighted_score"] for r in results if "weighted_score" in r]

    by_category: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "routing_correct": 0,
            "relevance_sum": 0.0,
            "weighted_sum": 0.0,
        }
    )
    for r in results:
        cat = r.get("category", "unknown")
        by_category[cat]["count"] += 1
        if r.get("routing_correct"):
            by_category[cat]["routing_correct"] += 1
        by_category[cat]["relevance_sum"] += r.get("response_relevance", 0)
        by_category[cat]["weighted_sum"] += r.get("weighted_score", 0)

    category_summary = {}
    for cat, data in by_category.items():
        n = data["count"]
        category_summary[cat] = {
            "count": n,
            "routing_accuracy": round(data["routing_correct"] / n, 2) if n else 0,
            "mean_relevance": round(data["relevance_sum"] / n, 1) if n else 0,
            "mean_score": round(data["weighted_sum"] / n, 1) if n else 0,
        }

    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "api_url": api_url,
        "rag_available": rag_available,
        "summary": {
            "total_queries": total,
            "routing_accuracy": (round(routing_correct_count / total, 2) if total else 0),
            "mean_relevance": (
                round(sum(relevance_scores) / len(relevance_scores), 1) if relevance_scores else 0
            ),
            "overall_score": (
                round(sum(weighted_scores) / len(weighted_scores), 1) if weighted_scores else 0
            ),
        },
        "by_category": category_summary,
        "results": results,
    }

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = REPORTS_DIR / f"eval-{ts}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return path
