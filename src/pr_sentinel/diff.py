"""Unified diff parsing, line mapping and chunking for model context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# GitHub review comments address a line by its 1-based position inside the
# file's patch, where hunk headers count as lines and counting starts at the
# first "@@" header of the first hunk.


@dataclass
class DiffLine:
    """One line inside a hunk, with its position in the file's patch."""

    kind: str  # "add" | "del" | "context"
    old_no: int | None
    new_no: int | None
    text: str
    position: int  # 1-based position in the file patch (see module docstring)


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)

    def render(self) -> str:
        header = f"@@ -{self.old_start},{self.old_count} "
        header += f"+{self.new_start},{self.new_count} @@\n"
        body = "".join(
            ("+" if line.kind == "add" else "-" if line.kind == "del" else " ")
            + line.text
            + "\n"
            for line in self.lines
        )
        return header + body


@dataclass
class DiffFile:
    path: str
    old_path: str
    added_lines: int = 0
    removed_lines: int = 0
    hunks: list[Hunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False

    def new_line_position(self, new_line_no: int) -> int | None:
        """Patch position of a line, or None if it is not in the diff.

        Added and context lines both have a position in the new file, so
        a finding on either can get an inline comment; only removed lines
        (which do not exist in the new version) have no position.
        """
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.kind != "del" and line.new_no == new_line_no:
                    return line.position
        return None

    def render_patch(self) -> str:
        header = f"diff --git a/{self.old_path} b/{self.path}\n"
        if self.is_new:
            header += "new file mode 100644\n"
        if self.is_deleted:
            header += "deleted file mode 100644\n"
        header += f"--- a/{self.old_path}\n+++ b/{self.path}\n"
        return header + "".join(hunk.render() for hunk in self.hunks)


@dataclass
class DiffChunk:
    """One chunk of diff text sent to the model in a single review call."""

    files: list[DiffFile]
    text: str


def parse_diff(text: str) -> list[DiffFile]:
    """Parse a unified diff into files, hunks and numbered lines."""
    files: list[DiffFile] = []
    current: DiffFile | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    position = 0  # lines seen inside the current file's patch

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            parsed_paths = _split_git_paths(raw[len("diff --git ") :])
            if parsed_paths is None:
                # Fall back to the old whitespace split for malformed
                # headers instead of failing the whole parse.
                parts = raw.split(" ")
                b_path = parts[-1].removeprefix("b/")
                a_path = parts[-2].removeprefix("a/")
            else:
                a_path, b_path = parsed_paths
                a_path = a_path.removeprefix("a/")
                b_path = b_path.removeprefix("b/")
            current = DiffFile(path=b_path, old_path=a_path)
            files.append(current)
            hunk = None
            position = 0
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.is_new = True
            continue
        if raw.startswith("deleted file mode"):
            current.is_deleted = True
            continue
        if raw.startswith("--- "):
            stripped = _strip_prefix(raw[4:].strip(), "a/")
            # "--- /dev/null" (new file): keep the path from "diff --git".
            if stripped != "/dev/null":
                current.old_path = stripped
            continue
        if raw.startswith("+++ "):
            stripped = _strip_prefix(raw[4:].strip(), "b/")
            # "+++ /dev/null" (deleted file): keep the path from "diff --git".
            if stripped != "/dev/null":
                current.path = stripped
            continue
        if raw.startswith("Binary files "):
            continue
        match = _HUNK_HEADER.match(raw)
        if match:
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            hunk = Hunk(old_start, old_count, new_start, new_count)
            current.hunks.append(hunk)
            old_no, new_no = old_start, new_start
            position += 1  # the "@@" header counts as a patch line
            continue
        if hunk is None:
            continue
        if raw.startswith("\\"):
            # "\ No newline at end of file" — carries no line number.
            continue
        if raw == "":
            # An empty context line is a single space in a real diff, but be
            # lenient: treat a bare empty line as context with empty text.
            raw = " "
        kind = raw[0]
        if kind not in ("+", "-", " "):
            continue
        content = raw[1:]
        if kind == "+":
            line = DiffLine("add", None, new_no, content, position + 1)
            current.added_lines += 1
            new_no += 1
        elif kind == "-":
            line = DiffLine("del", old_no, None, content, position + 1)
            current.removed_lines += 1
            old_no += 1
        else:
            line = DiffLine("context", old_no, new_no, content, position + 1)
            old_no += 1
            new_no += 1
        position += 1
        hunk.lines.append(line)
    return files


def render_patch(files: list[DiffFile]) -> str:
    """Render the full patch text for the given files."""
    return "".join(f.render_patch() for f in files)


def chunk_diff(files: list[DiffFile], max_chars: int = 30000) -> list[DiffChunk]:
    """Split files into chunks of at most ``max_chars`` characters.

    Chunking never splits a single hunk. A hunk larger than ``max_chars``
    goes into a chunk of its own, whole.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    chunks: list[DiffChunk] = []
    cur_files: list[DiffFile] = []
    cur_text = ""

    def flush() -> None:
        nonlocal cur_files, cur_text
        if cur_text:
            chunks.append(DiffChunk(files=list(cur_files), text=cur_text))
        cur_files, cur_text = [], ""

    for diff_file in files:
        file_header = (
            f"diff --git a/{diff_file.old_path} b/{diff_file.path}\n"
            f"--- a/{diff_file.old_path}\n+++ b/{diff_file.path}\n"
        )
        for hunk in diff_file.hunks:
            piece = file_header + hunk.render() if hunk is diff_file.hunks[0] else ""
            # The file header belongs to the first hunk of the file; later
            # hunks of the same file continue the same piece.
            if hunk is not diff_file.hunks[0]:
                piece = hunk.render()
            if cur_text and len(cur_text) + len(piece) > max_chars:
                flush()
                # If the new chunk starts mid-file, repeat the file header.
                if hunk is not diff_file.hunks[0]:
                    piece = file_header + hunk.render()
            if not any(f is diff_file for f in cur_files):
                cur_files.append(diff_file)
            cur_text += piece
    flush()
    return chunks


def _strip_prefix(value: str, prefix: str) -> str:
    if value == "/dev/null":
        return value
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        # Git C-style-quotes paths with spaces (also in --- /+++ lines).
        value, _ = _parse_c_quoted(value, 0)
    return value.removeprefix(prefix)


def _split_git_paths(rest: str) -> tuple[str, str] | None:
    """Split the two paths of a ``diff --git`` header.

    Paths containing spaces are C-style double-quoted by git
    (``"a/my file.txt"``); unquoted paths are split on whitespace.
    Returns None when the header does not have exactly two paths.
    """
    tokens: list[str] = []
    i = 0
    while i < len(rest) and len(tokens) < 2:
        while i < len(rest) and rest[i] in " \t":
            i += 1
        if i >= len(rest):
            break
        if rest[i] == '"':
            token, i = _parse_c_quoted(rest, i)
            tokens.append(token)
        else:
            j = i
            while j < len(rest) and rest[j] not in " \t":
                j += 1
            tokens.append(rest[i:j])
            i = j
    if len(tokens) != 2:
        return None
    return tokens[0], tokens[1]


def _parse_c_quoted(text: str, start: int) -> tuple[str, int]:
    """Parse a C-style double-quoted segment starting at ``start``.

    Handles git's quoting: ``\\"`` and ``\\\\``, ``\\n``/``\\t``, and octal
    byte escapes (``\\303\\251`` for non-ASCII bytes). Returns the unescaped
    value and the index just past the closing quote (or the end of the
    string when unterminated).
    """
    raw = bytearray()
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in "01234567":
                digits = nxt
                j = i + 2
                while j < len(text) and len(digits) < 3 and text[j] in "01234567":
                    digits += text[j]
                    j += 1
                raw.append(int(digits, 8))
                i = j
                continue
            raw.extend(
                {"n": b"\n", "t": b"\t", '"': b'"', "\\": b"\\"}.get(
                    nxt, nxt.encode("utf-8")
                )
            )
            i += 2
            continue
        if char == '"':
            return raw.decode("utf-8", errors="replace"), i + 1
        raw.extend(char.encode("utf-8"))
        i += 1
    return raw.decode("utf-8", errors="replace"), i
