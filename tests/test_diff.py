"""Tests for diff parsing, line mapping and chunking."""

from pr_sentinel.diff import chunk_diff, parse_diff

MULTI_FILE_DIFF = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 import os
+import sys
 print("hello")
 print("world")
@@ -10,2 +11,3 @@
 def f():
-    return 1
+    return 2
+    return 3
diff --git a/src/b.py b/src/b.py
new file mode 100644
--- /dev/null
+++ b/src/b.py
@@ -0,0 +1,2 @@
+line one
+line two
diff --git a/src/old.py b/src/old.py
deleted file mode 100644
--- a/src/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-print(1)
-print(2)
"""


def test_multi_file_diff_parses_all_files():
    files = parse_diff(MULTI_FILE_DIFF)
    assert [f.path for f in files] == ["src/a.py", "src/b.py", "src/old.py"]
    assert files[0].added_lines == 3
    assert files[0].removed_lines == 1
    assert files[1].is_new
    assert files[1].added_lines == 2
    assert files[2].is_deleted
    assert files[2].removed_lines == 2
    assert files[2].added_lines == 0


def test_hunks_and_line_numbers():
    files = parse_diff(MULTI_FILE_DIFF)
    a = files[0]
    assert len(a.hunks) == 2
    first = a.hunks[0]
    assert (first.old_start, first.old_count) == (1, 3)
    assert (first.new_start, first.new_count) == (1, 4)
    added = [line for line in first.lines if line.kind == "add"]
    assert len(added) == 1
    assert added[0].new_no == 2
    assert added[0].text == "import sys"
    kinds = [line.kind for line in first.lines]
    assert kinds == ["context", "add", "context", "context"]


def test_new_line_position_values():
    files = parse_diff(MULTI_FILE_DIFF)
    a = files[0]
    # Patch positions count the "@@" header as a line.
    assert a.new_line_position(2) == 3  # "import sys", header + context + add
    assert a.new_line_position(12) == 9  # "return 2" in second hunk
    assert a.new_line_position(13) == 10  # "return 3" in second hunk
    # Context lines exist in the new file too, so they have a position
    # (since v0.1.2); only removed lines and unknown lines do not.
    assert a.new_line_position(1) == 2  # context line "import os"
    assert a.new_line_position(99) is None
    b = files[1]
    assert b.new_line_position(1) == 2
    assert b.new_line_position(2) == 3


def test_new_line_position_covers_context_lines():
    files = parse_diff(MULTI_FILE_DIFF)
    a = files[0]
    assert a.new_line_position(1) == 2  # context line "import os"
    assert a.new_line_position(3) == 4  # context line 'print("hello")'
    assert a.new_line_position(11) == 7  # context line "def f():"
    assert a.new_line_position(99) is None


SPACED_FILE_DIFF = (
    'diff --git "a/my file.txt" "b/my file.txt"\n'
    "new file mode 100644\n"
    "--- /dev/null\n"
    '+++ "b/my file.txt"\n'
    "@@ -0,0 +1,1 @@\n"
    "+hello\n"
)


def test_filename_with_spaces_new_file():
    """Git C-style-quotes paths with spaces; the quoted --- /+++ lines
    must not corrupt the path either (the bug bit new files hardest,
    because their path comes only from the header)."""
    files = parse_diff(SPACED_FILE_DIFF)
    assert [f.path for f in files] == ["my file.txt"]
    assert files[0].old_path == "my file.txt"
    assert files[0].is_new
    assert files[0].added_lines == 1


def test_filename_with_spaces_modified_file():
    diff = (
        'diff --git "a/my file.txt" "b/my file.txt"\n'
        '--- "a/my file.txt"\n'
        '+++ "b/my file.txt"\n'
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        "+added\n"
    )
    files = parse_diff(diff)
    assert files[0].path == "my file.txt"
    assert files[0].old_path == "my file.txt"
    assert files[0].added_lines == 1


def test_filename_with_escaped_quote():
    diff = (
        'diff --git "a/we\\"ird.txt" "b/we\\"ird.txt"\n'
        "new file mode 100644\n"
        "--- /dev/null\n"
        '+++ "b/we\\"ird.txt"\n'
        "@@ -0,0 +1,1 @@\n"
        "+hello\n"
    )
    files = parse_diff(diff)
    assert files[0].path == 'we"ird.txt'
    assert files[0].old_path == 'we"ird.txt'


def test_deleted_file_keeps_its_path():
    files = parse_diff(MULTI_FILE_DIFF)
    old = files[2]
    assert old.path == "src/old.py"
    assert old.old_path == "src/old.py"
    assert "b//dev/null" not in old.render_patch()
    assert "+++ b/src/old.py" in old.render_patch()


def test_new_file_old_path_stays_sane():
    files = parse_diff(MULTI_FILE_DIFF)
    new = files[1]
    assert new.path == "src/b.py"
    assert "--- a/src/b.py" in new.render_patch()


def test_render_patch_round_trip():
    files = parse_diff(MULTI_FILE_DIFF)
    rendered = "".join(f.render_patch() for f in files)
    reparsed = parse_diff(rendered)
    assert [f.path for f in reparsed] == [f.path for f in files]
    assert [f.added_lines for f in reparsed] == [f.added_lines for f in files]
    assert [f.removed_lines for f in reparsed] == [f.removed_lines for f in files]


def test_empty_diff():
    assert parse_diff("") == []
    assert parse_diff("nothing here\n") == []
    assert chunk_diff([], max_chars=100) == []


def test_chunking_respects_max_chars_and_never_splits_hunk():
    files = parse_diff(MULTI_FILE_DIFF)
    chunks = chunk_diff(files, max_chars=200)
    assert len(chunks) > 1
    hunks = [hunk for f in files for hunk in f.hunks]
    for chunk in chunks:
        assert len(chunk.text) <= 200
    for hunk in hunks:
        rendered = hunk.render()
        containing = [c for c in chunks if rendered in c.text]
        assert len(containing) == 1, "hunk must appear whole in exactly one chunk"


def test_oversized_hunk_goes_whole_into_its_own_chunk():
    body = "".join(f"+line {i}\n" for i in range(50))
    diff = (
        "diff --git a/big.py b/big.py\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -0,0 +1,50 @@\n" + body
    )
    files = parse_diff(diff)
    chunks = chunk_diff(files, max_chars=100)
    assert len(chunks) == 1
    assert chunks[0].text == files[0].render_patch()
    assert chunks[0].files == files


def test_chunking_single_chunk_when_it_fits():
    files = parse_diff(MULTI_FILE_DIFF)
    chunks = chunk_diff(files, max_chars=100000)
    assert len(chunks) == 1
    assert chunks[0].files == files
    for f in files:
        for hunk in f.hunks:
            assert hunk.render() in chunks[0].text


# --- v0.3.0: hunk section + risk-prioritized ordering -----------------------


def test_hunk_section_is_preserved_and_rendered():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -10,5 +10,7 @@ def foo():\n"
        " context\n"
        "+added\n"
    )
    files = parse_diff(diff)
    assert files[0].hunks[0].section == " def foo():"
    assert "@@ -10,5 +10,7 @@ def foo():" in files[0].render_patch()


def test_hunk_without_section_renders_plain_header():
    files = parse_diff(MULTI_FILE_DIFF)
    assert files[0].hunks[0].section == ""
    assert "@@ -1,3 +1,4 @@\n" in files[0].render_patch()


def test_hunk_whitespace_only_section_is_empty():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,1 +1,2 @@   \n"
        " context\n"
        "+added\n"
    )
    files = parse_diff(diff)
    assert files[0].hunks[0].section == ""


def test_risk_tier_table():
    from pr_sentinel.diff import risk_tier

    tier1 = [
        ".github/workflows/ci.yml",
        "Dockerfile",
        "Dockerfile.dev",
        "action.yml",
        "action.yaml",
        "entrypoint.sh",
        "src/auth/login.py",
        "src/security/crypto.py",
        "src/crypto_utils.py",
    ]
    tier2 = ["src/app.py", "src/main.py", "lib/util.py", "src/author.py"]
    tier3 = [
        "tests/test_app.py",
        "test/test_app.py",
        "src/app_test.py",
        "src/app.test.js",
        "src/app.spec.ts",
        "src/test_helper.py",
    ]
    tier4 = [
        "README.md",
        "docs/guide.md",
        "doc/api.rst",
        "package-lock.json",
        "poetry.lock",
        "yarn.lock",
        "Cargo.lock",
        "pnpm-lock.yaml",
        "static/app.min.js",
        "static/app.min.css",
        "static/app.js.map",
    ]
    for path in tier1:
        assert risk_tier(path) == 1, path
    for path in tier2:
        assert risk_tier(path) == 2, path
    for path in tier3:
        assert risk_tier(path) == 3, path
    for path in tier4:
        assert risk_tier(path) == 4, path


def test_sort_by_risk_is_stable_within_tiers():
    from pr_sentinel.diff import sort_by_risk

    def _file(path):
        return parse_diff(
            f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            "@@ -1,1 +1,2 @@\n"
            " context\n"
            "+added\n"
        )[0]

    files = [
        _file("README.md"),
        _file("src/b.py"),
        _file(".github/workflows/ci.yml"),
        _file("tests/test_b.py"),
        _file("src/a.py"),
        _file("docs/guide.md"),
        _file("Dockerfile"),
        _file("tests/test_a.py"),
    ]
    ordered = [f.path for f in sort_by_risk(files)]
    assert ordered == [
        ".github/workflows/ci.yml",
        "Dockerfile",
        "src/b.py",
        "src/a.py",
        "tests/test_b.py",
        "tests/test_a.py",
        "README.md",
        "docs/guide.md",
    ]
