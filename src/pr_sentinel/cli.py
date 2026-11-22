"""Command-line interface for pr-sentinel."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

from . import __version__
from .config import (
    Config,
    ConfigError,
    api_key_from_env,
    github_token_from_env,
    load_config,
)
from .diff import DiffFile, parse_diff
from .findings import Finding, filter_findings, sort_findings
from .github_client import GitHubClient, GitHubError
from .reporter import build_inline_comments, build_summary, findings_to_json

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

    review_diff = subparsers.add_parser(
        "review-diff", help="Review a local patch file (no GitHub needed)."
    )
    review_diff.add_argument("--file", required=True, help="Path to a .patch file.")
    review_diff.add_argument("--config", help="Path to a config file.")
    review_diff.add_argument("--json-out", help="Write findings as JSON to this path.")

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
    for field in fields(config):
        print(f"{field.name}: {getattr(config, field.name)}")
    return 0


def _cmd_review_diff(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        diff_text = Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read diff file: {exc}", file=sys.stderr)
        return 2
    return _run_and_report(
        config, diff_text, pr_title="", pr_number=None, json_out=args.json_out
    )


def _cmd_review(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parts = args.repo.split("/")
    if len(parts) != 2 or not all(parts):
        print("error: --repo must be OWNER/REPO", file=sys.stderr)
        return 2
    owner, repo = parts
    token = github_token_from_env()
    if token is None:
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
    summary, findings, files = _prepare_review(
        config, diff_text, pr_title=title, pr_number=args.pr
    )
    if not args.dry_run:
        if config.mode == "summary":
            inline: list[dict] = []
        else:
            inline = build_inline_comments(findings, files)[
                : config.max_inline_comments
            ]
        try:
            client.create_review(
                owner,
                repo,
                args.pr,
                body=summary,
                comments=inline,
                event="COMMENT",
            )
        except GitHubError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
    print(summary)
    return _write_json(findings, args.json_out)


def _run_and_report(
    config: Config,
    diff_text: str,
    pr_title: str,
    pr_number: int | None,
    json_out: str | None,
) -> int:
    summary, findings, _files = _prepare_review(
        config, diff_text, pr_title=pr_title, pr_number=pr_number
    )
    print(summary)
    return _write_json(findings, json_out)


def _prepare_review(
    config: Config,
    diff_text: str,
    pr_title: str = "",
    pr_number: int | None = None,
) -> tuple[str, list[Finding], list[DiffFile]]:
    """Truncate/limit the diff, collect findings, filter and summarize."""
    if len(diff_text) > config.max_diff_chars:
        print(
            f"warning: diff truncated from {len(diff_text)} to "
            f"{config.max_diff_chars} characters",
            file=sys.stderr,
        )
        diff_text = diff_text[: config.max_diff_chars]
    files = parse_diff(diff_text)
    if len(files) > config.max_files:
        print(
            f"warning: reviewing the first {config.max_files} of {len(files)} files",
            file=sys.stderr,
        )
        files = files[: config.max_files]
    findings = sort_findings(
        filter_findings(
            _collect_findings(files, config),
            threshold=config.severity_threshold,
            ignore_paths=config.ignore_paths,
        )
    )
    summary = build_summary(findings, pr_title=pr_title, pr_number=pr_number)
    return summary, findings, files


def _collect_findings(files: list[DiffFile], config: Config) -> list[Finding]:
    """Run static checks and (when an API key is set) the AI reviewer."""
    findings = _run_static_checks(files)
    api_key = api_key_from_env()
    if api_key is None:
        print(
            "warning: PR_SENTINEL_API_KEY is not set; running static checks only",
            file=sys.stderr,
        )
        return findings
    try:
        from .reviewer import Reviewer, ReviewerError
    except ImportError:
        print(
            "warning: reviewer module is not available; running static checks only",
            file=sys.stderr,
        )
        return findings
    try:
        reviewer = Reviewer(
            api_key=api_key, base_url=config.api_base_url, model=config.model
        )
        findings.extend(reviewer.review(files, max_chars=config.max_diff_chars))
    except ReviewerError as exc:
        print(
            f"warning: AI review failed ({exc}); continuing with static checks only",
            file=sys.stderr,
        )
    return findings


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
        Path(json_out).write_text(findings_to_json(findings), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write JSON output: {exc}", file=sys.stderr)
        return 2
    return 0
