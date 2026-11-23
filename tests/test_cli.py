"""Tests for the CLI: check-config, review-diff and review (dry-run)."""

import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

import pr_sentinel.cli as cli_module
from pr_sentinel import __version__
from pr_sentinel.cli import _post_review, _prepare_review, main
from pr_sentinel.config import Config
from pr_sentinel.diff import parse_diff
from pr_sentinel.findings import Finding
from pr_sentinel.github_client import GitHubError
from pr_sentinel.reporter import SUMMARY_MARKER

SAMPLE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1,2 +1,4 @@\n"
    " import os\n"
    "+import sys\n"
    "+import json\n"
    " x = 1\n"
)

MODEL_FINDING = {
    "severity": "high",
    "category": "bug",
    "file": "src/a.py",
    "line": 3,
    "message": "Model finding.",
    "suggestion": "Fix it.",
    "rule": "model-rule",
}


# --- Fake servers -----------------------------------------------------------


def _send_json(handler, status, payload):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class FakeGitHubHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    # Issue comments returned by GET /issues/1/comments, paginated at 100
    # per page. Tests set this before running the CLI.
    issue_comments: ClassVar[list[dict]] = []
    protocol_version = "HTTP/1.1"

    def _record(self, body=b""):
        FakeGitHubHandler.requests.append(
            {"method": self.command, "path": self.path, "body": body}
        )

    def do_GET(self):
        self._record()
        parsed = urlparse(self.path)
        if parsed.path == "/user":
            _send_json(self, 200, {"login": "github-actions[bot]", "type": "Bot"})
        elif parsed.path == "/repos/o/r/issues/1/comments":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            start = (page - 1) * 100
            _send_json(self, 200, FakeGitHubHandler.issue_comments[start : start + 100])
        elif self.path == "/repos/o/r/pulls/1":
            if self.headers.get("Accept") == "application/vnd.github.diff":
                data = SAMPLE_DIFF.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                _send_json(self, 200, {"number": 1, "title": "Test PR"})
        else:
            _send_json(self, 404, {"message": "Not Found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self._record(raw)
        if self.path == "/repos/o/r/pulls/1/reviews":
            _send_json(self, 200, {"id": 99, "state": "COMMENTED"})
        elif self.path == "/repos/o/r/issues/1/comments":
            _send_json(self, 200, {"id": 7, "body": "created"})
        else:
            _send_json(self, 404, {"message": "Not Found"})

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self._record(raw)
        if self.path.startswith("/repos/o/r/issues/comments/"):
            _send_json(self, 200, {"id": 7, "body": "updated"})
        else:
            _send_json(self, 404, {"message": "Not Found"})

    def log_message(self, *args):
        pass


class FakeModelHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        FakeModelHandler.requests.append({"path": self.path, "body": raw})
        if self.path.endswith("/chat/completions"):
            # Chat Completions reply: the model text is in
            # choices[0].message.content.
            _send_json(
                self,
                200,
                {"choices": [{"message": {"content": json.dumps([MODEL_FINDING])}}]},
            )
            return
        # Responses API reply: the model text is the JSON array of findings.
        _send_json(
            self,
            200,
            {"output_text": json.dumps([MODEL_FINDING])},
        )

    def log_message(self, *args):
        pass


def _start_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def point_github_client_at_fake_server(monkeypatch, github_server):
    """Point the CLI's GitHubClient at the fake server (same interface)."""
    from pr_sentinel.github_client import GitHubClient

    class FakeBaseClient(GitHubClient):
        def __init__(self, token, base_url=None, timeout=30.0):
            super().__init__(token, base_url=github_server, timeout=timeout)

    monkeypatch.setattr(cli_module, "GitHubClient", FakeBaseClient)


@pytest.fixture()
def github_server():
    FakeGitHubHandler.requests.clear()
    FakeGitHubHandler.issue_comments.clear()
    server, thread = _start_server(FakeGitHubHandler)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def model_server():
    FakeModelHandler.requests.clear()
    server, thread = _start_server(FakeModelHandler)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --- Test doubles for modules built by other agents ------------------------
# reviewer.py / static_checks.py may not exist yet. When they are missing,
# install in-memory doubles with the specified interfaces so the CLI wiring
# can still be tested; when they exist, the real modules are used.


def _make_static_checks_stub():
    module = types.ModuleType("pr_sentinel.static_checks")

    def run_static_checks(files):
        return [
            Finding(
                severity="low",
                category="style",
                file="src/a.py",
                line=2,
                message="Stub static finding.",
                suggestion="Tidy it.",
                rule="stub-rule",
            )
        ]

    module.run_static_checks = run_static_checks
    return module


def _make_reviewer_stub():
    import urllib.request

    module = types.ModuleType("pr_sentinel.reviewer")

    class ReviewerError(Exception):
        pass

    class Reviewer:
        def __init__(self, api_key, base_url, model, timeout=60.0):
            self.api_key = api_key
            self.base_url = base_url.rstrip("/")
            self.model = model
            self.timeout = timeout

        def review(self, files, max_chars=30000):
            # Mirrors the real Reviewer protocol (Responses API).
            payload = json.dumps(
                {"model": self.model, "input": "review this diff"}
            ).encode("utf-8")
            request = urllib.request.Request(
                self.base_url + "/responses",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data.get("output_text")
            if not content:
                raise ReviewerError("review response contained no output text")
            return [Finding(**item) for item in json.loads(content)]

    module.Reviewer = Reviewer
    module.ReviewerError = ReviewerError
    return module


@pytest.fixture()
def stub_dependencies(monkeypatch):
    """Install doubles for reviewer/static_checks only if they are absent.

    Returns True when the doubles were installed (i.e. the real modules
    do not exist yet), so tests can assert on the doubles' known findings.
    """
    installed = False
    if importlib.util.find_spec("pr_sentinel.static_checks") is None:
        monkeypatch.setitem(
            sys.modules, "pr_sentinel.static_checks", _make_static_checks_stub()
        )
        installed = True
    if importlib.util.find_spec("pr_sentinel.reviewer") is None:
        monkeypatch.setitem(sys.modules, "pr_sentinel.reviewer", _make_reviewer_stub())
        installed = True
    return installed


# --- Helpers ----------------------------------------------------------------


def _write_config(tmp_path, model_server, extra=""):
    path = tmp_path / ".pr-sentinel.yaml"
    path.write_text(
        "model: fake-model\n"
        "severity_threshold: low\n"
        f"api_base_url: {model_server}/v1\n" + extra,
        encoding="utf-8",
    )
    return path


def _review_posted():
    return [r for r in FakeGitHubHandler.requests if r["path"].endswith("/reviews")]


def _issue_comments_created():
    return [
        r
        for r in FakeGitHubHandler.requests
        if r["method"] == "POST" and r["path"] == "/repos/o/r/issues/1/comments"
    ]


def _issue_comments_updated():
    return [
        r
        for r in FakeGitHubHandler.requests
        if r["method"] == "PATCH" and "/issues/comments/" in r["path"]
    ]


def _issue_comments_listed():
    return [
        r
        for r in FakeGitHubHandler.requests
        if r["method"] == "GET" and "/issues/1/comments" in r["path"]
    ]


# --- check-config ------------------------------------------------------------


def test_check_config_valid(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("model: gpt-5\nseverity_threshold: high\n", encoding="utf-8")
    assert main(["check-config", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "model: gpt-5" in out
    assert "severity_threshold: high" in out


def test_check_config_invalid(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("bogus_key: 1\n", encoding="utf-8")
    assert main(["check-config", "--config", str(config)]) == 2
    assert "error" in capsys.readouterr().err


def test_check_config_missing_file(capsys):
    assert main(["check-config", "--config", "/nonexistent/config.yaml"]) == 2
    assert "error" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert f"pr-sentinel {__version__}" in capsys.readouterr().out


# --- review-diff -------------------------------------------------------------


def test_review_diff_with_fake_model(
    tmp_path, monkeypatch, capsys, model_server, stub_dependencies
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(tmp_path, model_server)
    json_out = tmp_path / "findings.json"

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--config",
            str(config),
            "--json-out",
            str(json_out),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "pr-sentinel review" in out
    assert "Model finding." in out
    assert json_out.is_file()
    findings = json.loads(json_out.read_text(encoding="utf-8"))
    assert any(f["message"] == "Model finding." for f in findings)
    if stub_dependencies:
        assert any(f["message"] == "Stub static finding." for f in findings)
        assert len(findings) == 2


def test_review_diff_without_api_key_runs_static_only(
    tmp_path, monkeypatch, capsys, model_server, stub_dependencies
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(tmp_path, model_server)
    json_out = tmp_path / "findings.json"

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--config",
            str(config),
            "--json-out",
            str(json_out),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "Model finding." not in captured.out
    assert "PR_SENTINEL_API_KEY" in captured.err
    assert json_out.is_file()
    findings = json.loads(json_out.read_text(encoding="utf-8"))
    assert all(f["message"] != "Model finding." for f in findings)
    if stub_dependencies:
        assert findings == [
            {
                "severity": "low",
                "category": "style",
                "file": "src/a.py",
                "line": 2,
                "message": "Stub static finding.",
                "suggestion": "Tidy it.",
                "rule": "stub-rule",
            }
        ]


def test_review_diff_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["review-diff", "--file", str(tmp_path / "nope.patch")]) == 2
    assert "error" in capsys.readouterr().err


# --- review -------------------------------------------------------------------


def test_review_dry_run_posts_nothing(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)
    json_out = tmp_path / "findings.json"

    code = main(
        [
            "review",
            "--repo",
            "o/r",
            "--pr",
            "1",
            "--config",
            str(config),
            "--dry-run",
            "--json-out",
            str(json_out),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "PR #1: Test PR" in out
    assert "Model finding." in out
    assert _review_posted() == []
    # --dry-run must not touch issue comments either: no listing, no
    # create, no update.
    assert _issue_comments_listed() == []
    assert _issue_comments_created() == []
    assert _issue_comments_updated() == []
    assert json_out.is_file()


# --- Reviewed commit line (v0.2.1) ------------------------------------------


def test_review_diff_reviewed_commit_line_in_summary(tmp_path, monkeypatch, capsys):
    """--reviewed-commit adds the "Reviewed commit: <short-sha>" line
    to the printed summary (and the hidden marker stays last)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--reviewed-commit",
            "abc1234def567890",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Reviewed commit: abc1234" in out
    assert "Reviewed by pr-sentinel" in out
    assert out.rstrip("\n").splitlines()[-1] == SUMMARY_MARKER


def test_review_diff_without_reviewed_commit_omits_line(tmp_path, monkeypatch, capsys):
    """Without --reviewed-commit the summary has no commit line at all."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)

    code = main(["review-diff", "--file", str(patch)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Reviewed commit:" not in out
    assert "Reviewed by pr-sentinel" in out


def test_review_reviewed_commit_line_in_summary(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """--reviewed-commit on `review` is threaded into the summary."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)

    code = main(
        [
            "review",
            "--repo",
            "o/r",
            "--pr",
            "1",
            "--config",
            str(config),
            "--dry-run",
            "--reviewed-commit",
            "deadbee1234567",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Reviewed commit: deadbee" in out
    assert out.rstrip("\n").splitlines()[-1] == SUMMARY_MARKER


def test_review_summary_mode_posts_issue_comment_not_review(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """In summary mode the summary goes to an issue comment; no review
    is created at all."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server, extra="mode: summary\n")

    code = main(["review", "--repo", "o/r", "--pr", "1", "--config", str(config)])
    assert code == 0
    assert _review_posted() == []
    created = _issue_comments_created()
    assert len(created) == 1
    body = json.loads(created[0]["body"])
    assert "Model finding." in body["body"]
    assert SUMMARY_MARKER in body["body"]
    assert _issue_comments_updated() == []


def test_review_without_github_token_exits_3(
    tmp_path, monkeypatch, capsys, github_server
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["review", "--repo", "o/r", "--pr", "1"]) == 3
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_review_with_empty_github_token_exits_3(tmp_path, monkeypatch, capsys):
    """An empty-string token is rejected, not sent as an empty Bearer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "")
    assert main(["review", "--repo", "o/r", "--pr", "1"]) == 3
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err


def test_review_diff_non_utf8_patch_exits_2(tmp_path, monkeypatch, capsys):
    """A patch file with non-UTF-8 bytes is a usage error, not a crash."""
    monkeypatch.chdir(tmp_path)
    patch = tmp_path / "bad.patch"
    patch.write_bytes(b"diff --git a/a.py b/a.py\n+caf\xff\n")
    assert main(["review-diff", "--file", str(patch)]) == 2
    assert "error" in capsys.readouterr().err


# --- _prepare_review pipeline order (v0.1.2) ---------------------------------


def test_prepare_review_ignores_files_before_max_files(monkeypatch, capsys):
    """Ignored files must not consume the max_files quota (or AI tokens):
    with max_files=1 and the ignored file first, the real file is still
    reviewed."""
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    diff = (
        "diff --git a/vendor/v.py b/vendor/v.py\n"
        "--- a/vendor/v.py\n"
        "+++ b/vendor/v.py\n"
        "@@ -1,1 +1,2 @@\n"
        " x = 1\n"
        "+eval(v)\n"  # pr-sentinel: ignore
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " y = 1\n"
        "+eval(a)\n"  # pr-sentinel: ignore
    )
    config = Config(ignore_paths=["vendor/**"], max_files=1, severity_threshold="low")
    _summary, findings, files = _prepare_review(config, diff)
    assert [f.path for f in files] == ["src/a.py"]
    assert [f.file for f in findings] == ["src/a.py"]
    err = capsys.readouterr().err
    assert "ignoring 1 file" in err
    assert "reviewing the first" not in err


def test_prepare_review_drops_whole_trailing_files_over_size_limit(monkeypatch, capsys):
    """The size limit is applied after parsing: whole trailing files go,
    and the kept file's hunks stay intact (never cut mid-hunk)."""
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    first = (
        "diff --git a/one.py b/one.py\n"
        "--- a/one.py\n"
        "+++ b/one.py\n"
        "@@ -1,1 +1,2 @@\n"
        " a = 1\n"
        "+eval(one)\n"  # pr-sentinel: ignore
        "@@ -10,1 +11,2 @@\n"
        " b = 1\n"
        "+eval(two)\n"  # pr-sentinel: ignore
    )
    second = (
        "diff --git a/two.py b/two.py\n"
        "--- a/two.py\n"
        "+++ b/two.py\n"
        "@@ -1,1 +1,2 @@\n"
        " c = 1\n"
        "+eval(three)\n"  # pr-sentinel: ignore
    )
    files = parse_diff(first + second)
    budget = len(files[0].render_patch()) + 5  # first file fits, both do not
    config = Config(max_diff_chars=budget, severity_threshold="low")
    _summary, findings, kept = _prepare_review(config, first + second)
    assert [f.path for f in kept] == ["one.py"]
    assert len(kept[0].hunks) == 2
    assert all(f.file == "one.py" for f in findings)
    assert "dropped" in capsys.readouterr().err


def test_prepare_review_keeps_single_oversized_hunk_whole(monkeypatch, capsys):
    """One hunk alone larger than the limit is kept whole (and warned
    about) instead of leaving nothing to review."""
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    body = "".join(f"+line {i}\n" for i in range(20))
    diff = (
        "diff --git a/big.py b/big.py\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -0,0 +1,20 @@\n" + body
    )
    config = Config(max_diff_chars=50, severity_threshold="low")
    _summary, _findings, kept = _prepare_review(config, diff)
    assert len(kept) == 1
    assert len(kept[0].hunks) == 1
    assert len(kept[0].hunks[0].lines) == 20
    assert "kept whole" in capsys.readouterr().err


def test_review_diff_warns_about_plain_http_base_url(
    tmp_path, monkeypatch, capsys, model_server
):
    """With an http:// model URL and an API key, the key is sent in
    cleartext: the run must warn about it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(tmp_path, model_server)
    assert main(["review-diff", "--file", str(patch), "--config", str(config)]) == 0
    assert "cleartext" in capsys.readouterr().err


def test_review_diff_static_only_summary_names_static_checks(
    tmp_path, monkeypatch, capsys
):
    """In static-only mode the summary must not claim AI-generated
    findings."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = tmp_path / "sample.patch"
    patch.write_text(
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " x = 1\n"
        "+eval(user_input)\n",  # pr-sentinel: ignore
        encoding="utf-8",
    )
    assert main(["review-diff", "--file", str(patch)]) == 0
    out = capsys.readouterr().out
    assert "deterministic static checks" in out
    assert "AI-generated suggestions" not in out


# --- fail_on_severity CI gate (v0.2.0) ---------------------------------------

EVAL_PATCH = (
    "diff --git a/a.py b/a.py\n"
    "--- a/a.py\n"
    "+++ b/a.py\n"
    "@@ -1,1 +1,2 @@\n"
    " x = 1\n"
    "+eval(user_input)\n"  # pr-sentinel: ignore
)


def _write_eval_patch(tmp_path):
    patch = tmp_path / "eval.patch"
    patch.write_text(EVAL_PATCH, encoding="utf-8")
    return patch


def test_review_diff_fail_on_severity_high_exits_1(tmp_path, monkeypatch, capsys):
    """A high static finding meets a high gate: exit 1, but the summary
    is printed and the JSON output is still written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    json_out = tmp_path / "findings.json"

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--json-out",
            str(json_out),
            "--fail-on-severity",
            "high",
        ]
    )
    assert code == 1
    out = capsys.readouterr()
    assert "eval()" in out.out
    assert "fail_on_severity=high" in out.err
    findings = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"


def test_review_diff_fail_on_severity_critical_exits_0(tmp_path, monkeypatch, capsys):
    """A high finding does not meet a critical gate: exit 0."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)

    code = main(["review-diff", "--file", str(patch), "--fail-on-severity", "critical"])
    assert code == 0
    assert "eval()" in capsys.readouterr().out


def test_review_diff_fail_on_severity_config_file(tmp_path, monkeypatch, capsys):
    """The gate can come from the config file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("fail_on_severity: high\n", encoding="utf-8")

    assert main(["review-diff", "--file", str(patch), "--config", str(config)]) == 1
    assert "fail_on_severity=high" in capsys.readouterr().err


def test_review_diff_fail_on_severity_cli_overrides_config(
    tmp_path, monkeypatch, capsys
):
    """The CLI flag wins over the config file, in both directions."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("fail_on_severity: critical\n", encoding="utf-8")

    # CLI "high" is stricter than the config's "critical": exit 1.
    assert (
        main(
            [
                "review-diff",
                "--file",
                str(patch),
                "--config",
                str(config),
                "--fail-on-severity",
                "high",
            ]
        )
        == 1
    )
    # CLI "none" disables the config's gate entirely: exit 0.
    assert (
        main(
            [
                "review-diff",
                "--file",
                str(patch),
                "--config",
                str(config),
                "--fail-on-severity",
                "none",
            ]
        )
        == 0
    )
    assert "eval()" in capsys.readouterr().out


def test_review_diff_fail_on_severity_invalid_value_exits_2(tmp_path, monkeypatch):
    """An invalid CLI gate value is a usage error (exit 2)."""
    monkeypatch.chdir(tmp_path)
    patch = _write_eval_patch(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["review-diff", "--file", str(patch), "--fail-on-severity", "urgent"])
    assert excinfo.value.code == 2


def test_review_diff_fail_on_severity_uppercase_cli_value(tmp_path, monkeypatch):
    """The CLI gate value is case-insensitive: --fail-on-severity HIGH
    works like "high"."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)

    code = main(["review-diff", "--file", str(patch), "--fail-on-severity", "HIGH"])
    assert code == 1


def test_review_diff_gate_below_threshold_warns(tmp_path, monkeypatch, capsys):
    """A gate lower than severity_threshold warns on stderr: findings
    below the threshold are filtered out before the gate runs, so the
    gate can never fire on them."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("fail_on_severity: low\n", encoding="utf-8")

    code = main(["review-diff", "--file", str(patch), "--config", str(config)])
    assert code == 1
    err = capsys.readouterr().err
    assert "fail_on_severity=low is lower" in err
    assert "severity_threshold=medium" in err


def test_review_diff_gate_at_or_above_threshold_no_warning(
    tmp_path, monkeypatch, capsys
):
    """No threshold warning when the gate is at or above the reporting
    threshold (or when the thresholds are equal)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "fail_on_severity: high\nseverity_threshold: high\n", encoding="utf-8"
    )

    code = main(["review-diff", "--file", str(patch), "--config", str(config)])
    assert code == 1
    assert "is lower" not in capsys.readouterr().err


def test_gate_message_uses_singular_and_plural(capsys):
    """The gate stderr message says "1 finding meets" but
    "N findings meet"."""
    from pr_sentinel.cli import _finish_outputs

    one = [Finding("high", "bug", "a.py", 1, "One.")]
    assert _finish_outputs(one, None, "high") == 1
    assert "1 finding meets or exceeds the threshold" in capsys.readouterr().err
    two = [
        Finding("high", "bug", "a.py", 1, "One."),
        Finding("critical", "security", "b.py", 2, "Two."),
    ]
    assert _finish_outputs(two, None, "high") == 1
    assert "2 findings meet or exceed the threshold" in capsys.readouterr().err


def test_review_fail_on_severity_posts_before_exit_1(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """The review is posted (and JSON written) before the gate exits 1:
    a failing gate must not swallow the report."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)
    json_out = tmp_path / "findings.json"

    code = main(
        [
            "review",
            "--repo",
            "o/r",
            "--pr",
            "1",
            "--config",
            str(config),
            "--json-out",
            str(json_out),
            "--fail-on-severity",
            "high",
        ]
    )
    assert code == 1
    assert len(_review_posted()) == 1
    assert "Model finding." in capsys.readouterr().out
    assert json_out.is_file()
    findings = json.loads(json_out.read_text(encoding="utf-8"))
    assert any(f["message"] == "Model finding." for f in findings)


def test_review_dry_run_fail_on_severity_exits_1_without_posting(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """In --dry-run the gate still applies, but nothing is posted."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)

    code = main(
        [
            "review",
            "--repo",
            "o/r",
            "--pr",
            "1",
            "--config",
            str(config),
            "--dry-run",
            "--fail-on-severity",
            "high",
        ]
    )
    assert code == 1
    assert _review_posted() == []
    assert "Model finding." in capsys.readouterr().out


# --- Idempotent summary comment (v0.2.0) ------------------------------------


def test_review_inline_mode_creates_summary_comment_and_brief_review(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """Inline mode: the summary goes to a new issue comment, and the
    review carries a brief body plus the inline comments — not the full
    summary table again."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)

    code = main(["review", "--repo", "o/r", "--pr", "1", "--config", str(config)])
    assert code == 0
    created = _issue_comments_created()
    assert len(created) == 1
    comment_body = json.loads(created[0]["body"])["body"]
    assert "Model finding." in comment_body
    assert SUMMARY_MARKER in comment_body
    posted = _review_posted()
    assert len(posted) == 1
    review = json.loads(posted[0]["body"])
    assert review["event"] == "COMMENT"
    # Brief body: no findings table in the review itself.
    assert "Model finding." not in review["body"]
    assert "inline comment" in review["body"]
    assert len(review["comments"]) == 1
    assert review["comments"][0]["path"] == "src/a.py"


def test_review_updates_existing_summary_comment(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """When a comment with the marker already exists, it is updated in
    place instead of creating a second summary comment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    FakeGitHubHandler.issue_comments.append(
        {
            "id": 7,
            "body": f"{SUMMARY_MARKER}\nOld summary.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    )
    config = _write_config(tmp_path, model_server)

    code = main(["review", "--repo", "o/r", "--pr", "1", "--config", str(config)])
    assert code == 0
    assert _issue_comments_created() == []
    updated = _issue_comments_updated()
    assert len(updated) == 1
    assert updated[0]["path"] == "/repos/o/r/issues/comments/7"
    new_body = json.loads(updated[0]["body"])["body"]
    assert "Model finding." in new_body
    assert SUMMARY_MARKER in new_body
    # The inline review is still posted.
    assert len(_review_posted()) == 1


def test_review_summary_comment_pagination(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """The marker comment is found even when it sits on page 2 of the
    issue comments (100 filler comments first)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    FakeGitHubHandler.issue_comments.extend(
        {"id": i, "body": f"comment {i}"} for i in range(100)
    )
    FakeGitHubHandler.issue_comments.append(
        {
            "id": 101,
            "body": f"{SUMMARY_MARKER}\nOld summary.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    )
    config = _write_config(tmp_path, model_server)

    code = main(["review", "--repo", "o/r", "--pr", "1", "--config", str(config)])
    assert code == 0
    listed = _issue_comments_listed()
    assert [r["path"] for r in listed] == [
        "/repos/o/r/issues/1/comments?per_page=100&page=1",
        "/repos/o/r/issues/1/comments?per_page=100&page=2",
    ]
    assert _issue_comments_created() == []
    updated = _issue_comments_updated()
    assert len(updated) == 1
    assert updated[0]["path"] == "/repos/o/r/issues/comments/101"


def test_review_spoofed_marker_comment_is_not_updated(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """End to end: the only marker comment belongs to another user, so
    the bot creates a fresh summary instead of PATCHing theirs."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    FakeGitHubHandler.issue_comments.append(
        {
            "id": 42,
            "body": f"{SUMMARY_MARKER}\nQuoted by a human.",
            "user": {"login": "mallory", "type": "User"},
        }
    )
    config = _write_config(tmp_path, model_server)

    code = main(["review", "--repo", "o/r", "--pr", "1", "--config", str(config)])
    assert code == 0
    assert _issue_comments_updated() == []
    created = _issue_comments_created()
    assert len(created) == 1
    assert "Model finding." in json.loads(created[0]["body"])["body"]


class _StubClient:
    """Records GitHub calls for direct _post_review tests."""

    def __init__(self, authenticated_login: str = "github-actions[bot]"):
        self.authenticated_login = authenticated_login
        self.created: list[str] = []
        self.updated: list[tuple[int, str]] = []
        self.reviews: list[tuple[str, list]] = []
        self.listed_comments: list[dict] = []
        self.review_comments: list[dict] = []
        self.threads: list[dict] = []
        self.resolved: list[str] = []

    def get_authenticated_user(self) -> dict:
        return {"login": self.authenticated_login, "type": "Bot"}

    def list_issue_comments(self, owner, repo, number):
        return self.listed_comments

    def list_pull_review_comments(self, owner, repo, number):
        return self.review_comments

    def list_review_threads(self, owner, repo, number):
        return self.threads

    def resolve_review_thread(self, thread_id):
        self.resolved.append(thread_id)
        return {"id": thread_id, "isResolved": True}

    def create_issue_comment(self, owner, repo, number, body):
        self.created.append(body)
        return {"id": 1, "body": body}

    def update_issue_comment(self, owner, repo, comment_id, body):
        self.updated.append((comment_id, body))
        return {"id": comment_id, "body": body}

    def create_review(self, owner, repo, number, body, comments, event="COMMENT"):
        self.reviews.append((body, comments))
        return {"id": 99, "state": "COMMENTED"}


def test_post_review_skips_review_when_no_inline_comments():
    """Findings that map to no diff line still get a summary comment,
    but no (empty) review is posted."""
    client = _StubClient()
    findings = [
        Finding("high", "bug", "src/a.py", None, "File-level finding."),
    ]
    summary = "Summary text."
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), summary, findings, files)
    assert client.created == ["Summary text."]
    assert client.updated == []
    assert client.reviews == []


def test_post_review_summary_mode_never_posts_review():
    """In summary mode even mappable findings produce no review call."""
    client = _StubClient()
    findings = [
        Finding("high", "bug", "src/a.py", 2, "Line finding."),
    ]
    files = parse_diff(SAMPLE_DIFF)
    _post_review(
        client, "o", "r", 1, Config(mode="summary"), "Summary.", findings, files
    )
    assert client.created == ["Summary."]
    assert client.reviews == []


def test_post_review_zero_findings_still_updates_summary():
    """A clean re-push updates the summary comment to 'No findings'
    instead of leaving a stale findings table behind."""
    client = _StubClient()
    client.listed_comments = [
        {
            "id": 5,
            "body": f"{SUMMARY_MARKER}\nOld table.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]
    _post_review(client, "o", "r", 1, Config(), "No findings.", [], [])
    assert client.created == []
    assert client.updated == [(5, "No findings.")]
    assert client.reviews == []


def test_post_review_ignores_spoofed_marker_comment():
    """A marker quoted by another user is never updated: the bot only
    PATCHes its own summary, and picks the bot-authored one."""
    client = _StubClient()
    client.listed_comments = [
        {
            "id": 9,
            "body": f"{SUMMARY_MARKER}\nQuoted by a human.",
            "user": {"login": "mallory", "type": "User"},
        },
        {
            "id": 10,
            "body": f"{SUMMARY_MARKER}\nBot's own summary.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        },
    ]
    _post_review(client, "o", "r", 1, Config(), "New summary.", [], [])
    assert client.created == []
    assert client.updated == [(10, "New summary.")]


def test_post_review_spoofed_marker_only_creates_new():
    """When the only marker comment belongs to someone else, a fresh
    summary is created instead of PATCHing a stranger's comment."""
    client = _StubClient()
    client.listed_comments = [
        {
            "id": 9,
            "body": f"{SUMMARY_MARKER}\nQuoted by a human.",
            "user": {"login": "mallory", "type": "User"},
        }
    ]
    _post_review(client, "o", "r", 1, Config(), "New summary.", [], [])
    assert client.created == ["New summary."]
    assert client.updated == []


def test_post_review_updates_comment_by_authenticated_login():
    """A marker comment by the token user (personal-access-token run,
    not a bot) is still recognized as ours and updated in place."""
    client = _StubClient(authenticated_login="octocat-human")
    client.listed_comments = [
        {
            "id": 11,
            "body": f"{SUMMARY_MARKER}\nMy own summary.",
            "user": {"login": "octocat-human", "type": "User"},
        }
    ]
    _post_review(client, "o", "r", 1, Config(), "New summary.", [], [])
    assert client.created == []
    assert client.updated == [(11, "New summary.")]


@pytest.mark.parametrize("status", [403, 404])
def test_post_review_update_forbidden_or_gone_falls_back_to_create(monkeypatch, status):
    """If PATCHing the summary fails with 403/404 (comment deleted, or
    the token lost write access), a fresh summary is created instead of
    crashing the review with exit code 3."""
    from pr_sentinel.github_client import GitHubError

    client = _StubClient()
    client.listed_comments = [
        {
            "id": 5,
            "body": f"{SUMMARY_MARKER}\nOld summary.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]

    def _raise(*args, **kwargs):
        raise GitHubError("update failed", status=status)

    monkeypatch.setattr(client, "update_issue_comment", _raise)
    _post_review(client, "o", "r", 1, Config(), "New summary.", [], [])
    assert client.created == ["New summary."]
    assert client.updated == []


def test_post_review_update_other_error_still_raises(monkeypatch):
    """A non-403/404 update failure is still an error, not a silent
    fallback: only the known-race statuses fall back to create."""
    from pr_sentinel.github_client import GitHubError

    client = _StubClient()
    client.listed_comments = [
        {
            "id": 5,
            "body": f"{SUMMARY_MARKER}\nOld summary.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]

    def _raise(*args, **kwargs):
        raise GitHubError("server error", status=500)

    monkeypatch.setattr(client, "update_issue_comment", _raise)
    with pytest.raises(GitHubError):
        _post_review(client, "o", "r", 1, Config(), "New summary.", [], [])
    assert client.created == []


# --- SARIF output (v0.2.0) ----------------------------------------------------


def test_review_diff_sarif_out_writes_valid_sarif(tmp_path, monkeypatch, capsys):
    """--sarif-out writes a valid SARIF 2.1.0 document for the findings."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    sarif_out = tmp_path / "report.sarif"

    code = main(["review-diff", "--file", str(patch), "--sarif-out", str(sarif_out)])
    assert code == 0
    parsed = json.loads(sarif_out.read_text(encoding="utf-8"))
    assert parsed["version"] == "2.1.0"
    results = parsed["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "dangerous-eval"
    assert results[0]["level"] == "error"
    location = results[0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "a.py"
    assert location["region"]["startLine"] == 2


def test_review_diff_sarif_out_written_before_gate_exit_1(
    tmp_path, monkeypatch, capsys
):
    """The SARIF file is written even when the run then exits 1."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    sarif_out = tmp_path / "report.sarif"

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--sarif-out",
            str(sarif_out),
            "--fail-on-severity",
            "high",
        ]
    )
    assert code == 1
    assert sarif_out.is_file()
    parsed = json.loads(sarif_out.read_text(encoding="utf-8"))
    assert len(parsed["runs"][0]["results"]) == 1


def test_review_diff_sarif_out_empty_findings(tmp_path, monkeypatch, capsys):
    """A clean diff still writes a valid SARIF document with no results."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = tmp_path / "clean.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    sarif_out = tmp_path / "report.sarif"

    code = main(["review-diff", "--file", str(patch), "--sarif-out", str(sarif_out)])
    assert code == 0
    parsed = json.loads(sarif_out.read_text(encoding="utf-8"))
    assert parsed["runs"][0]["results"] == []
    assert parsed["runs"][0]["tool"]["driver"]["rules"] == []


def test_review_diff_sarif_out_unwritable_exits_2(tmp_path, monkeypatch, capsys):
    """An unwritable SARIF path is a usage error (exit 2). A missing
    parent directory is auto-created (see below), so the unwritable
    target here is a path under a regular file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--sarif-out",
            str(blocker / "report.sarif"),
        ]
    )
    assert code == 2
    assert "error" in capsys.readouterr().err


def test_review_diff_outputs_create_parent_dirs(tmp_path, monkeypatch, capsys):
    """--sarif-out and --json-out into a non-existent nested directory
    create the parent directories and write the files."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    patch = _write_eval_patch(tmp_path)
    sarif_out = tmp_path / "reports" / "sentinel.sarif"
    json_out = tmp_path / "reports" / "findings.json"

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--sarif-out",
            str(sarif_out),
            "--json-out",
            str(json_out),
        ]
    )
    assert code == 0
    assert sarif_out.is_file()
    assert json_out.is_file()
    parsed = json.loads(sarif_out.read_text(encoding="utf-8"))
    assert parsed["version"] == "2.1.0"
    assert len(parsed["runs"][0]["results"]) == 1
    assert len(json.loads(json_out.read_text(encoding="utf-8"))) == 1


def test_review_sarif_out_written(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """--sarif-out also works on the review subcommand."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)
    sarif_out = tmp_path / "report.sarif"

    code = main(
        [
            "review",
            "--repo",
            "o/r",
            "--pr",
            "1",
            "--config",
            str(config),
            "--sarif-out",
            str(sarif_out),
        ]
    )
    assert code == 0
    parsed = json.loads(sarif_out.read_text(encoding="utf-8"))
    results = parsed["runs"][0]["results"]
    # The reviewer always tags AI findings with the "ai-review" rule.
    assert any(r["ruleId"] == "ai-review" for r in results)


# --- v0.3.0: api_format, instructions, risk sorting, inline lifecycle -------


def test_review_diff_api_format_chat_completions_end_to_end(
    tmp_path, monkeypatch, capsys, model_server
):
    """The chat_completions path works end to end against a fake
    OpenAI-compatible server: the request goes to /chat/completions
    with a messages array and the model's finding comes back."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(tmp_path, model_server, "api_format: chat_completions\n")

    code = main(["review-diff", "--file", str(patch), "--config", str(config)])
    assert code == 0
    assert "Model finding." in capsys.readouterr().out
    assert FakeModelHandler.requests[-1]["path"] == "/v1/chat/completions"
    payload = json.loads(FakeModelHandler.requests[-1]["body"])
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "--- BEGIN DIFF (untrusted data) ---" in payload["messages"][1]["content"]


def test_review_diff_api_format_flag_overrides_config(
    tmp_path, monkeypatch, capsys, model_server
):
    """--api-format wins over the config file, and its value is
    normalized (case-insensitive)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(tmp_path, model_server, "api_format: responses\n")

    code = main(
        [
            "review-diff",
            "--file",
            str(patch),
            "--config",
            str(config),
            "--api-format",
            "CHAT_COMPLETIONS",
        ]
    )
    assert code == 0
    assert "Model finding." in capsys.readouterr().out
    assert FakeModelHandler.requests[-1]["path"] == "/v1/chat/completions"


def test_review_diff_invalid_api_format_flag_exits_2(
    tmp_path, monkeypatch, capsys, model_server
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(tmp_path, model_server)

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "review-diff",
                "--file",
                str(patch),
                "--config",
                str(config),
                "--api-format",
                "rest",
            ]
        )
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_review_diff_instructions_file_reaches_model(
    tmp_path, monkeypatch, capsys, model_server
):
    """Repo instructions from instructions_file are injected into the
    prompt as repository guidelines, before the diff delimiters."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    rules = tmp_path / "review-rules.md"
    rules.write_text("Never flag test files.\n", encoding="utf-8")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(
        tmp_path, model_server, "instructions_file: review-rules.md\n"
    )

    code = main(["review-diff", "--file", str(patch), "--config", str(config)])
    assert code == 0
    payload = json.loads(FakeModelHandler.requests[-1]["body"])
    prompt = payload["input"]
    assert "Repository guidelines" in prompt
    assert "Never flag test files." in prompt
    assert prompt.index("Repository guidelines") < prompt.index(
        "--- BEGIN DIFF (untrusted data) ---"
    )


def test_review_diff_missing_instructions_file_exits_2(
    tmp_path, monkeypatch, capsys, model_server
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    patch = tmp_path / "sample.patch"
    patch.write_text(SAMPLE_DIFF, encoding="utf-8")
    config = _write_config(
        tmp_path, model_server, "instructions_file: /definitely/not/here.md\n"
    )

    code = main(["review-diff", "--file", str(patch), "--config", str(config)])
    assert code == 2
    assert "instructions_file" in capsys.readouterr().err


def _three_file_diff():
    return (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,1 +1,2 @@\n"
        " docs\n"
        "+more docs\n"
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " x = 1\n"
        "+y = 2\n"
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
        "--- a/.github/workflows/ci.yml\n"
        "+++ b/.github/workflows/ci.yml\n"
        "@@ -1,1 +1,2 @@\n"
        " jobs:\n"
        "+  test: run\n"
    )


def test_prepare_review_risk_sorting_keeps_workflow_first_under_max_files(
    monkeypatch, capsys
):
    """With max_files=1 the highest-risk file (the workflow) survives,
    even though it is last in the diff."""
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    config = Config(max_files=1, severity_threshold="low")
    _summary, _findings, files = _prepare_review(config, _three_file_diff())
    assert [f.path for f in files] == [".github/workflows/ci.yml"]
    assert "reviewing the first 1 of 3 files" in capsys.readouterr().err


def test_prepare_review_risk_sorting_drops_docs_first_under_size_limit(
    monkeypatch, capsys
):
    """Under max_diff_chars the lowest-risk files (docs) are dropped
    before source and CI files."""
    monkeypatch.delenv("PR_SENTINEL_API_KEY", raising=False)
    files = parse_diff(_three_file_diff())
    by_path = {f.path: f for f in files}
    budget = (
        len(by_path[".github/workflows/ci.yml"].render_patch())
        + len(by_path["src/a.py"].render_patch())
        + 5
    )
    config = Config(max_diff_chars=budget, severity_threshold="low")
    _summary, _findings, kept = _prepare_review(config, _three_file_diff())
    assert [f.path for f in kept] == [".github/workflows/ci.yml", "src/a.py"]
    assert "diff exceeds max_diff_chars" in capsys.readouterr().err


def _line_finding():
    return [Finding("high", "bug", "src/a.py", 2, "Line finding.")]


def _own_inline_comment(body):
    return {
        "id": 5,
        "path": "src/a.py",
        "line": 2,
        "body": body,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }


def _own_thread(thread_id="T1", path="src/a.py", line=2, resolved=False):
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": path,
        "line": line,
        "comments": {
            "nodes": [
                {
                    "body": "**high · bug**\n\nOld finding.\n\n"
                    "<!-- pr-sentinel:inline -->",
                    "author": {"login": "github-actions[bot]"},
                }
            ]
        },
    }


def test_post_review_dedupes_identical_inline_comment():
    """A re-push with the same finding does not post the same inline
    comment a second time."""
    client = _StubClient()
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert len(client.reviews) == 1
    posted_body = client.reviews[0][1][0]["body"]
    client.review_comments = [_own_inline_comment(posted_body)]
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert len(client.reviews) == 1  # no second review posted


def test_post_review_posts_changed_message_at_same_line():
    """The same (path, line) with a different message is a new comment,
    not a duplicate."""
    client = _StubClient()
    files = parse_diff(SAMPLE_DIFF)
    client.review_comments = [
        _own_inline_comment(
            "**high · bug**\n\nOld message.\n\n<!-- pr-sentinel:inline -->"
        )
    ]
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert len(client.reviews) == 1
    assert "Line finding." in client.reviews[0][1][0]["body"]


def test_post_review_ignores_other_users_identical_comment():
    """An identical comment from another user is not ours: it does not
    suppress our own inline comment."""
    client = _StubClient()
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    posted_body = client.reviews[0][1][0]["body"]
    someone_elses = _own_inline_comment(posted_body)
    someone_elses["user"] = {"login": "someone-else", "type": "User"}
    client.review_comments = [someone_elses]
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert len(client.reviews) == 2


def test_post_review_ignores_unmarked_identical_comment():
    """A comment with the same text but without our hidden marker does
    not count as a duplicate (it could be a human quoting us)."""
    client = _StubClient()
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    posted_body = client.reviews[0][1][0]["body"]
    unmarked = _own_inline_comment(
        posted_body.replace("\n\n<!-- pr-sentinel:inline -->", "")
    )
    client.review_comments = [unmarked]
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert len(client.reviews) == 2


def test_post_review_resolves_outdated_own_thread():
    """A re-push with no findings resolves our own open thread, even
    when there are no new inline comments to post."""
    client = _StubClient()
    client.threads = [_own_thread()]
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", [], files)
    assert client.reviews == []
    assert client.resolved == ["T1"]


def test_post_review_keeps_thread_for_current_finding():
    """A thread whose (path, line) is still flagged is left alone."""
    client = _StubClient()
    client.threads = [_own_thread()]
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert client.resolved == []


def test_post_review_ignores_unmarked_thread():
    """A thread without our marker is not ours: never resolved."""
    client = _StubClient()
    thread = _own_thread()
    thread["comments"]["nodes"][0]["body"] = "A human comment."
    client.threads = [thread]
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", [], files)
    assert client.resolved == []


def _own_thread_github_actions(thread_id="T1", path="src/a.py", line=2):
    """A thread as GitHub GraphQL returns it in Actions: the author
    login is "github-actions" (no "[bot]" suffix) with __typename Bot."""
    thread = _own_thread(thread_id, path, line)
    thread["comments"]["nodes"][0]["author"] = {
        "__typename": "Bot",
        "login": "github-actions",
    }
    return thread


def test_post_review_resolves_github_actions_thread_when_user_lookup_403():
    """In GitHub Actions GET /user fails with 403 on GITHUB_TOKEN, so
    ``me`` is None — the thread is still ours via author __typename."""

    class _ForbiddenUserClient(_StubClient):
        def get_authenticated_user(self):
            raise GitHubError("403 Forbidden", status=403)

    client = _ForbiddenUserClient()
    client.threads = [_own_thread_github_actions()]
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", [], files)
    assert client.resolved == ["T1"]


def test_post_review_only_inspects_thread_root_comment():
    """Only nodes[0] counts: a human root comment with our marker in a
    later reply is not our thread."""
    client = _StubClient()
    thread = _own_thread()
    thread["comments"]["nodes"] = [
        {
            "body": "A human opened this thread.",
            "author": {"login": "someone-else"},
        },
        thread["comments"]["nodes"][0],
    ]
    client.threads = [thread]
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", [], files)
    assert client.resolved == []


def test_post_review_dedupes_before_max_inline_comments():
    """Dedup runs before the max_inline_comments cut: a previously
    posted finding must not crowd out a new finding on re-pushes."""
    client = _StubClient()
    files = parse_diff(SAMPLE_DIFF)
    first = [Finding("high", "bug", "src/a.py", 2, "Line finding.")]
    _post_review(client, "o", "r", 1, Config(), "Summary.", first, files)
    assert len(client.reviews) == 1
    posted_body = client.reviews[0][1][0]["body"]
    client.review_comments = [_own_inline_comment(posted_body)]
    both = [
        Finding("high", "bug", "src/a.py", 2, "Line finding."),
        Finding("high", "bug", "src/a.py", 3, "Other finding."),
    ]
    _post_review(
        client, "o", "r", 1, Config(max_inline_comments=1), "Summary.", both, files
    )
    assert len(client.reviews) == 2
    assert len(client.reviews[1][1]) == 1
    assert client.reviews[1][1][0]["line"] == 3
    assert "Other finding." in client.reviews[1][1][0]["body"]


def test_post_review_resolve_listing_failure_warns_but_succeeds(capsys):
    """If GraphQL listing fails, the review still succeeds with a
    warning instead of crashing."""

    class _FailingThreadsClient(_StubClient):
        def list_review_threads(self, owner, repo, number):
            raise GitHubError("GraphQL is down")

    client = _FailingThreadsClient()
    files = parse_diff(SAMPLE_DIFF)
    _post_review(client, "o", "r", 1, Config(), "Summary.", _line_finding(), files)
    assert len(client.reviews) == 1
    assert "could not list review threads" in capsys.readouterr().err


def test_review_dry_run_touches_no_review_comments_or_threads(
    tmp_path,
    monkeypatch,
    capsys,
    github_server,
    model_server,
    stub_dependencies,
    point_github_client_at_fake_server,
):
    """--dry-run never lists review comments or calls GraphQL: it must
    not mutate or resolve anything."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("PR_SENTINEL_API_KEY", "fake-key")
    config = _write_config(tmp_path, model_server)

    code = main(
        [
            "review",
            "--repo",
            "o/r",
            "--pr",
            "1",
            "--config",
            str(config),
            "--dry-run",
        ]
    )
    assert code == 0
    paths = [r["path"] for r in FakeGitHubHandler.requests]
    assert not any("/pulls/1/comments" in p for p in paths)
    assert not any(p == "/graphql" for p in paths)
    assert _review_posted() == []
