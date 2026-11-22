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
    assert a.new_line_position(1) is None  # context line, not added
    assert a.new_line_position(99) is None
    b = files[1]
    assert b.new_line_position(1) == 2
    assert b.new_line_position(2) == 3


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
