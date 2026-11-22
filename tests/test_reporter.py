"""Tests for the reporter: summary, inline comments and JSON output."""

import json

from pr_sentinel.diff import parse_diff
from pr_sentinel.findings import Finding
from pr_sentinel.reporter import (
    build_inline_comments,
    build_summary,
    findings_to_json,
)

SAMPLE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1,2 +1,4 @@\n"
    " import os\n"
    "+import sys\n"
    "+import json\n"
    " x = 1\n"
)


def sample_findings() -> list[Finding]:
    return [
        Finding(
            severity="high",
            category="bug",
            file="src/a.py",
            line=2,
            message="Unused import.",
            suggestion="Remove the import.",
            rule="unused-import",
        ),
        Finding(
            severity="low",
            category="style",
            file="src/a.py",
            line=3,
            message="Import order.",
        ),
        Finding(
            severity="critical",
            category="security",
            file="src/b.py",
            line=None,
            message="Hardcoded secret.",
        ),
    ]


def test_summary_contains_counts_and_table_rows():
    summary = build_summary(sample_findings(), pr_title="Add thing", pr_number=42)
    assert "# pr-sentinel review — PR #42: Add thing" in summary
    assert "Found 3 findings: 1 critical, 1 high, 1 low." in summary
    assert "| Severity | File | Line | Message |" in summary
    assert "| high | src/a.py | 2 | Unused import. |" in summary
    assert "| low | src/a.py | 3 | Import order. |" in summary
    assert "| critical | src/b.py | — | Hardcoded secret. |" in summary
    assert "AI-generated suggestions" in summary
    assert "Reviewed by pr-sentinel" in summary


def test_summary_without_pr_info():
    summary = build_summary(sample_findings())
    assert summary.startswith("# pr-sentinel review\n")


def test_summary_empty_findings():
    summary = build_summary([], pr_title="Clean PR", pr_number=7)
    assert "No findings" in summary
    assert "PR #7" in summary
    assert "| Severity |" not in summary
    assert "Reviewed by pr-sentinel" in summary


def test_inline_comments_use_patch_positions():
    diff_files = parse_diff(SAMPLE_DIFF)
    comments = build_inline_comments(sample_findings(), diff_files)
    # Line 2 of src/a.py is the first added line: position 3
    # (hunk header = 1, context line = 2, added line = 3).
    # Line 3 is the second added line: position 4.
    # The src/b.py finding has no line, so it is skipped.
    assert comments == [
        {
            "path": "src/a.py",
            "position": 3,
            "body": (
                "**high · bug**\n\nUnused import.\n\n**Suggestion:** Remove the import."
            ),
        },
        {
            "path": "src/a.py",
            "position": 4,
            "body": "**low · style**\n\nImport order.",
        },
    ]


def test_inline_comments_skip_findings_without_a_position():
    diff_files = parse_diff(SAMPLE_DIFF)
    findings = [
        # Line 1 is a context line, not an added line: no patch position.
        Finding("medium", "bug", "src/a.py", 1, "Context line."),
        # File not in the diff at all.
        Finding("medium", "bug", "src/missing.py", 1, "Missing file."),
        # No line number.
        Finding("medium", "bug", "src/a.py", None, "No line."),
    ]
    assert build_inline_comments(findings, diff_files) == []


def test_findings_to_json_round_trips():
    findings = sample_findings()
    parsed = json.loads(findings_to_json(findings))
    assert parsed == [f.to_dict() for f in findings]
    assert findings_to_json([]) == "[]"
