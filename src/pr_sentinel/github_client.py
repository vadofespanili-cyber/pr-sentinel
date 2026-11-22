"""GitHub REST API client (stdlib urllib only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

DEFAULT_BASE_URL = "https://api.github.com"
USER_AGENT = "pr-sentinel/0.1.2"
API_VERSION = "2022-11-28"

# Retries for transient failures: at most this many attempts in total,
# with exponential backoff between them. _sleep is an indirection so tests
# can observe (or stub) the delays instead of really waiting.
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
_MAX_RETRY_DELAY = 60.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_sleep = time.sleep


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Delay before retry ``attempt`` (1-based).

    Honors a Retry-After header when present, otherwise exponential
    backoff (1s, 2s, ...), capped so a single retry never waits too long.
    """
    if retry_after is not None:
        try:
            return min(max(0.0, float(retry_after)), _MAX_RETRY_DELAY)
        except ValueError:
            pass
    return min(_RETRY_BASE_DELAY * (2.0 ** (attempt - 1)), _MAX_RETRY_DELAY)


def _retry_after_from(exc: urllib.error.HTTPError) -> str | None:
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    return value if isinstance(value, str) else None


class GitHubError(Exception):
    """Raised when a GitHub API call fails; carries the HTTP status if known."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    """Minimal GitHub REST client for pull requests and reviews."""

    def __init__(
        self,
        token: str | None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Fetch one pull request."""
        return cast(
            dict[str, Any],
            self._request(
                "GET", f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}"
            ),
        )

    def list_pull_files(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        """List all files changed by a pull request, following pagination."""
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            page_files = cast(
                list[dict[str, Any]],
                self._request(
                    "GET",
                    f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}"
                    f"/files?per_page=100&page={page}",
                ),
            )
            files.extend(page_files)
            if len(page_files) < 100:
                break
            page += 1
        return files

    def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        """Fetch a pull request's unified diff as text."""
        return cast(
            str,
            self._request(
                "GET",
                f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}",
                extra_headers={"Accept": "application/vnd.github.diff"},
                raw=True,
            ),
        )

    def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        comments: list[dict[str, str | int]],
        event: str = "COMMENT",
    ) -> dict[str, Any]:
        """Post a review (with optional inline comments) on a pull request."""
        payload = {"body": body, "event": event, "comments": comments}
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}/reviews",
                body=payload,
            ),
        )

    def add_labels(
        self, owner: str, repo: str, number: int, labels: list[str]
    ) -> list[dict[str, Any]]:
        """Add labels to a pull request (via its backing issue)."""
        return cast(
            list[dict[str, Any]],
            self._request(
                "POST",
                f"/repos/{_quote(owner)}/{_quote(repo)}/issues/{number}/labels",
                body=labels,
            ),
        )

    def _request(
        self,
        method: str,
        path: str,
        body: object = None,
        extra_headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> dict[str, Any] | list[Any] | str:
        if not self.token:
            raise GitHubError("GitHub token is not set (GITHUB_TOKEN)")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if extra_headers:
            headers.update(extra_headers)
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        # Transient failures are retried, but only when a retry cannot
        # repeat a side effect. A 429 is safe for any method (rejected
        # before processing) and a URLError means the connection never
        # reached the server, so those retry for POST too. A 5xx or a
        # timeout may mean the server processed the request and the
        # response was lost, so non-GET methods are not retried on
        # those — a retried create_review could post the review twice.
        # A failed attempt that is not retryable, or the last attempt,
        # raises.
        payload = b""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                self.base_url + path, data=data, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or (
                    method == "GET" and exc.code in _RETRYABLE_STATUSES
                )
                if retryable and attempt < _MAX_ATTEMPTS:
                    _sleep(_retry_delay(attempt, _retry_after_from(exc)))
                    continue
                excerpt = exc.read().decode("utf-8", errors="replace")[:500]
                raise GitHubError(
                    f"GitHub API request failed: {method} {path} "
                    f"-> HTTP {exc.code}: {excerpt}",
                    status=exc.code,
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < _MAX_ATTEMPTS:
                    _sleep(_retry_delay(attempt))
                    continue
                raise GitHubError(
                    f"GitHub API request failed: {method} {path}: {exc.reason}"
                ) from exc
            except TimeoutError as exc:
                if method == "GET" and attempt < _MAX_ATTEMPTS:
                    _sleep(_retry_delay(attempt))
                    continue
                raise GitHubError(
                    f"GitHub API request failed: {method} {path}: {exc}"
                ) from exc
            break
        if raw:
            return payload.decode("utf-8", errors="replace")
        if not payload:
            return {}
        try:
            parsed: dict[str, Any] | list[Any] | str = json.loads(
                payload.decode("utf-8")
            )
        except json.JSONDecodeError as exc:
            raise GitHubError(
                f"GitHub API returned invalid JSON for {method} {path}: {exc}"
            ) from exc
        return parsed


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")
