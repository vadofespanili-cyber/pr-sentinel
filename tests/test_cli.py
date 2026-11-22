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
from pr_sentinel.cli import main
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
    assert "pr-sentinel 0.1.0" in capsys.readouterr().out


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
