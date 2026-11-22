"""AI reviewer: sends diff chunks to a Responses API and parses findings.

Stdlib only (urllib). Works with any Responses-compatible endpoint, e.g.
OpenAI's ``/v1/responses`` or a compatible relay, by pointing
``base_url`` at the provider root.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .diff import DiffFile, chunk_diff
from .findings import CATEGORIES, SEVERITIES, Finding, sort_findings

REVIEWER_RULE = "ai-review"


class ReviewerError(Exception):
    """Raised when the review request or the model's reply is unusable."""


def build_review_prompt(chunk_text: str) -> str:
    """Build the prompt asking the model to review one diff chunk."""
    return (
        "You are a careful code reviewer reviewing a pull request diff.\n"
        "Review the diff below and report real issues only.\n"
        "Focus on real bugs, security vulnerabilities, and performance "
        "problems. Do not report style nitpicks unless they are severe.\n"
        "Return ONLY a JSON array of findings, with no other text.\n"
        'Each finding must have these keys: "severity", "category", "file", '
        '"line", "message", "suggestion".\n'
        '"severity" must be one of: critical, high, medium, low.\n'
        '"category" must be one of: bug, security, performance, style, '
        "maintainability.\n"
        '"file" is the path of the file in the diff. "line" is the line '
        "number in the new version of the file, or null if the finding is "
        "about the whole file.\n"
        '"message" explains the issue and "suggestion" gives a fix.\n'
        "If there are no issues, return an empty array: [].\n"
        "\n"
        "Diff:\n"
        f"{chunk_text}\n"
    )


def parse_findings_json(text: str) -> list[Finding]:
    """Parse the model's JSON reply into findings.

    Tolerant: ```` ```json ```` fences are stripped, entries missing a
    required key or with an invalid severity/category are skipped.
    Raises :class:`ReviewerError` if the text is not parseable JSON at all
    or is not a JSON array.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
        stripped = stripped.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ReviewerError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ReviewerError("model returned JSON that is not an array")
    findings: list[Finding] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        # severity, category, file, line and message are all required;
        # suggestion is optional. A missing "line" key is not the same
        # as an explicit null (a file-level finding).
        if not {"severity", "category", "file", "line", "message"} <= entry.keys():
            continue
        severity = entry.get("severity")
        category = entry.get("category")
        file = entry.get("file")
        line = entry.get("line")
        message = entry.get("message")
        if severity not in SEVERITIES or category not in CATEGORIES:
            continue
        if not isinstance(file, str) or not file:
            continue
        if not isinstance(message, str) or not message:
            continue
        if line is not None and (isinstance(line, bool) or not isinstance(line, int)):
            continue
        suggestion = entry.get("suggestion") or ""
        if not isinstance(suggestion, str):
            suggestion = str(suggestion)
        findings.append(
            Finding(
                severity=severity,
                category=category,
                file=file,
                line=line,
                message=message,
                suggestion=suggestion,
                rule=REVIEWER_RULE,
            )
        )
    return findings


def _extract_output_text(data: object) -> str | None:
    """Pull the model's text out of a Responses API reply."""
    if not isinstance(data, dict):
        return None
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    parts: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(part["text"])
    joined = "".join(parts)
    return joined or None


class Reviewer:
    """Minimal Responses API client for reviewing diff chunks."""

    def __init__(
        self, api_key: str, base_url: str, model: str, timeout: float = 60.0
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def review_chunk(self, chunk_text: str) -> list[Finding]:
        """Review one diff chunk and return its findings."""
        payload = json.dumps(
            {"model": self.model, "input": build_review_prompt(chunk_text)}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise ReviewerError(
                f"review request failed with status {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ReviewerError(f"review request failed: {exc.reason}") from exc
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReviewerError(f"invalid JSON in review response: {exc}") from exc
        text = _extract_output_text(data)
        if not text:
            raise ReviewerError("review response contained no output text")
        return parse_findings_json(text)

    def review(self, files: list[DiffFile], max_chars: int = 30000) -> list[Finding]:
        """Review all files, chunked, deduplicated and sorted."""
        seen: set[tuple[str, int | None, str]] = set()
        findings: list[Finding] = []
        for chunk in chunk_diff(files, max_chars=max_chars):
            for finding in self.review_chunk(chunk.text):
                key = (finding.file, finding.line, finding.message)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
        return sort_findings(findings)
