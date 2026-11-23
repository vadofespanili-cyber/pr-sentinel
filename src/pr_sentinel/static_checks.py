"""Static checks over added diff lines: leaked secrets, dangerous calls, size.

Only added lines are inspected: removed lines and context lines never
produce findings. Secret values are never echoed in messages, so findings
are safe to log and post as review comments.

False-positive reduction (v0.3.0):

- An added line carrying ``pr-sentinel: ignore`` inside a comment
  (``#``, ``//`` or ``/* ... */`` style, case-insensitive) suppresses
  static findings on that exact line — for intentional fixtures such
  as ``eval()`` in tests.
- Secret matches that are obvious placeholders (dummy/test/example
  values, repeated characters, or very short low-entropy values) are
  suppressed. Real high-entropy secrets are never suppressed: the
  private-key rule is exempt outright, and the entropy gate only fires
  for short values, so genuine AWS keys, project keys and tokens still
  report.
"""

from __future__ import annotations

import math
import re

from .diff import DiffFile
from .findings import Finding

# An inline suppression marker, inside a comment on the same added line:
# "# pr-sentinel: ignore", "// pr-sentinel: ignore" or
# "/* pr-sentinel: ignore */". Case-insensitive. The marker is checked
# after string literals are stripped (see _strip_string_literals), so
# the same words inside a quoted string do not suppress anything.
_IGNORE_MARKER = re.compile(r"(?i)(?:#|//|/\*).*pr-sentinel:\s*ignore")


def _strip_string_literals(line: str) -> str:
    """Blank out quoted string literals on one line.

    A ``# pr-sentinel: ignore`` inside a string (``x = "# ..."; eval(u)``)
    is not a comment marker. A quote only starts a string when a matching
    closing quote exists later on the line, so an apostrophe in a comment
    (``# don't ...``) does not swallow the marker. Unterminated quotes
    are left alone.
    """
    chars = list(line)
    index = 0
    while index < len(chars):
        quote = chars[index]
        if quote not in ("'", '"'):
            index += 1
            continue
        closing = index + 1
        while closing < len(chars):
            if chars[closing] == "\\":
                closing += 2
                continue
            if chars[closing] == quote:
                break
            closing += 1
        if closing >= len(chars):
            # No closing quote on this line: an apostrophe in a comment,
            # not a string literal.
            index += 1
            continue
        for pos in range(index, closing + 1):
            chars[pos] = " "
        index = closing + 1
    return "".join(chars)


# Words that mark a matched secret value as an obvious placeholder —
# but only as a whole component of the value (split on non-alphanumeric
# characters), or as one placeholder word repeated, optionally followed
# by digits: "ghp_test" is a fixture, and so are "test123" and
# "testtest". A mere suffix is NOT enough: real words like "contest",
# "protest" and "latest" end in "test" but are not placeholders.
_PLACEHOLDER_WORDS = (
    "test",
    "dummy",
    "example",
    "fake",
    "sample",
    "placeholder",
    "changeme",
)
# A component that is one placeholder word repeated, optionally
# followed by digits ("test", "testtest", "test123", "dummy99").
_PLACEHOLDER_REPEATED = re.compile(r"^(?:" + "|".join(_PLACEHOLDER_WORDS) + r")+\d*$")
# Well-known published example values (not real credentials): the AWS
# documentation example key. Kept explicit instead of a suffix rule so
# real words ending in a placeholder word are not suppressed.
_KNOWN_EXAMPLE_VALUES = frozenset({"akiaiosfodnn7example"})
# "your_..." values ("your_api_key_here") are placeholders too.
_PLACEHOLDER_SUBSTRING = "your_"

# A secret value shorter than this AND below the entropy threshold is a
# dummy, not a leak. The threshold is deliberately low (2.0 bits/char):
# weak-looking but real values the tests pin down ("hunter2" at 2.81,
# "abcdef" at 2.59) still fire, while degenerate values ("aabbcc" at
# 1.58, "xxxx" at 0.0) do not. Long values never reach this gate: real
# tokens are long and high-entropy.
_PLACEHOLDER_MAX_LEN = 12
_PLACEHOLDER_MAX_ENTROPY = 2.0

# The value inside the quotes of a hardcoded-credential match.
_QUOTED_VALUE = re.compile(r"""['"]([^'"]+)['"]""")
# Split a secret value into components ("ghp_test" -> ["ghp", "test"]).
_COMPONENTS = re.compile(r"[^a-z0-9]+")


def shannon_entropy(value: str) -> float:
    """Shannon entropy of ``value`` in bits per character (stdlib only)."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def _looks_like_placeholder(token: str) -> bool:
    """True for obvious dummy secret values; never True for real secrets.

    A value is a placeholder when it is a well-known published example
    value, it contains ``your_``, one of its components is a placeholder
    word (``ghp_test``) or a placeholder word repeated, optionally
    followed by digits (``test123``, ``testtest``), or it is both short
    and very low-entropy (``xxxx`` has entropy 0.0). A random-looking
    token of real length is never caught: its entropy is far above the
    threshold and its length is above the cap. Real words that merely
    end in a placeholder word (``contest``, ``protest``, ``latest``)
    are not placeholders and still fire.
    """
    lowered = token.lower()
    if lowered in _KNOWN_EXAMPLE_VALUES:
        return True
    if _PLACEHOLDER_SUBSTRING in lowered:
        return True
    for part in _COMPONENTS.split(lowered):
        if not part:
            continue
        if part in _PLACEHOLDER_WORDS or _PLACEHOLDER_REPEATED.match(part):
            return True
    return len(token) < _PLACEHOLDER_MAX_LEN and (
        shannon_entropy(token) < _PLACEHOLDER_MAX_ENTROPY
    )


def _is_placeholder_match(rule: str, matched: str) -> bool:
    """True when a secret-pattern match is a dummy value, not a leak.

    For the generic hardcoded-credential pattern the value inside the
    quotes is tested (``password = "test123"`` is a fixture; the key
    name is not the secret). For specific token patterns the whole
    match is the token.
    """
    if rule == "hardcoded-credential":
        value_match = _QUOTED_VALUE.search(matched)
        if value_match is None:
            return False
        return _looks_like_placeholder(value_match.group(1))
    return _looks_like_placeholder(matched)


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
        # Newer OpenAI project keys (sk-proj-...): the generic pattern
        # above cannot match them, because the dash after "sk-" breaks
        # its [A-Za-z0-9] run.
        re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
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
        "security",
        "dangerous-eval",
        "Use of eval() on added code.",
        "Avoid eval(); parse or validate the input explicitly instead.",
    ),
    (
        re.compile(r"\bexec\s*\("),
        "high",
        "security",
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
                if _IGNORE_MARKER.search(_strip_string_literals(line.text)):
                    # Inline suppression: an intentional fixture (e.g.
                    # eval() in tests) opts out of static findings on
                    # this exact line only. String literals are stripped
                    # first, so the marker words inside a quoted string
                    # do not suppress anything.
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
        match = pattern.search(text)
        if match is None:
            continue
        if rule == "leaked-private-key":
            # Private key headers are always real: the dashes alone
            # would trip the repeated-character placeholder check, so
            # this rule is exempt from placeholder filtering outright.
            pass
        elif _is_placeholder_match(rule, match.group(0)):
            continue
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
