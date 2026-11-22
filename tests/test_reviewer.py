"""Tests for the AI reviewer, against an in-process fake HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pr_sentinel.diff import parse_diff
from pr_sentinel.reviewer import (
    Reviewer,
    ReviewerError,
    build_review_prompt,
    parse_findings_json,
)

FINDING = {
    "severity": "high",
    "category": "bug",
    "file": "a.py",
    "line": 2,
    "message": "Possible off-by-one error.",
    "suggestion": "Check the loop bound.",
}


class _Handler(BaseHTTPRequestHandler):
    """Fake Responses API: replies with whatever the test configured."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence the server
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append(body)
        payload = self.server.response_body
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.status = 200
    server.response_body = json.dumps({"output_text": json.dumps([FINDING])})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _reviewer(fake_api):
    host, port = fake_api.server_address
    return Reviewer(
        api_key="sk-fake-test", base_url=f"http://{host}:{port}", model="test-model"
    )


def _set_response(fake_api, body, status=200):
    fake_api.response_body = body
    fake_api.status = status


def test_prompt_contains_diff_text_and_json_instructions():
    prompt = build_review_prompt("diff --git a/a.py b/a.py\n+evil()")
    assert "diff --git a/a.py b/a.py" in prompt
    assert "+evil()" in prompt
    assert "JSON array" in prompt
    assert "critical" in prompt
    assert "security" in prompt


def test_review_chunk_output_text_shape(fake_api):
    findings = _reviewer(fake_api).review_chunk("some diff")
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].category == "bug"
    assert findings[0].file == "a.py"
    assert findings[0].line == 2
    assert findings[0].rule == "ai-review"
    # The request carried the model and the prompt with the diff.
    request = json.loads(fake_api.requests[0])
    assert request["model"] == "test-model"
    assert "some diff" in request["input"]
    assert request["input"].startswith("You are a careful code reviewer")


def test_review_chunk_responses_output_shape(fake_api):
    body = json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps([FINDING])}],
                }
            ]
        }
    )
    _set_response(fake_api, body)
    findings = _reviewer(fake_api).review_chunk("some diff")
    assert len(findings) == 1
    assert findings[0].message == "Possible off-by-one error."


def test_review_chunk_strips_code_fences(fake_api):
    fenced = "```json\n" + json.dumps([FINDING]) + "\n```"
    _set_response(fake_api, json.dumps({"output_text": fenced}))
    findings = _reviewer(fake_api).review_chunk("some diff")
    assert len(findings) == 1


def test_review_chunk_non_2xx_raises(fake_api):
    _set_response(fake_api, json.dumps({"error": "unauthorized"}), status=401)
    with pytest.raises(ReviewerError, match="401"):
        _reviewer(fake_api).review_chunk("some diff")


def test_review_chunk_invalid_json_raises(fake_api):
    _set_response(fake_api, json.dumps({"output_text": "not json at all"}))
    with pytest.raises(ReviewerError):
        _reviewer(fake_api).review_chunk("some diff")


def test_review_dedupes_across_chunks(fake_api):
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        "+line one\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        "+line two\n"
    )
    files = parse_diff(diff)
    # Tiny max_chars forces one chunk per file; the fake API returns the
    # same finding for both, so dedupe must collapse them to one.
    findings = _reviewer(fake_api).review(files, max_chars=80)
    assert len(fake_api.requests) == 2
    assert len(findings) == 1
    assert findings[0].message == "Possible off-by-one error."


def test_parse_findings_json_skips_invalid_entries():
    text = json.dumps(
        [
            FINDING,
            {
                "severity": "fatal",
                "category": "bug",
                "file": "a.py",
                "line": 1,
                "message": "bad severity",
            },
            {
                "severity": "high",
                "category": "typo",
                "file": "a.py",
                "line": 1,
                "message": "bad category",
            },
            {
                "severity": "high",
                "category": "bug",
                "file": "a.py",
                "message": "missing line",
            },
            {"severity": "high", "category": "bug", "file": "a.py", "line": 1},
            {
                "severity": "low",
                "category": "style",
                "file": "b.py",
                "line": None,
                "message": "file-level note",
            },
            "not a dict",
        ]
    )
    findings = parse_findings_json(text)
    assert [(f.file, f.line) for f in findings] == [("a.py", 2), ("b.py", None)]


def test_parse_findings_json_unparseable_raises():
    with pytest.raises(ReviewerError):
        parse_findings_json("this is not json")
    with pytest.raises(ReviewerError):
        parse_findings_json('{"not": "an array"}')
    assert parse_findings_json("[]") == []
