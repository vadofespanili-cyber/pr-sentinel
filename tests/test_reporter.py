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


def test_summary_note_names_static_checks_when_ai_did_not_run():
    summary = build_summary(sample_findings(), ai_used=False)
    assert "deterministic static checks" in summary
    assert "AI-generated suggestions" not in summary


def test_summary_note_names_ai_when_ai_ran():
    summary = build_summary(sample_findings(), ai_used=True)
    assert "AI-generated suggestions" in summary
    assert "deterministic static checks" not in summary


def test_summary_without_pr_info():
    summary = build_summary(sample_findings())
    assert summary.startswith("# pr-sentinel review\n")


def test_summary_empty_findings():
    summary = build_summary([], pr_title="Clean PR", pr_number=7)
    assert "No findings" in summary
    assert "PR #7" in summary
    assert "| Severity |" not in summary
    assert "Reviewed by pr-sentinel" in summary


def test_inline_comments_use_line_and_side():
    diff_files = parse_diff(SAMPLE_DIFF)
    comments = build_inline_comments(sample_findings(), diff_files)
    # Line 2 of src/a.py is the first added line; line 3 the second.
    # The src/b.py finding has no line, so it is skipped.
    assert comments == [
        {
            "path": "src/a.py",
            "line": 2,
            "side": "RIGHT",
            "body": (
                "**high · bug**\n\nUnused import.\n\n**Suggestion:** Remove the import."
            ),
        },
        {
            "path": "src/a.py",
            "line": 3,
            "side": "RIGHT",
            "body": "**low · style**\n\nImport order.",
        },
    ]


def test_inline_comments_cover_context_lines_but_skip_unmapped_findings():
    diff_files = parse_diff(SAMPLE_DIFF)
    findings = [
        # Line 1 is a context line, but it exists in the new file, so it
        # gets a comment (since v0.1.2).
        Finding("medium", "bug", "src/a.py", 1, "Context line."),
        # File not in the diff at all.
        Finding("medium", "bug", "src/missing.py", 1, "Missing file."),
        # No line number.
        Finding("medium", "bug", "src/a.py", None, "No line."),
    ]
    assert build_inline_comments(findings, diff_files) == [
        {
            "path": "src/a.py",
            "line": 1,
            "side": "RIGHT",
            "body": "**medium · bug**\n\nContext line.",
        }
    ]


def test_findings_to_json_round_trips():
    findings = sample_findings()
    parsed = json.loads(findings_to_json(findings))
    assert parsed == [f.to_dict() for f in findings]
    assert findings_to_json([]) == "[]"
