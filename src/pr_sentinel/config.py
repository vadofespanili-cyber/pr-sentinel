"""Configuration loading and validation for pr-sentinel.

Config file (``.pr-sentinel.yaml`` / ``.pr-sentinel.yml``) uses a small
YAML subset: top-level mappings, nested mappings, sequences of scalars and
flow lists. No external YAML library is needed (stdlib only).
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, cast

from .findings import SEVERITIES

CONFIG_FILENAMES = (".pr-sentinel.yml", ".pr-sentinel.yaml")
KNOWN_KEYS = {
    "model",
    "severity_threshold",
    "ignore_paths",
    "max_files",
    "max_diff_chars",
    "mode",
    "max_inline_comments",
    "api_base_url",
}
MODES = ("inline", "summary")
DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
ENV_API_KEY = "PR_SENTINEL_API_KEY"
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"


class ConfigError(ValueError):
    """Raised when a config file is missing, malformed or invalid."""


@dataclass
class Config:
    model: str = "gpt-5"
    severity_threshold: str = "medium"
    ignore_paths: list[str] = field(default_factory=list)
    max_files: int = 50
    max_diff_chars: int = 100_000
    mode: str = "inline"
    max_inline_comments: int = 20
    api_base_url: str = DEFAULT_API_BASE_URL

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ConfigError("model must be a non-empty string")
        if self.severity_threshold not in SEVERITIES:
            raise ConfigError(
                f"severity_threshold must be one of {list(SEVERITIES)}, "
                f"got {self.severity_threshold!r}"
            )
        if self.mode not in MODES:
            raise ConfigError(f"mode must be one of {list(MODES)}, got {self.mode!r}")
        if not isinstance(self.ignore_paths, list) or not all(
            isinstance(p, str) for p in self.ignore_paths
        ):
            raise ConfigError("ignore_paths must be a list of strings")
        for name in ("max_files", "max_diff_chars", "max_inline_comments"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigError(f"{name} must be an integer >= 1, got {value!r}")
        # A real URL needs a scheme and a host. Plain http:// is still
        # allowed (local test servers use it), but the CLI warns about it
        # when the API key is actually sent; garbage like "httpfoo" has
        # neither a scheme nor a host and is rejected here.
        parsed = (
            urllib.parse.urlparse(self.api_base_url)
            if isinstance(self.api_base_url, str)
            else None
        )
        if (
            parsed is None
            or parsed.scheme not in ("http", "https")
            or not parsed.netloc
        ):
            raise ConfigError(
                f"api_base_url must be an http(s) URL, got {self.api_base_url!r}"
            )


def default_config() -> Config:
    return Config()


def find_config_file(start_dir: str = ".") -> str | None:
    for name in CONFIG_FILENAMES:
        candidate = os.path.join(start_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_config(path: str | None = None) -> Config:
    """Load config from ``path``, auto-discovered file, or defaults."""
    if path is None:
        path = find_config_file()
    if path is None:
        return default_config()
    if not os.path.isfile(path):
        raise ConfigError(f"config file not found: {path}")
    with open(path, encoding="utf-8") as handle:
        return parse_config_text(handle.read())


def parse_config_text(text: str) -> Config:
    data = parse_yaml_subset(text)
    if data is None:
        return default_config()
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping of keys to values")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")
    config = Config(**data)
    config.validate()
    return config


def api_key_from_env(env: dict[str, str] | None = None) -> str | None:
    return (env if env is not None else os.environ).get(ENV_API_KEY) or None


def github_token_from_env(env: dict[str, str] | None = None) -> str | None:
    return (env if env is not None else os.environ).get(ENV_GITHUB_TOKEN) or None


# --- Minimal YAML subset parser -------------------------------------------
# Supports: mappings (indent-based), sequences ("- item"), scalars (plain,
# single/double quoted, int, float, bool, null), flow lists "[a, b]",
# full-line and trailing comments. Anything else raises ConfigError.


def parse_yaml_subset(
    text: str,
) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
    lines = _logical_lines(text)
    if not lines:
        return None
    value, next_index = _parse_block(lines, 0, 0)
    if next_index != len(lines):
        raise ConfigError(f"unexpected content at line {lines[next_index][2]}")
    return cast(dict[str, Any] | list[Any] | str | int | float | bool | None, value)


def _logical_lines(text: str) -> list[tuple[int, str, int]]:
    """Return (indent, content, line_no) for non-blank, non-comment lines."""
    out = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        if "\t" in stripped[:indent]:
            raise ConfigError(f"tabs are not allowed for indentation (line {line_no})")
        out.append((indent, stripped.strip(), line_no))
    return out


def _strip_comment(raw: str) -> str:
    if raw.lstrip().startswith("#"):
        return ""
    in_single = in_double = False
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "\\" and (in_single or in_double) and i + 1 < len(raw):
            # A backslash escapes the next character inside a quoted
            # string: an escaped quote must not toggle the quote state.
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif (
            char == "#"
            and not in_single
            and not in_double
            and (i == 0 or raw[i - 1] in " \t")
        ):
            return raw[:i]
        i += 1
    return raw


def _parse_block(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(lines):
        return None, index
    line_indent, content, _line_no = lines[index]
    if line_indent < indent:
        return None, index
    if content.startswith("- ") or content == "-":
        return _parse_sequence(lines, index, line_indent)
    if _is_mapping_line(content):
        return _parse_mapping(lines, index, line_indent)
    return _parse_scalar(content), index + 1


def _is_mapping_line(content: str) -> bool:
    if ":" not in content:
        return False
    key = content.split(":", 1)[0]
    return bool(key) and " " not in key.strip(" \"'")


def _parse_mapping(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, content, line_no = lines[index]
        if line_indent != indent or not _is_mapping_line(content):
            break
        key, _, rest = content.partition(":")
        parsed_key = _parse_scalar(key.strip())
        if not isinstance(parsed_key, str):
            raise ConfigError(f"mapping key must be a string (line {line_no})")
        key = parsed_key
        rest = rest.strip()
        if rest in ("|", ">", "|-", "|+", ">-", ">+"):
            # Block scalar: following more-indented lines are one string.
            # (Blank lines inside are dropped: _logical_lines strips them.
            # The config schema has no multi-line string field, so this is
            # enough for workflow-style files.)
            block_lines = []
            j = index + 1
            while j < len(lines) and lines[j][0] > indent:
                block_lines.append(lines[j][1])
                j += 1
            if rest.startswith("|"):
                block_value = "\n".join(block_lines)
                if not rest.endswith("-"):
                    block_value += "\n"
            else:
                block_value = " ".join(block_lines)
                if not rest.endswith("-"):
                    block_value += "\n"
            result[key] = block_value
            index = j
        elif rest:
            result[key] = _parse_scalar(rest)
            index += 1
        else:
            if index + 1 < len(lines) and lines[index + 1][0] > indent:
                value, index = _parse_block(lines, index + 1, lines[index + 1][0])
                result[key] = value
            else:
                result[key] = None
                index += 1
    return result, index


def _parse_sequence(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, content, line_no = lines[index]
        if line_indent != indent or not (content.startswith("- ") or content == "-"):
            break
        item = content[1:].strip()
        if not item:
            if index + 1 < len(lines) and lines[index + 1][0] > indent:
                value, index = _parse_block(lines, index + 1, lines[index + 1][0])
                result.append(value)
            else:
                result.append(None)
                index += 1
        elif _is_mapping_line(item):
            # Inline mapping inside a sequence item ("- key: value").
            # sub_lines[0] is synthetic; consumed counts it, so the next
            # unconsumed real line is at index + consumed.
            sub_lines = [(indent + 2, item, line_no)] + lines[index + 1 :]
            value, consumed = _parse_mapping(sub_lines, 0, indent + 2)
            result.append(value)
            index += consumed
        else:
            result.append(_parse_scalar(item))
            index += 1
    return result, index


def _split_flow(inner: str) -> list[str]:
    parts, depth, current = [], 0, ""
    in_single = in_double = False
    for char in inner:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char in "[{" and not in_single and not in_double:
            depth += 1
        elif char in "]}" and not in_single and not in_double:
            depth -= 1
        elif char == "," and depth == 0 and not in_single and not in_double:
            parts.append(current)
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current)
    return parts


def _parse_scalar(value: str) -> object:
    value = value.strip()
    if value in ("", "~", "null", "Null", "NULL"):
        return None
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        else:
            inner = inner.replace("''", "'")
        return inner
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_flow(inner)]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
