"""Static checks over added diff lines: leaked secrets, dangerous calls, size.

Only added lines are inspected: removed lines and context lines never
produce findings. Secret values are never echoed in messages, so findings
are safe to log and post as review comments.
"""

from __future__ import annotations

import re

from .diff import DiffFile
from .findings import Finding

# (pattern, rule, message, suggestion)
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    (
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        "leaked-openai-key",
        "Possible OpenAI API key in added code.",
        (
            "Remove the key, revoke it, and load it from an environment "
            "variable or a secret store instead."
        ),
    ),
    (
        re.compile(r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]+"),
        "leaked-github-token",
        "Possible GitHub token in added code.",
        (
            "Remove the token, revoke it, and load it from an environment "
            "variable or a secret store instead."
        ),
    ),
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "leaked-aws-key",
        "Possible AWS access key ID in added code.",
        (
            "Remove the key, deactivate it in AWS IAM, and load credentials "
            "from the environment or an IAM role instead."
        ),
    ),
    (
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        "leaked-slack-token",
        "Possible Slack token in added code.",
        (
            "Remove the token, revoke it, and load it from an environment "
            "variable or a secret store instead."
        ),
    ),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "leaked-private-key",
        "Private key material in added code.",
        (
            "Remove the key from the repository, revoke/replace it, and "
            "never commit private keys."
        ),
    ),
    (
        re.compile(
            r"(?i)(password|passwd|secret|api[_-]?key|token)"
            r"\s*=\s*['\"][^'\"]{4,}['\"]"
        ),
        "hardcoded-credential",
        "Possible hardcoded credential assigned in added code.",
        (
            "Remove the value and load it from an environment variable or "
            "a secret store instead."
        ),
    ),
]

# (pattern, severity, category, rule, message, suggestion)
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str, str, str, str, str]] = [
    (
        re.compile(r"\beval\s*\("),
        "high",
        "bug",
        "dangerous-eval",
        "Use of eval() on added code.",
        "Avoid eval(); parse or validate the input explicitly instead.",
    ),
    (
        re.compile(r"\bexec\s*\("),
        "high",
        "bug",
        "dangerous-exec",
        "Use of exec() on added code.",
        "Avoid exec(); call the intended function directly instead.",
    ),
    (
        re.compile(r"\bos\.system\s*\("),
        "high",
        "security",
        "dangerous-os-system",
        "Use of os.system() on added code.",
        (
            "Use subprocess.run() with an argument list and shell=False "
            "instead of os.system()."
        ),
    ),
    (
        re.compile(r"\bsubprocess\.\w+\s*\([^)]*shell\s*=\s*True"),
        "high",
        "security",
        "dangerous-shell-true",
        "subprocess call with shell=True on added code.",
        (
            "Pass shell=False with an argument list, or validate and quote "
            "the command if a shell is unavoidable."
        ),
    ),
    (
        re.compile(r"\bpickle\.loads?\s*\("),
        "medium",
        "security",
        "dangerous-pickle-load",
        "Use of pickle.load()/loads() on added code.",
        ("Do not unpickle untrusted data; use a safe format such as JSON instead."),
    ),
]

_YAML_LOAD = re.compile(r"\byaml\.load\s*\(([^)]*)\)")


def run_static_checks(
    files: list[DiffFile], max_added_lines: int = 1000
) -> list[Finding]:
    """Run all static checks over the added lines of ``files``."""
    findings: list[Finding] = []
    for diff_file in files:
        for hunk in diff_file.hunks:
            for line in hunk.lines:
                if line.kind != "add":
                    continue
                findings.extend(_check_line(diff_file.path, line.new_no, line.text))
        if diff_file.added_lines > max_added_lines:
            first_added = next(
                (
                    line.new_no
                    for hunk in diff_file.hunks
                    for line in hunk.lines
                    if line.kind == "add"
                ),
                None,
            )
            findings.append(
                Finding(
                    severity="medium",
                    category="maintainability",
                    file=diff_file.path,
                    line=first_added,
                    message=(
                        f"File adds {diff_file.added_lines} lines, more than "
                        f"the limit of {max_added_lines}."
                    ),
                    suggestion="Split the change into smaller, focused "
                    "commits or files.",
                    rule="large-file",
                )
            )
    return findings


def _check_line(path: str, line_no: int | None, text: str) -> list[Finding]:
    findings: list[Finding] = []
    specific_secret_found = False
    for pattern, rule, message, suggestion in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(
                Finding(
                    severity="critical",
                    category="security",
                    file=path,
                    line=line_no,
                    message=message,
                    suggestion=suggestion,
                    rule=rule,
                )
            )
            # The generic hardcoded-credential pattern is a fallback: it
            # must not double-report a line a specific secret rule already
            # caught (e.g. api_key = "sk-...").
            if rule != "hardcoded-credential":
                specific_secret_found = True
    if specific_secret_found:
        findings = [f for f in findings if f.rule != "hardcoded-credential"]
    for pattern, severity, category, rule, message, suggestion in _DANGEROUS_PATTERNS:
        if pattern.search(text):
            findings.append(
                Finding(
                    severity=severity,
                    category=category,
                    file=path,
                    line=line_no,
                    message=message,
                    suggestion=suggestion,
                    rule=rule,
                )
            )
    match = _YAML_LOAD.search(text)
    if match and "Loader" not in match.group(1):
        findings.append(
            Finding(
                severity="medium",
                category="security",
                file=path,
                line=line_no,
                message="Use of yaml.load() without a Loader on added code.",
                suggestion="Pass Loader=yaml.SafeLoader, or use "
                "yaml.safe_load() instead.",
                rule="dangerous-yaml-load",
            )
        )
    return findings
