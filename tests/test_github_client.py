"""Tests for GitHubClient against an in-process fake GitHub server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from pr_sentinel import __version__
from pr_sentinel.github_client import GitHubClient, GitHubError

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


class FakeGitHubHandler(BaseHTTPRequestHandler):
    """Tiny fake GitHub API; every request is recorded on the class."""

    requests: ClassVar[list[dict]] = []
    protocol_version = "HTTP/1.1"

    def _record(self, body: bytes = b"") -> None:
        FakeGitHubHandler.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )

    def _send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/repos/o/r/pulls/1/files":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            if page == 1:
                items = [{"filename": f"f{i}.py"} for i in range(100)]
            elif page == 2:
                items = [{"filename": "f100.py"}, {"filename": "f101.py"}]
            else:
                items = []
            self._record()
            self._send_json(200, items)
            return
        if parsed.path == "/repos/o/r/pulls/1":
            self._record()
            if self.headers.get("Accept") == "application/vnd.github.diff":
                self._send_text(200, SAMPLE_DIFF)
            else:
                self._send_json(200, {"number": 1, "title": "Test PR"})
            return
        self._record()
        self._send_json(404, {"message": "Not Found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self._record(raw)
        if self.path == "/repos/o/r/pulls/1/reviews":
            self._send_json(200, {"id": 99, "state": "COMMENTED"})
        elif self.path == "/repos/o/r/issues/1/labels":
            self._send_json(200, [{"name": "needs-review"}])
        else:
            self._send_json(404, {"message": "Not Found"})

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def github_server():
    FakeGitHubHandler.requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def client(github_server):
    return GitHubClient(token="fake-token", base_url=github_server)


def test_get_pull(client):
    pull = client.get_pull("o", "r", 1)
    assert pull == {"number": 1, "title": "Test PR"}
    request = FakeGitHubHandler.requests[-1]
    assert request["method"] == "GET"
    assert request["path"] == "/repos/o/r/pulls/1"
    # urllib capitalizes header names ("X-github-api-version"), so compare
    # case-insensitively.
    headers = {k.lower(): v for k, v in request["headers"].items()}
    assert headers["authorization"] == "Bearer fake-token"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["user-agent"] == f"pr-sentinel/{__version__}"
    assert headers["accept"] == "application/vnd.github+json"


def test_list_pull_files_pagination(client):
    files = client.list_pull_files("o", "r", 1)
    assert len(files) == 102
    assert files[0]["filename"] == "f0.py"
    assert files[-1]["filename"] == "f101.py"
    pages = [r["path"] for r in FakeGitHubHandler.requests if "/files" in r["path"]]
    assert pages == [
        "/repos/o/r/pulls/1/files?per_page=100&page=1",
        "/repos/o/r/pulls/1/files?per_page=100&page=2",
    ]


def test_get_pull_diff_accept_header(client):
    diff = client.get_pull_diff("o", "r", 1)
    assert diff == SAMPLE_DIFF
    request = FakeGitHubHandler.requests[-1]
    headers = {k.lower(): v for k, v in request["headers"].items()}
    assert headers["accept"] == "application/vnd.github.diff"


def test_create_review_body(client):
    comments = [{"path": "src/a.py", "position": 3, "body": "Looks off."}]
    result = client.create_review(
        "o", "r", 1, body="Summary", comments=comments, event="COMMENT"
    )
    assert result == {"id": 99, "state": "COMMENTED"}
    request = FakeGitHubHandler.requests[-1]
    assert request["method"] == "POST"
    assert request["path"] == "/repos/o/r/pulls/1/reviews"
    assert json.loads(request["body"]) == {
        "body": "Summary",
        "event": "COMMENT",
        "comments": comments,
    }


def test_add_labels_body(client):
    result = client.add_labels("o", "r", 1, ["needs-review"])
    assert result == [{"name": "needs-review"}]
    request = FakeGitHubHandler.requests[-1]
    assert request["method"] == "POST"
    assert request["path"] == "/repos/o/r/issues/1/labels"
    assert json.loads(request["body"]) == ["needs-review"]


def test_404_raises_github_error_with_status(client):
    with pytest.raises(GitHubError) as excinfo:
        client.get_pull("o", "r", 999)
    assert excinfo.value.status == 404
    assert "404" in str(excinfo.value)


def test_missing_token_raises_github_error(github_server):
    client = GitHubClient(token=None, base_url=github_server)
    with pytest.raises(GitHubError):
        client.get_pull("o", "r", 1)
    assert FakeGitHubHandler.requests == []
