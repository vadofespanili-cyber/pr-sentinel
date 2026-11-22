"""Markdown summary, inline review comments and JSON output."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import __version__
from .diff import DiffFile
from .findings import SEVERITIES, Finding

# Hidden marker in the summary comment: the CLI finds the existing
# summary by this marker and updates it in place, so re-pushes never
# spam the PR with a new summary comment each time.
SUMMARY_MARKER = "<!-- pr-sentinel:summary -->"

_AI_NOTE = (
    "> **Note:** These findings are AI-generated suggestions. "
    "Please verify them before acting on them."
)
_STATIC_NOTE = (
    "> **Note:** These findings are from deterministic static checks. "
    "Please verify them before acting on them."
)
_FOOTER = "---\nReviewed by pr-sentinel"


def build_summary(
    findings: list[Finding],
    pr_title: str = "",
    pr_number: int | None = None,
    ai_used: bool = True,
) -> str:
    """Render the review summary as Markdown.

    ``ai_used`` says whether the AI reviewer actually ran: the note under
    the table names the source of the findings, so a static-only review
    is never presented as AI output.
    """
    if pr_number is not None:
        title = f"# pr-sentinel review — PR #{pr_number}"
        if pr_title:
            title += f": {pr_title}"
    elif pr_title:
        title = f"# pr-sentinel review — {pr_title}"
    else:
        title = "# pr-sentinel review"

    lines = [title, ""]
    if not findings:
        lines.append("No findings. Nothing to report — the changes look clean.")
        lines.extend(["", _FOOTER, "", SUMMARY_MARKER])
        return "\n".join(lines) + "\n"

    counts = Counter(f.severity for f in findings)
    breakdown = ", ".join(
        f"{counts[severity]} {severity}"
        for severity in SEVERITIES
        if counts.get(severity)
    )
    noun = "finding" if len(findings) == 1 else "findings"
    lines.append(f"Found {len(findings)} {noun}: {breakdown}.")
    lines.append("")
    lines.append("| Severity | File | Line | Message |")
    lines.append("| --- | --- | --- | --- |")
    for finding in findings:
        line = str(finding.line) if finding.line is not None else "—"
        lines.append(
            f"| {finding.severity} | {_cell(finding.file)} | {line} "
            f"| {_cell(finding.message)} |"
        )
    lines.extend(
        ["", _AI_NOTE if ai_used else _STATIC_NOTE, "", _FOOTER, "", SUMMARY_MARKER]
    )
    return "\n".join(lines) + "\n"


def build_inline_comments(
    findings: list[Finding], diff_files: list[DiffFile]
) -> list[dict[str, str | int]]:
    """Turn findings into GitHub inline review comments.

    Only findings on a line that exists in the diff get a comment (added
    and context lines both exist in the new version); the rest are
    skipped (they still appear in the summary). Comments use the modern
    ``line`` + ``side`` fields, not the legacy ``position`` field.
    """
    by_path = {diff_file.path: diff_file for diff_file in diff_files}
    comments: list[dict[str, str | int]] = []
    for finding in findings:
        if finding.line is None:
            continue
        diff_file = by_path.get(finding.file)
        if diff_file is None:
            continue
        if diff_file.new_line_position(finding.line) is None:
            continue
        body = f"**{finding.severity} · {finding.category}**\n\n{finding.message}"
        if finding.suggestion:
            body += f"\n\n**Suggestion:** {finding.suggestion}"
        comments.append(
            {
                "path": finding.file,
                "line": finding.line,
                "side": "RIGHT",
                "body": body,
            }
        )
    return comments


def findings_to_json(findings: list[Finding]) -> str:
    """Serialize findings as indented JSON."""
    return json.dumps([f.to_dict() for f in findings], indent=2)


SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)
SARIF_LEVELS = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
}
SARIF_INFORMATION_URI = "https://github.com/vadofespanili-cyber/pr-sentinel"


def findings_to_sarif(findings: list[Finding], version: str = __version__) -> str:
    """Serialize findings as SARIF 2.1.0 JSON (stdlib ``json`` only).

    Rule ids are the finding's stable ``rule`` slug — static checks use
    one slug per check (e.g. ``leaked-openai-key``), the AI reviewer uses
    ``ai-review`` — falling back to ``pr-sentinel-<category>`` when a
    finding has no rule. Rules are deduplicated in first-seen order.
    Messages never contain raw secret values: static-check messages are
    fixed strings, and the AI reviewer is instructed not to echo secrets.
    """
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for finding in findings:
        rule_id = finding.rule or f"pr-sentinel-{finding.category}"
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": finding.message},
                "fullDescription": {"text": finding.suggestion or finding.message},
                "defaultConfiguration": {"level": SARIF_LEVELS[finding.severity]},
            }
        physical_location: dict[str, Any] = {"artifactLocation": {"uri": finding.file}}
        if finding.line is not None:
            physical_location["region"] = {"startLine": finding.line}
        results.append(
            {
                "ruleId": rule_id,
                "level": SARIF_LEVELS[finding.severity],
                "message": {"text": finding.message},
                "locations": [{"physicalLocation": physical_location}],
            }
        )
    sarif: dict[str, Any] = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pr-sentinel",
                        "version": version,
                        "informationUri": SARIF_INFORMATION_URI,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def _cell(text: str) -> str:
    """Escape one Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")
