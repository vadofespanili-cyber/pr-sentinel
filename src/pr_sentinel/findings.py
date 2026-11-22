"""Finding model shared by static checks, the AI reviewer and the reporter."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

SEVERITIES = ("critical", "high", "medium", "low")
CATEGORIES = ("bug", "security", "performance", "style", "maintainability")

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Finding:
    """One review finding, from a static check or the AI reviewer."""

    severity: str
    category: str
    file: str
    line: int | None
    message: str
    suggestion: str = ""
    rule: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r}")
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category!r}")
        if self.line is not None and self.line < 1:
            raise ValueError(f"line must be >= 1, got {self.line!r}")

    def to_dict(self) -> dict:
        """Plain dict for JSON output."""
        return {
            "severity": self.severity,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "suggestion": self.suggestion,
            "rule": self.rule,
        }


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Sort by severity (worst first), then file, then line."""
    return sorted(
        findings,
        key=lambda f: (
            SEVERITY_RANK[f.severity],
            f.file,
            f.line if f.line is not None else 0,
        ),
    )


def filter_findings(
    findings: list[Finding],
    threshold: str = "medium",
    ignore_paths: tuple[str, ...] | list[str] = (),
) -> list[Finding]:
    """Keep findings at or above the severity threshold, minus ignored paths.

    ``threshold`` is inclusive: "medium" keeps critical, high and medium.
    ``ignore_paths`` uses fnmatch globs (e.g. ``"vendor/**"``, ``"*.min.js"``).
    """
    if threshold not in SEVERITIES:
        raise ValueError(f"unknown severity threshold: {threshold!r}")
    max_rank = SEVERITY_RANK[threshold]
    kept = []
    for finding in findings:
        if SEVERITY_RANK[finding.severity] > max_rank:
            continue
        if any(fnmatch.fnmatch(finding.file, pattern) for pattern in ignore_paths):
            continue
        kept.append(finding)
    return kept
