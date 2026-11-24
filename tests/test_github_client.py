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


SAMPLE_THREADS = [
    {
        "id": "T1",
        "isResolved": False,
        "path": "src/a.py",
        "line": 2,
        "comments": {
            "nodes": [
                {
                    "body": "**high · bug**\n\nUnused import.\n\n"
                    "<!-- pr-sentinel:inline -->",
                    "author": {
                        "__typename": "Bot",
                        "login": "github-actions",
                    },
                }
            ]
        },
    },
    {
        "id": "T2",
        "isResolved": True,
        "path": "src/a.py",
        "line": 3,
        "comments": {
            "nodes": [
                {
                    "body": "Already resolved.",
                    "author": {
                        "__typename": "Bot",
                        "login": "github-actions",
                    },
                }
            ]
        },
    },
]


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
        if parsed.path == "/repos/o/r/pulls/1/comments":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            if page == 1:
                items = [
                    {"id": i, "path": "a.py", "line": i, "body": f"c{i}"}
                    for i in range(100)
                ]
            elif page == 2:
                items = [{"id": 100, "path": "a.py", "line": 100, "body": "c100"}]
            else:
                items = []
            self._record()
            self._send_json(200, items)
            return
        if parsed.path == "/user":
            self._record()
            self._send_json(200, {"login": "github-actions[bot]", "type": "Bot"})
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
        elif self.path == "/graphql":
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            query = payload.get("query", "") if isinstance(payload, dict) else ""
            if "resolveReviewThread" in query:
                self._send_json(
                    200,
                    {
                        "data": {
                            "resolveReviewThread": {
                                "thread": {"id": "T1", "isResolved": True}
                            }
                        }
                    },
                )
            elif "reviewThreads" in query:
                self._send_json(
                    200,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {"nodes": SAMPLE_THREADS}
                                }
                            }
                        }
                    },
                )
            else:
                self._send_json(200, {"data": {}, "errors": [{"message": "bad query"}]})
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


def test_get_authenticated_user_is_cached(client):
    """GET /user is fetched once and cached on the client."""
    first = client.get_authenticated_user()
    assert first == {"login": "github-actions[bot]", "type": "Bot"}
    second = client.get_authenticated_user()
    assert second is first
    user_requests = [r for r in FakeGitHubHandler.requests if r["path"] == "/user"]
    assert len(user_requests) == 1


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


def test_empty_token_raises_github_error(github_server):
    """An empty-string token is rejected, not sent as an empty Bearer."""
    client = GitHubClient(token="", base_url=github_server)
    with pytest.raises(GitHubError):
        client.get_pull("o", "r", 1)
    assert FakeGitHubHandler.requests == []


