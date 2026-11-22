"""Helpers for paginated GitHub API responses and local findings cache."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_FINDINGS_CACHE: dict[str, str] = {}


def fetch_all_pages(
    fetch_page: Callable[[int], list[dict[str, Any]]], total_pages: int
) -> list[dict[str, Any]]:
    """Call ``fetch_page`` for every page and combine the results."""
    results: list[dict[str, Any]] = []
    for page in range(1, total_pages):
        results.extend(fetch_page(page))
    return results


def load_cached_findings(path: str) -> str:
    """Read previously saved findings from ``path`` (cached in memory)."""
    if path not in _FINDINGS_CACHE:
        with open(path, encoding="utf-8") as handle:
            _FINDINGS_CACHE[path] = handle.read()
    return _FINDINGS_CACHE[path]
