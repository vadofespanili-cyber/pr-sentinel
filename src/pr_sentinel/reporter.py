"""Markdown summary, inline review comments and JSON output."""

from __future__ import annotations

import json
from collections import Counter

from .diff import DiffFile
from .findings import SEVERITIES, Finding

_NOTE = (
    "> **Note:** These findings are AI-generated suggestions. "
    "Please verify them before acting on them."
)
_FOOTER = "---\nReviewed by pr-sentinel"


def build_summary(
    findings: list[Finding], pr_title: str = "", pr_number: int | None = None
) -> str:
    """Render the review summary as Markdown."""
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
        lines.extend(["", _FOOTER])
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
    lines.extend(["", _NOTE, "", _FOOTER])
    return "\n".join(lines) + "\n"


def build_inline_comments(
    findings: list[Finding], diff_files: list[DiffFile]
) -> list[dict]:
    """Turn findings into GitHub inline review comments.

    Only findings on an added line that exists in the diff get a comment;
    the rest are skipped (they still appear in the summary).
    """
    by_path = {diff_file.path: diff_file for diff_file in diff_files}
    comments: list[dict] = []
    for finding in findings:
        if finding.line is None:
            continue
        diff_file = by_path.get(finding.file)
        if diff_file is None:
            continue
        position = diff_file.new_line_position(finding.line)
        if position is None:
            continue
        body = f"**{finding.severity} · {finding.category}**\n\n{finding.message}"
        if finding.suggestion:
            body += f"\n\n**Suggestion:** {finding.suggestion}"
        comments.append({"path": finding.file, "position": position, "body": body})
    return comments


def findings_to_json(findings: list[Finding]) -> str:
    """Serialize findings as indented JSON."""
    return json.dumps([f.to_dict() for f in findings], indent=2)


def _cell(text: str) -> str:
    """Escape one Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")
