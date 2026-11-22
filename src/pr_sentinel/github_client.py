"""GitHub REST API client (stdlib urllib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://api.github.com"
USER_AGENT = "pr-sentinel/0.1.1"
API_VERSION = "2022-11-28"


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

    def get_pull(self, owner: str, repo: str, number: int) -> dict:
        """Fetch one pull request."""
        return self._request(
            "GET", f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}"
        )

    def list_pull_files(self, owner: str, repo: str, number: int) -> list[dict]:
        """List all files changed by a pull request, following pagination."""
        files: list[dict] = []
        page = 1
        while True:
            page_files = self._request(
                "GET",
                f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}"
                f"/files?per_page=100&page={page}",
            )
            files.extend(page_files)
            if len(page_files) < 100:
                break
            page += 1
        return files

    def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        """Fetch a pull request's unified diff as text."""
        return self._request(
            "GET",
            f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}",
            extra_headers={"Accept": "application/vnd.github.diff"},
            raw=True,
        )

    def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """Post a review (with optional inline comments) on a pull request."""
        payload = {"body": body, "event": event, "comments": comments}
        return self._request(
            "POST",
            f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{number}/reviews",
            body=payload,
        )

    def add_labels(self, owner: str, repo: str, number: int, labels: list[str]) -> dict:
        """Add labels to a pull request (via its backing issue)."""
        return self._request(
            "POST",
            f"/repos/{_quote(owner)}/{_quote(repo)}/issues/{number}/labels",
            body=labels,
        )

    def _request(
        self,
        method: str,
        path: str,
        body: object = None,
        extra_headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> dict | list | str:
        if self.token is None:
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
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            excerpt = exc.read().decode("utf-8", errors="replace")[:500]
            raise GitHubError(
                f"GitHub API request failed: {method} {path} "
                f"-> HTTP {exc.code}: {excerpt}",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubError(
                f"GitHub API request failed: {method} {path}: {exc.reason}"
            ) from exc
        if raw:
            return payload.decode("utf-8", errors="replace")
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")
