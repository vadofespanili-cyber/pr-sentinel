"""Command-line interface for pr-sentinel."""

from __future__ import annotations

import argparse
import fnmatch
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    Config,
    ConfigError,
    api_key_from_env,
    github_token_from_env,
    load_config,
    resolve_instructions,
)
from .diff import DiffFile, parse_diff, sort_by_risk
from .findings import SEVERITY_RANK, Finding, filter_findings, sort_findings
from .github_client import GitHubClient, GitHubError
from .reporter import (
    INLINE_MARKER,
    SUMMARY_MARKER,
    build_inline_comments,
    build_summary,
    findings_to_json,
    findings_to_sarif,
)

# The reviewer and static checks are built by other parts of the project.
# They are imported lazily so `check-config` (and static-only runs) keep
# working when those modules are not present yet.
_STATIC_CHECK_FUNCTIONS = ("run_static_checks", "check_diff", "run_checks")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="pr-sentinel",
        description="AI-powered pull request review bot.",
    )
    parser.add_argument(
        "--version", action="version", version=f"pr-sentinel {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")

    review = subparsers.add_parser("review", help="Review a GitHub pull request.")
    review.add_argument("--repo", required=True, help="Repository as OWNER/REPO.")
    review.add_argument("--pr", required=True, type=int, help="Pull request number.")
    review.add_argument("--config", help="Path to a config file.")
    review.add_argument(
        "--dry-run", action="store_true", help="Do not post the review to GitHub."
    )
    review.add_argument("--json-out", help="Write findings as JSON to this path.")
    review.add_argument(
        "--sarif-out", help="Write findings as SARIF 2.1.0 to this path."
    )
    review.add_argument(
        "--fail-on-severity",
        type=_normalize_gate_value,
        choices=["none", "critical", "high", "medium", "low"],
        default=None,
        help="Exit 1 if any reported finding meets this severity "
        "(overrides the config file; default: no gate).",
    )
    review.add_argument(
        "--api-format",
        type=_normalize_api_format_value,
        choices=["responses", "chat_completions"],
        default=None,
        help="Model API format (overrides the config file; default: "
        "responses). Use chat_completions for OpenRouter, Ollama, "
        "Groq, vLLM and other OpenAI-compatible providers.",
    )
    review.add_argument(
        "--reviewed-commit",
        default=None,
        help="Full commit SHA the review covers; shown as "
        "'Reviewed commit: <short-sha>' at the bottom of the summary "
        "comment (default: no line).",
    )

    review_diff = subparsers.add_parser(
        "review-diff", help="Review a local patch file (no GitHub needed)."
    )
    review_diff.add_argument("--file", required=True, help="Path to a .patch file.")
    review_diff.add_argument("--config", help="Path to a config file.")
    review_diff.add_argument("--json-out", help="Write findings as JSON to this path.")
    review_diff.add_argument(
        "--sarif-out", help="Write findings as SARIF 2.1.0 to this path."
    )
    review_diff.add_argument(
        "--fail-on-severity",
        type=_normalize_gate_value,
        choices=["none", "critical", "high", "medium", "low"],
        default=None,
        help="Exit 1 if any reported finding meets this severity "
        "(overrides the config file; default: no gate).",
    )
    review_diff.add_argument(
        "--api-format",
        type=_normalize_api_format_value,
        choices=["responses", "chat_completions"],
        default=None,
        help="Model API format (overrides the config file; default: "
        "responses). Use chat_completions for OpenRouter, Ollama, "
        "Groq, vLLM and other OpenAI-compatible providers.",
    )
    review_diff.add_argument(
        "--reviewed-commit",
        default=None,
        help="Full commit SHA the reviewed patch came from; shown as "
        "'Reviewed commit: <short-sha>' at the bottom of the summary "
        "(default: no line).",
    )

    check_config = subparsers.add_parser(
        "check-config", help="Validate a config file and print its fields."
    )
    check_config.add_argument("--config", help="Path to a config file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse arguments and dispatch to the subcommand."""
    args = build_parser().parse_args(argv)
    if args.command == "check-config":
        return _cmd_check_config(args.config)
    if args.command == "review-diff":
        return _cmd_review_diff(args)
    if args.command == "review":
        return _cmd_review(args)
    build_parser().print_help()
    return 2


def _cmd_check_config(config_path: str | None) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for cfg_field in fields(config):
        print(f"{cfg_field.name}: {getattr(config, cfg_field.name)}")
    return 0


def _cmd_review_diff(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        _apply_api_format_override(config, args)
        instructions = resolve_instructions(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        diff_text = Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read diff file: {exc}", file=sys.stderr)
        return 2
    return _run_and_report(
        config,
        diff_text,
        pr_title="",
        pr_number=None,
        json_out=args.json_out,
        sarif_out=args.sarif_out,
        fail_on_severity=_effective_fail_on_severity(config, args),
        reviewed_commit=args.reviewed_commit,
        instructions=instructions,
    )


def _cmd_review(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        _apply_api_format_override(config, args)
        instructions = resolve_instructions(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parts = args.repo.split("/")
    if len(parts) != 2 or not all(parts):
        print("error: --repo must be OWNER/REPO", file=sys.stderr)
        return 2
    owner, repo = parts
    token = github_token_from_env()
    if not token:
        print("error: GITHUB_TOKEN is not set", file=sys.stderr)
        return 3
    client = GitHubClient(token=token)
    try:
        pull = client.get_pull(owner, repo, args.pr)
        diff_text = client.get_pull_diff(owner, repo, args.pr)
    except GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    title = pull.get("title", "") if isinstance(pull, dict) else ""
    summary, findings, files, state = _prepare_review(
        config,
        diff_text,
        pr_title=title,
        pr_number=args.pr,
        reviewed_commit=args.reviewed_commit,
        instructions=instructions,
    )
    if not args.dry_run:
        try:
            _post_review(
                client, owner, repo, args.pr, config, summary, findings, files, state
            )
        except GitHubError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
    print(summary)
    return _finish_outputs(
        findings,
        args.json_out,
        _effective_fail_on_severity(config, args),
        sarif_out=args.sarif_out,
        severity_threshold=config.severity_threshold,
    )


def _post_review(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    config: Config,
    summary: str,
    findings: list[Finding],
    files: list[DiffFile],
    review_state: ReviewState | None = None,
) -> None:
    """Post the summary as an idempotent issue comment, then (in inline
    mode) any inline comments as a review.

    The summary comment is found by its hidden marker and updated in
    place, so re-pushes never spam the PR with a new summary each time.
    Inline comments go through create_review only when there is at least
    one to post, with a brief body — the full findings table lives in the
    summary comment, not in every review. Thread resolution uses
    ``review_state`` when given: the severity-filtered ``findings`` are
    not a complete view of the PR, so resolving against them would
    close threads whose finding was only filtered out or truncated.
    """
    existing = _find_summary_comment(client, owner, repo, pr_number)
    if existing is not None:
        try:
            client.update_issue_comment(owner, repo, existing["id"], summary)
        except GitHubError as exc:
            if exc.status in (403, 404):
                # The comment vanished (or the token lost write access)
                # between list and update: post a fresh summary instead
                # of crashing the whole review with exit code 3.
                client.create_issue_comment(owner, repo, pr_number, summary)
            else:
                raise
    else:
        client.create_issue_comment(owner, repo, pr_number, summary)
    if config.mode != "inline":
        return
    # Deduplicate BEFORE the max_inline_comments cut: previously posted
    # findings would otherwise crowd out new findings on re-pushes.
    inline = build_inline_comments(findings, files)
    if inline:
        inline = _dedupe_inline_comments(client, owner, repo, pr_number, inline)
    inline = inline[: config.max_inline_comments]
    if inline:
        body = (
            f"pr-sentinel posted {len(inline)} inline comment(s) below; "
            "the full findings table is in the summary comment."
        )
        client.create_review(
            owner, repo, pr_number, body=body, comments=inline, event="COMMENT"
        )
    # Best effort only: resolving stale threads must never fail the
    # review, so any error here becomes a warning, not an exception.
    # The resolver sees the UNFILTERED findings: a finding hidden by
    # the severity threshold is still an open issue, and coverage
    # tells it which locations the review never saw.
    resolver_findings = (
        review_state.all_findings if review_state is not None else findings
    )
    resolver_coverage = review_state.coverage if review_state is not None else None
    _resolve_outdated_threads(
        client, owner, repo, pr_number, resolver_findings, resolver_coverage
    )


def _normalize_inline_body(body: str) -> str:
    """Collapse whitespace so identical comments compare equal even
    when GitHub normalizes line endings or trailing spaces."""
    return " ".join(body.split())


def _dedupe_inline_comments(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    comments: list[dict[str, str | int]],
) -> list[dict[str, str | int]]:
    """Drop inline comments we already posted on this PR.

    An existing review comment counts as a duplicate when it was posted
    by this bot / token user, carries our inline marker, and matches
    the new comment on (path, line, normalized body). A failed listing
    does not block the review: the comments are posted as usual and a
    warning is printed.
    """
    try:
        existing = client.list_pull_review_comments(owner, repo, pr_number)
    except GitHubError as exc:
        print(
            f"warning: could not list existing review comments "
            f"({exc}); posting inline comments without deduplication",
            file=sys.stderr,
        )
        return comments
    seen: set[tuple[str, int, str]] = set()
    for comment in existing:
        body = comment.get("body")
        if not (isinstance(body, str) and INLINE_MARKER in body):
            continue
        if not _is_own_comment(client, comment):
            continue
        path = comment.get("path")
        line = comment.get("line")
        if not isinstance(path, str) or not isinstance(line, int):
            continue
        seen.add((path, line, _normalize_inline_body(body)))
    kept = []
    for comment in comments:
        body = comment.get("body")
        key = (
            str(comment.get("path")),
            int(comment["line"]) if isinstance(comment.get("line"), int) else -1,
            _normalize_inline_body(body) if isinstance(body, str) else "",
        )
        if key in seen:
            continue
        kept.append(comment)
        seen.add(key)
    return kept


def _resolve_outdated_threads(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    findings: list[Finding],
    coverage: ReviewCoverage | None = None,
) -> None:
    """Resolve our own inline threads whose finding is gone.

    A thread is resolved when it is still open, belongs to us (its
    comments carry the inline marker and an author that is this bot or
    the token user), and its (path, line) is no longer in the current
    findings. A thread whose line is null — its diff position is
    outdated — is resolved only when its file has no current findings
    at any line: if a finding remains in that file it may have moved
    to another line, and resolving the old thread would be wrong. Any
    failure (GraphQL error, missing permission) prints a warning and
    the review still succeeds — this is best effort.

    ``findings`` must be the UNFILTERED findings: a finding hidden by
    the severity threshold is still open, so resolving against the
    filtered list would close its thread as if it were fixed.
    ``coverage`` (when given) guards truncation the same way: a thread
    in a dropped file, or on a line inside a dropped hunk, is never
    resolved — the review never saw that location. A null-line thread
    is resolved only when its file was fully reviewed, because with
    no line the location cannot be checked against a single hunk.
    """
    current: set[tuple[str, int]] = {
        (finding.file, finding.line) for finding in findings if finding.line is not None
    }
    current_paths: set[str] = {finding.file for finding in findings}
    try:
        threads = client.list_review_threads(owner, repo, pr_number)
    except GitHubError as exc:
        print(
            f"warning: could not list review threads ({exc}); "
            "leaving existing inline comments unresolved",
            file=sys.stderr,
        )
        return
    me: str | None = None
    try:
        authenticated = client.get_authenticated_user()
        if isinstance(authenticated, dict):
            login = authenticated.get("login")
            me = login if isinstance(login, str) else None
    except GitHubError:
        me = None
    for thread in threads:
        if thread.get("isResolved"):
            continue
        path = thread.get("path")
        if not isinstance(path, str):
            continue
        line = thread.get("line")
        if isinstance(line, int):
            if (path, line) in current:
                continue
            if coverage is not None and coverage.was_dropped(path, line):
                # The finding's location was never reviewed on this
                # push: it may still be there, just dropped by a
                # size/file limit, so it must not look "fixed".
                continue
        elif path in current_paths:
            # Outdated position (null line): only certainly stale when
            # the file has no current findings left at all.
            continue
        elif coverage is not None and not coverage.is_fully_reviewed(path):
            # With no usable line the location cannot be checked
            # against one hunk: a dropped hunk anywhere in the file
            # may still hold the finding.
            continue
        if not _is_own_thread(thread, me):
            continue
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        try:
            client.resolve_review_thread(thread_id)
        except GitHubError as exc:
            print(
                f"warning: could not resolve review thread {thread_id} ({exc})",
                file=sys.stderr,
            )


def _is_own_thread(thread: dict[str, Any], me: str | None) -> bool:
    """True when a review thread was opened by this bot / token user.

    Only the thread's root comment (``nodes[0]``) is inspected: it must
    carry our inline marker and be authored by a bot account. In
    GitHub Actions the GraphQL author login is ``github-actions``
    (without the ``[bot]`` suffix) and ``GET /user`` fails with 403 on
    ``GITHUB_TOKEN``, so the author ``__typename == "Bot"`` is the
    check that actually works there; the login checks cover
    personal-access-token runs.
    """
    comments = thread.get("comments") or {}
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    if not isinstance(nodes, list) or not nodes:
        return False
    root = nodes[0]
    if not isinstance(root, dict):
        return False
    body = root.get("body")
    if not (isinstance(body, str) and INLINE_MARKER in body):
        return False
    author = root.get("author")
    if not isinstance(author, dict):
        return False
    login = author.get("login")
    if not isinstance(login, str) or not login:
        return False
    return author.get("__typename") == "Bot" or login == me or login.endswith("[bot]")


def _find_summary_comment(
    client: GitHubClient, owner: str, repo: str, pr_number: int
) -> dict[str, Any] | None:
    """The bot's own summary comment, if any, found by its hidden marker.

    Only a comment authored by this bot (or the token user) counts. An
    unrelated user quoting the marker in a public repo must never be
    PATCHed: that update would fail with 403/404 and crash the review.
    """
    for comment in client.list_issue_comments(owner, repo, pr_number):
        body = comment.get("body")
        if not (isinstance(body, str) and SUMMARY_MARKER in body):
            continue
        if _is_own_comment(client, comment):
            return comment
    return None


def _is_own_comment(client: GitHubClient, comment: dict[str, Any]) -> bool:
    """True when the comment was posted by this bot / token user.

    The rule: ``user.type == "Bot"`` (e.g. github-actions[bot] under
    GITHUB_TOKEN), or the author's login matches the authenticated token
    user (GET /user, cached on the client — covers personal-access-token
    runs). A failed /user lookup means "unknown": only bot comments
    count. A comment with no usable author info is never treated as ours.
    """
    user = comment.get("user")
    if not isinstance(user, dict):
        return False
    if user.get("type") == "Bot":
        return True
    login = user.get("login")
    if not isinstance(login, str) or not login:
        return False
    try:
        me = client.get_authenticated_user()
    except GitHubError:
        return False
    return isinstance(me, dict) and me.get("login") == login


def _run_and_report(
    config: Config,
    diff_text: str,
    pr_title: str,
    pr_number: int | None,
    json_out: str | None,
    fail_on_severity: str | None = None,
    sarif_out: str | None = None,
    reviewed_commit: str | None = None,
    instructions: str | None = None,
) -> int:
    summary, findings, _files, _state = _prepare_review(
        config,
        diff_text,
        pr_title=pr_title,
        pr_number=pr_number,
        reviewed_commit=reviewed_commit,
        instructions=instructions,
    )
    print(summary)
    return _finish_outputs(
        findings,
        json_out,
        fail_on_severity,
        sarif_out=sarif_out,
        severity_threshold=config.severity_threshold,
    )


def _normalize_api_format_value(value: str) -> str:
    """Normalize an --api-format value: case- and whitespace-insensitive.
    argparse applies this before its choices check, so an uppercase
    value works and an invalid one still exits 2."""
    return value.strip().lower()


def _apply_api_format_override(config: Config, args: argparse.Namespace) -> None:
    """The --api-format flag wins over the config file (like
    --fail-on-severity). A missing flag leaves the config value alone."""
    raw = getattr(args, "api_format", None)
    if raw is not None:
        config.api_format = raw
        config.validate()


def _normalize_gate_value(value: str) -> str:
    """Normalize a --fail-on-severity value: case- and
    whitespace-insensitive, blank means "none". argparse applies this
    before its choices check, so an uppercase value works and an
    invalid one still exits 2."""
    normalized = value.strip().lower()
    return "none" if normalized == "" else normalized


def _effective_fail_on_severity(config: Config, args: argparse.Namespace) -> str | None:
    """The CI gate severity: the CLI flag wins over the config file.

    ``"none"`` (from either source) means "no gate"; the config value is
    already normalized by ``Config.__post_init__`` and the CLI value by
    the argument parser, but normalize defensively here too so direct
    callers get the same treatment.
    """
    raw = getattr(args, "fail_on_severity", None)
    if raw is not None:
        normalized = raw.strip().lower() if isinstance(raw, str) else raw
        return None if normalized in ("", "none") else normalized
    return config.fail_on_severity


def _meets_gate(findings: list[Finding], fail_on_severity: str) -> bool:
    """True when a reported finding meets or exceeds the gate severity."""
    threshold_rank = SEVERITY_RANK[fail_on_severity]
    return any(
        SEVERITY_RANK[finding.severity] <= threshold_rank for finding in findings
    )


def _finish_outputs(
    findings: list[Finding],
    json_out: str | None,
    fail_on_severity: str | None,
    sarif_out: str | None = None,
    severity_threshold: str | None = None,
) -> int:
    """Write outputs, then apply the CI gate — in that order.

    The gate is evaluated last on purpose: when a run fails the gate
    (exit 1), the review has already been posted and every output file
    already written, so CI still has the full report to look at.
    """
    code = _write_json(findings, json_out)
    if code != 0:
        return code
    code = _write_sarif(findings, sarif_out)
    if code != 0:
        return code
    if fail_on_severity is not None:
        _warn_gate_below_threshold(fail_on_severity, severity_threshold)
        if _meets_gate(findings, fail_on_severity):
            count = sum(
                1
                for finding in findings
                if SEVERITY_RANK[finding.severity] <= SEVERITY_RANK[fail_on_severity]
            )
            noun = "finding" if count == 1 else "findings"
            verb = "meets" if count == 1 else "meet"
            verb2 = "exceeds" if count == 1 else "exceed"
            print(
                f"error: fail_on_severity={fail_on_severity}: {count} {noun} "
                f"{verb} or {verb2} the threshold",
                file=sys.stderr,
            )
            return 1
    return 0


def _warn_gate_below_threshold(
    fail_on_severity: str, severity_threshold: str | None
) -> None:
    """Warn when the gate is lower than the reporting threshold.

    Findings below severity_threshold are filtered out before the gate
    runs, so a lower gate can only ever fire on findings at or above
    the threshold — the lower severities it names can never trigger it.
    """
    if severity_threshold is None:
        return
    if SEVERITY_RANK[fail_on_severity] > SEVERITY_RANK[severity_threshold]:
        print(
            f"warning: fail_on_severity={fail_on_severity} is lower "
            f"(less severe) than severity_threshold={severity_threshold}; "
            f"findings below {severity_threshold} are filtered out "
            "before the gate runs, so the gate can only fire on "
            f"findings at or above {severity_threshold}",
            file=sys.stderr,
        )


@dataclass
class ReviewCoverage:
    """Which parts of the diff the review actually saw.

    A thread may only be resolved when its location was reviewed: a
    finding that is missing only because its file or hunk was dropped
    (``ignore_paths``, ``max_files`` or ``max_diff_chars``) must never
    look "fixed". Ranges are half-open new-file line intervals
    ``[start, end)`` of the dropped hunks.
    """

    dropped_files: set[str] = field(default_factory=set)
    dropped_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def was_dropped(self, path: str, line: int) -> bool:
        """True when the line sits in a dropped file or dropped hunk."""
        if path in self.dropped_files:
            return True
        return any(
            start <= line < end for start, end in self.dropped_ranges.get(path, ())
        )

    def is_fully_reviewed(self, path: str) -> bool:
        """True when every hunk of the file survived truncation."""
        return path not in self.dropped_files and not self.dropped_ranges.get(path)


@dataclass
class ReviewState:
    """Unfiltered findings plus coverage, for thread resolution.

    The summary and inline comments use the severity-filtered
    findings, but the resolver must see the complete view: a finding
    hidden by the severity threshold is still an open issue.
    """

    all_findings: list[Finding]
    coverage: ReviewCoverage


def _compute_coverage(
    all_paths: set[str],
    original_ranges: dict[str, list[tuple[int, int]]],
    kept_files: list[DiffFile],
) -> ReviewCoverage:
    """Coverage of the review: which files/hunks were dropped.

    ``all_paths``/``original_ranges`` describe the parsed diff before
    any limit; ``kept_files`` is what survived. A file dropped by
    ``ignore_paths``, ``max_files`` or the size limit lands in
    ``dropped_files``; a hunk dropped by the size limit lands in
    ``dropped_ranges``. Kept hunks are compared by range, so the
    result does not depend on which end was trimmed.
    """
    kept_paths = {diff_file.path for diff_file in kept_files}
    dropped_files = set(all_paths) - kept_paths
    dropped_ranges: dict[str, list[tuple[int, int]]] = {}
    for diff_file in kept_files:
        kept_ranges = {
            (hunk.new_start, hunk.new_start + hunk.new_count)
            for hunk in diff_file.hunks
        }
        dropped = [
            hunk_range
            for hunk_range in original_ranges.get(diff_file.path, [])
            if hunk_range not in kept_ranges
        ]
        if dropped:
            dropped_ranges[diff_file.path] = dropped
    return ReviewCoverage(dropped_files=dropped_files, dropped_ranges=dropped_ranges)


def _prepare_review(
    config: Config,
    diff_text: str,
    pr_title: str = "",
    pr_number: int | None = None,
    reviewed_commit: str | None = None,
    instructions: str | None = None,
) -> tuple[str, list[Finding], list[DiffFile], ReviewState]:
    """Parse, limit and review the diff, then filter and summarize.

    Order matters: ignored files are dropped before the file-count limit
    and before any review work, so they consume neither the ``max_files``
    quota nor AI tokens. Files are then sorted by risk tier, so the
    size limit and the file-count limit drop the lowest-risk files
    first. The size limit is applied after parsing, by dropping whole
    trailing hunks/files — never by cutting raw text mid-hunk.

    The returned state carries the pre-filter findings and the review
    coverage, so thread resolution can tell a genuinely fixed finding
    apart from one that was only filtered out or truncated away.
    """
    files = parse_diff(diff_text)
    # Snapshot the pre-limit shape before anything mutates or drops
    # files: _limit_diff_size pops hunks in place, so the dropped
    # ranges must be recorded from the original hunks.
    original_ranges = {
        diff_file.path: [
            (hunk.new_start, hunk.new_start + hunk.new_count)
            for hunk in diff_file.hunks
        ]
        for diff_file in files
    }
    all_paths = {diff_file.path for diff_file in files}
    files = _drop_ignored_files(files, config.ignore_paths)
    files = sort_by_risk(files)
    files = _limit_diff_size(files, config.max_diff_chars)
    if len(files) > config.max_files:
        print(
            f"warning: reviewing the first {config.max_files} of {len(files)} files",
            file=sys.stderr,
        )
        files = files[: config.max_files]
    coverage = _compute_coverage(all_paths, original_ranges, files)
    all_findings, ai_used = _collect_findings(files, config, instructions=instructions)
    findings = sort_findings(
        filter_findings(all_findings, threshold=config.severity_threshold)
    )
    summary = build_summary(
        findings,
        pr_title=pr_title,
        pr_number=pr_number,
        ai_used=ai_used,
        reviewed_commit=reviewed_commit,
    )
    return (
        summary,
        findings,
        files,
        ReviewState(all_findings=all_findings, coverage=coverage),
    )


def _drop_ignored_files(
    files: list[DiffFile], ignore_paths: list[str]
) -> list[DiffFile]:
    """Drop files matching an ignore glob; ignored files are never reviewed."""
    if not ignore_paths:
        return files
    kept = [
        diff_file
        for diff_file in files
        if not any(fnmatch.fnmatch(diff_file.path, pattern) for pattern in ignore_paths)
    ]
    dropped = len(files) - len(kept)
    if dropped:
        noun = "file" if dropped == 1 else "files"
        print(
            f"warning: ignoring {dropped} {noun} matching ignore_paths",
            file=sys.stderr,
        )
    return kept


def _limit_diff_size(files: list[DiffFile], max_chars: int) -> list[DiffFile]:
    """Drop whole trailing hunks/files until the rendered diff fits.

    The limit is applied after parsing, so a file is never cut in the
    middle of a hunk. If a single hunk alone is larger than the limit it
    is kept whole (and warned about): dropping it would mean reviewing
    nothing at all, and chunking already keeps oversized hunks whole.
    """
    total = sum(len(diff_file.render_patch()) for diff_file in files)
    if total <= max_chars:
        return files
    dropped_hunks = 0
    dropped_files = 0
    while total > max_chars and files:
        last = files[-1]
        if not last.hunks:
            files.pop()
            dropped_files += 1
        elif sum(len(f.hunks) for f in files) > 1:
            last.hunks.pop()
            dropped_hunks += 1
            if not last.hunks:
                files.pop()
                dropped_files += 1
        else:
            break  # one hunk alone exceeds the limit; keep it whole
        total = sum(len(diff_file.render_patch()) for diff_file in files)
    parts = []
    if dropped_files:
        noun = "file" if dropped_files == 1 else "files"
        parts.append(f"{dropped_files} {noun}")
    if dropped_hunks:
        noun = "hunk" if dropped_hunks == 1 else "hunks"
        parts.append(f"{dropped_hunks} {noun}")
    message = (
        f"warning: diff exceeds max_diff_chars ({max_chars} characters); "
        f"dropped {' and '.join(parts)}"
    )
    if files and total > max_chars:
        message += "; one remaining hunk alone exceeds the limit, kept whole"
    print(message, file=sys.stderr)
    return files


def _collect_findings(
    files: list[DiffFile], config: Config, instructions: str | None = None
) -> tuple[list[Finding], bool]:
    """Run static checks and (when an API key is set) the AI reviewer.

    Returns the findings and whether the AI reviewer actually ran, so the
    summary can say which kind of findings these are.
    """
    findings = _run_static_checks(files)
    api_key = api_key_from_env()
    if api_key is None:
        print(
            "warning: PR_SENTINEL_API_KEY is not set; running static checks only",
            file=sys.stderr,
        )
        return findings, False
    try:
        from .reviewer import Reviewer, ReviewerError
    except ImportError:
        print(
            "warning: reviewer module is not available; running static checks only",
            file=sys.stderr,
        )
        return findings, False
    if config.api_base_url.startswith("http://"):
        print(
            "warning: api_base_url uses plain http://; the API key is sent "
            "in cleartext",
            file=sys.stderr,
        )
    try:
        reviewer = Reviewer(
            api_key=api_key,
            base_url=config.api_base_url,
            model=config.model,
            api_format=config.api_format,
        )
        findings.extend(
            reviewer.review(
                files, max_chars=config.max_diff_chars, instructions=instructions
            )
        )
    except ReviewerError as exc:
        print(
            f"warning: AI review failed ({exc}); continuing with static checks only",
            file=sys.stderr,
        )
        return findings, False
    return findings, True


def _run_static_checks(files: list[DiffFile]) -> list[Finding]:
    """Run the static checks module, if it is available."""
    try:
        from . import static_checks
    except ImportError:
        print(
            "warning: static_checks module is not available; skipping static checks",
            file=sys.stderr,
        )
        return []
    for name in _STATIC_CHECK_FUNCTIONS:
        func = getattr(static_checks, name, None)
        if callable(func):
            return list(func(files))
    print(
        "warning: static_checks module has no known entry point; "
        "skipping static checks",
        file=sys.stderr,
    )
    return []


def _write_json(findings: list[Finding], json_out: str | None) -> int:
    if json_out is None:
        return 0
    try:
        path = Path(json_out)
        # Nested paths like reports/findings.json work cleanly: the
        # parent directory is created when it does not exist yet.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(findings_to_json(findings), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write JSON output: {exc}", file=sys.stderr)
        return 2
    return 0


def _write_sarif(findings: list[Finding], sarif_out: str | None) -> int:
    if sarif_out is None:
        return 0
    try:
        path = Path(sarif_out)
        # Nested paths like reports/sentinel.sarif work cleanly: the
        # parent directory is created when it does not exist yet.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(findings_to_sarif(findings), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write SARIF output: {exc}", file=sys.stderr)
        return 2
    return 0
