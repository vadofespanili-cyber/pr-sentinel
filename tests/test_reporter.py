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


def test_summary_contains_hidden_marker():
    """The summary carries the hidden marker used for idempotent
    updates (v0.2.0), with and without findings."""
    from pr_sentinel.reporter import SUMMARY_MARKER

    assert SUMMARY_MARKER in build_summary(sample_findings(), pr_number=42)
    assert SUMMARY_MARKER in build_summary([], pr_number=7)
    # The marker is the last line, so it never leaks into the table.
    summary = build_summary(sample_findings(), pr_number=42)
    assert summary.rstrip("\n").splitlines()[-1] == SUMMARY_MARKER


# --- SARIF 2.1.0 output (v0.2.0) --------------------------------------------


def test_sarif_structure_and_severity_mapping():
    from pr_sentinel import __version__
    from pr_sentinel.reporter import findings_to_sarif

    parsed = json.loads(findings_to_sarif(sample_findings()))
    assert parsed["$schema"].endswith("sarif-schema-2.1.0.json")
    assert parsed["version"] == "2.1.0"
    run = parsed["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "pr-sentinel"
    assert driver["version"] == __version__
    assert driver["informationUri"].startswith("https://github.com/")
    results = run["results"]
    assert len(results) == 3
    by_severity = {r["ruleId"]: r["level"] for r in results}
    # critical/high -> error, medium -> warning, low -> note.
    assert by_severity["unused-import"] == "error"  # high
    assert by_severity["pr-sentinel-style"] == "note"  # low, no rule
    assert by_severity["pr-sentinel-security"] == "error"  # critical, no rule
    medium = Finding("medium", "bug", "src/a.py", 1, "Medium one.")
    assert (
        json.loads(findings_to_sarif([medium]))["runs"][0]["results"][0]["level"]
        == "warning"
    )


def test_sarif_rules_are_deduplicated():
    from pr_sentinel.reporter import findings_to_sarif

    findings = [
        Finding("high", "security", "a.py", 1, "First eval.", rule="dangerous-eval"),
        Finding("high", "security", "b.py", 2, "Second eval.", rule="dangerous-eval"),
        Finding("low", "style", "a.py", 3, "Style one."),
    ]
    parsed = json.loads(findings_to_sarif(findings))
    rules = parsed["runs"][0]["tool"]["driver"]["rules"]
    assert [r["id"] for r in rules] == ["dangerous-eval", "pr-sentinel-style"]
    assert rules[0]["shortDescription"]["text"] == "First eval."
    assert rules[0]["defaultConfiguration"]["level"] == "error"


def test_sarif_locations_and_missing_line():
    from pr_sentinel.reporter import findings_to_sarif

    parsed = json.loads(findings_to_sarif(sample_findings()))
    results = parsed["runs"][0]["results"]
    with_line = next(r for r in results if r["ruleId"] == "unused-import")
    location = with_line["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "src/a.py"
    assert location["region"]["startLine"] == 2
    no_line = next(r for r in results if r["ruleId"] == "pr-sentinel-security")
    location = no_line["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "src/b.py"
    assert "region" not in location


def test_sarif_empty_findings():
    from pr_sentinel.reporter import findings_to_sarif

    parsed = json.loads(findings_to_sarif([]))
    run = parsed["runs"][0]
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []
    assert parsed["version"] == "2.1.0"


def test_sarif_version_parameter():
    from pr_sentinel.reporter import findings_to_sarif

    parsed = json.loads(findings_to_sarif([], version="9.9.9"))
    assert parsed["runs"][0]["tool"]["driver"]["version"] == "9.9.9"


def test_sarif_never_echoes_secret_values():
    """A leaked-key finding's SARIF must not contain the key itself:
    static-check messages are fixed strings, never the matched text."""
    from pr_sentinel.diff import parse_diff
    from pr_sentinel.reporter import findings_to_sarif
    from pr_sentinel.static_checks import run_static_checks

    key = "sk-abcdefghij1234567890"
    diff = parse_diff(
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " x = 1\n"
        f'+api_key = "{key}"\n'
    )
    findings = run_static_checks(diff)
    assert findings, "expected a leaked-key finding"
    assert all(f.severity == "critical" for f in findings)
    sarif = findings_to_sarif(findings)
    assert key not in sarif
    assert "OpenAI API key" in sarif