# --- Scripted server for error/retry behaviour ------------------------------
# Serves a fixed sequence of (status, headers, body) responses, one per
# request; once the script runs out, the last response repeats.


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Fake API that serves a scripted sequence of responses."""

    protocol_version = "HTTP/1.1"

    def _serve(self):
        index = min(self.server.hits, len(self.server.script) - 1)
        status, extra_headers, body = self.server.script[index]
        self.server.hits += 1
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        for key, value in extra_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._serve()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._serve()

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._serve()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def scripted_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server.script = [(200, {}, "{}")]
    server.hits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _scripted_client(scripted_server):
    return GitHubClient(
        token="fake-token",
        base_url=f"http://127.0.0.1:{scripted_server.server_address[1]}",
    )


def test_invalid_json_response_raises_github_error(scripted_server, monkeypatch):
    """A 200 with an unparseable body is a GitHubError, not a crash."""
    import pr_sentinel.github_client as gc

    monkeypatch.setattr(gc, "_sleep", lambda seconds: None)
    scripted_server.script = [(200, {}, "this is not json")]
    with pytest.raises(GitHubError, match="invalid JSON"):
        _scripted_client(scripted_server).get_pull("o", "r", 1)


def test_timeout_raises_github_error(monkeypatch, github_server):
    """TimeoutError is not a URLError on Python 3.10+; it must still be
    converted to GitHubError (after the retries, without waiting)."""
    import urllib.request

    import pr_sentinel.github_client as gc

    calls = []
    monkeypatch.setattr(gc, "_sleep", lambda seconds: None)

    def boom(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(GitHubError):
        GitHubClient(token="fake-token", base_url=github_server).get_pull("o", "r", 1)
    assert len(calls) == 3  # retried up to the attempt limit


def test_retries_after_429_then_succeeds(scripted_server, monkeypatch):
    """A 429 followed by a 200 succeeds, and the Retry-After header is
    honored as the delay."""
    import pr_sentinel.github_client as gc

    delays = []
    monkeypatch.setattr(gc, "_sleep", delays.append)
    scripted_server.script = [
        (429, {"Retry-After": "2"}, '{"message": "rate limited"}'),
        (200, {}, '{"number": 1, "title": "Test PR"}'),
    ]
    result = _scripted_client(scripted_server).get_pull("o", "r", 1)
    assert result == {"number": 1, "title": "Test PR"}
    assert scripted_server.hits == 2
    assert delays == [2.0]


def test_persistent_500_raises_after_three_attempts(scripted_server, monkeypatch):
    """A persistent 5xx is retried with exponential backoff, then raises
    after the third attempt."""
    import pr_sentinel.github_client as gc

    delays = []
    monkeypatch.setattr(gc, "_sleep", delays.append)
    scripted_server.script = [(500, {}, '{"message": "boom"}')]
    with pytest.raises(GitHubError) as excinfo:
        _scripted_client(scripted_server).get_pull("o", "r", 1)
    assert excinfo.value.status == 500
    assert scripted_server.hits == 3
    assert delays == [1.0, 2.0]


def test_post_500_is_not_retried(scripted_server, monkeypatch):
    """A 5xx on a POST must not be retried: the server may have processed
    the request and lost the response, so a retry could repeat the side
    effect (e.g. post the review twice)."""
    import pr_sentinel.github_client as gc

    delays = []
    monkeypatch.setattr(gc, "_sleep", delays.append)
    scripted_server.script = [(500, {}, '{"message": "boom"}')]
    with pytest.raises(GitHubError) as excinfo:
        _scripted_client(scripted_server).add_labels("o", "r", 1, ["x"])
    assert excinfo.value.status == 500
    assert scripted_server.hits == 1
    assert delays == []


def test_post_429_is_retried_then_succeeds(scripted_server, monkeypatch):
    """A 429 is safe to retry for POST too: the request was rejected
    before processing."""
    import pr_sentinel.github_client as gc

    delays = []
    monkeypatch.setattr(gc, "_sleep", delays.append)
    scripted_server.script = [
        (429, {"Retry-After": "1"}, '{"message": "rate limited"}'),
        (200, {}, '[{"name": "x"}]'),
    ]
    result = _scripted_client(scripted_server).add_labels("o", "r", 1, ["x"])
    assert result == [{"name": "x"}]
    assert scripted_server.hits == 2
    assert delays == [1.0]


def test_post_timeout_is_not_retried(monkeypatch, github_server):
    """A TimeoutError on a POST must not be retried: the request may
    already have been processed."""
    import urllib.request

    import pr_sentinel.github_client as gc

    calls = []
    monkeypatch.setattr(gc, "_sleep", lambda seconds: None)

    def boom(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(GitHubError):
        GitHubClient(token="fake-token", base_url=github_server).add_labels(
            "o", "r", 1, ["x"]
        )
    assert len(calls) == 1


# --- Issue comments (v0.2.0) ------------------------------------------------
# The fake server in this file does not implement issue comments yet;
# these tests use a small dedicated handler instead.


class _IssueCommentsHandler(BaseHTTPRequestHandler):
    """Fake API serving issue comments, paginated at 100 per page."""

    requests: ClassVar[list[dict]] = []
    comments: ClassVar[list[dict]] = []
    protocol_version = "HTTP/1.1"

    def _record(self, body: bytes = b"") -> None:
        _IssueCommentsHandler.requests.append(
            {"method": self.command, "path": self.path, "body": body}
        )

    def _send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._record()
        parsed = urlparse(self.path)
        if parsed.path == "/repos/o/r/issues/1/comments":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            start = (page - 1) * 100
            self._send_json(200, _IssueCommentsHandler.comments[start : start + 100])
            return
        self._send_json(404, {"message": "Not Found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self._record(raw)
        if self.path == "/repos/o/r/issues/1/comments":
            self._send_json(200, {"id": 7, "body": json.loads(raw)["body"]})
            return
        self._send_json(404, {"message": "Not Found"})

    def do_PATCH(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self._record(raw)
        if self.path.startswith("/repos/o/r/issues/comments/"):
            comment_id = int(self.path.rsplit("/", 1)[-1])
            self._send_json(200, {"id": comment_id, "body": json.loads(raw)["body"]})
            return
        self._send_json(404, {"message": "Not Found"})

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def issue_comments_server():
    _IssueCommentsHandler.requests.clear()
    _IssueCommentsHandler.comments.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _IssueCommentsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def issue_comments_client(issue_comments_server):
    return GitHubClient(token="fake-token", base_url=issue_comments_server)


def test_list_issue_comments_pagination(issue_comments_client):
    _IssueCommentsHandler.comments.extend(
        {"id": i, "body": f"c{i}"} for i in range(150)
    )
    comments = issue_comments_client.list_issue_comments("o", "r", 1)
    assert len(comments) == 150
    assert comments[0]["id"] == 0
    assert comments[-1]["id"] == 149
    pages = [r["path"] for r in _IssueCommentsHandler.requests if r["method"] == "GET"]
    assert pages == [
        "/repos/o/r/issues/1/comments?per_page=100&page=1",
        "/repos/o/r/issues/1/comments?per_page=100&page=2",
    ]


def test_list_issue_comments_empty(issue_comments_client):
    assert issue_comments_client.list_issue_comments("o", "r", 1) == []
    assert len(_IssueCommentsHandler.requests) == 1


def test_create_issue_comment_body(issue_comments_client):
    result = issue_comments_client.create_issue_comment("o", "r", 1, "Hello.")
    assert result == {"id": 7, "body": "Hello."}
    request = _IssueCommentsHandler.requests[-1]
    assert request["method"] == "POST"
    assert request["path"] == "/repos/o/r/issues/1/comments"
    assert json.loads(request["body"]) == {"body": "Hello."}


def test_update_issue_comment_body(issue_comments_client):
    result = issue_comments_client.update_issue_comment("o", "r", 42, "New text.")
    assert result == {"id": 42, "body": "New text."}
    request = _IssueCommentsHandler.requests[-1]
    assert request["method"] == "PATCH"
    assert request["path"] == "/repos/o/r/issues/comments/42"
    assert json.loads(request["body"]) == {"body": "New text."}


def test_patch_500_is_not_retried(scripted_server, monkeypatch):
    """A 5xx on a PATCH must not be retried either: like POST, the
    server may have applied the update and lost the response."""
    import pr_sentinel.github_client as gc

    delays = []
    monkeypatch.setattr(gc, "_sleep", delays.append)
    scripted_server.script = [(500, {}, '{"message": "boom"}')]
    with pytest.raises(GitHubError) as excinfo:
        _scripted_client(scripted_server).update_issue_comment("o", "r", 1, "x")
    assert excinfo.value.status == 500
    assert scripted_server.hits == 1
    assert delays == []


# --- v0.3.0: inline comments + GraphQL threads -------------------------------


def test_list_pull_review_comments_pagination(client):
    comments = client.list_pull_review_comments("o", "r", 1)
    assert len(comments) == 101
    assert comments[0]["id"] == 0
    assert comments[-1]["id"] == 100
    paths = [r["path"] for r in FakeGitHubHandler.requests]
    assert "/repos/o/r/pulls/1/comments?per_page=100&page=1" in paths
    assert "/repos/o/r/pulls/1/comments?per_page=100&page=2" in paths


def test_list_review_threads_returns_nodes(client):
    threads = client.list_review_threads("o", "r", 1)
    assert [t["id"] for t in threads] == ["T1", "T2"]
    assert threads[0]["path"] == "src/a.py"
    assert threads[0]["line"] == 2
    assert threads[0]["isResolved"] is False
    request = FakeGitHubHandler.requests[-1]
    assert request["method"] == "POST"
    assert request["path"] == "/graphql"
    payload = json.loads(request["body"])
    assert "reviewThreads" in payload["query"]
    assert "__typename" in payload["query"]
    assert payload["variables"] == {"owner": "o", "repo": "r", "number": 1}


def test_resolve_review_thread_posts_mutation(client):
    resolved = client.resolve_review_thread("T1")
    assert resolved == {"id": "T1", "isResolved": True}
    request = FakeGitHubHandler.requests[-1]
    payload = json.loads(request["body"])
    assert "resolveReviewThread" in payload["query"]
    assert payload["variables"] == {"threadId": "T1"}


def test_graphql_errors_raise_github_error(client):
    with pytest.raises(GitHubError, match="bad query"):
        client.graphql("query { nonsense }")
