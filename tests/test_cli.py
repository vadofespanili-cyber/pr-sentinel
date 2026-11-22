"""Tests for the CLI: check-config, review-diff and review (dry-run)."""

import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

import pr_sentinel.cli as cli_module
from pr_sentinel import __version__
from pr_sentinel.cli import _prepare_review, main
from pr_sentinel.config import Config
from pr_sentinel.diff import parse_diff
from pr_sentinel.findings import Finding

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
    protocol_version = "HTTP/1.1"

    def _record(self, body=b""):
        FakeGitHubHandler.requests.append(
            {"method": self.command, "path": self.path, "body": body}
        )

    def do_GET(self):
        self._record()
        if self.path == "/repos/o/r/pulls/1":
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
    assert json_out.is_file()


def test_review_posts_summary_only_in_summary_mode(
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
    config = _write_config(tmp_path, model_server, extra="mode: summary\n")

    code = main(["review", "--repo", "o/r", "--pr", "1", "--config", str(config)])
    assert code == 0
    posted = _review_posted()
    assert len(posted) == 1
    body = json.loads(posted[0]["body"])
    assert body["event"] == "COMMENT"
    assert "Model finding." in body["body"]
    assert body["comments"] == []


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
        "+eval(v)\n"
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " y = 1\n"
        "+eval(a)\n"
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
        "+eval(one)\n"
        "@@ -10,1 +11,2 @@\n"
        " b = 1\n"
        "+eval(two)\n"
    )
    second = (
        "diff --git a/two.py b/two.py\n"
        "--- a/two.py\n"
        "+++ b/two.py\n"
        "@@ -1,1 +1,2 @@\n"
        " c = 1\n"
        "+eval(three)\n"
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
        "+eval(user_input)\n",
        encoding="utf-8",
    )
    assert main(["review-diff", "--file", str(patch)]) == 0
    out = capsys.readouterr().out
    assert "deterministic static checks" in out
    assert "AI-generated suggestions" not in out
