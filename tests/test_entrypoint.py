"""Tests for entrypoint.sh, the GitHub Action entrypoint.

Regression coverage for v0.1.1: the action inputs were renamed from
hyphenated names (``github-token``) to underscore names (``github_token``)
because the GitHub runner passes inputs as ``INPUT_<NAME>`` environment
variables and ``/bin/sh`` (dash, the shell in the python:3.12-slim image)
silently drops environment variables whose names are not valid shell
identifiers. ``INPUT_GITHUB-TOKEN`` never reached the entrypoint, so
``GITHUB_TOKEN`` was empty and every Action run failed with
``error: GITHUB_TOKEN is not set``.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"

STUB = """#!/bin/sh
# Fake pr-sentinel: record argv and the token env vars, then behave like
# the real CLI when the token is missing.
echo "$@" > "$STUB_OUT/argv"
printf '%s' "${GITHUB_TOKEN:-}" > "$STUB_OUT/github_token"
printf '%s' "${PR_SENTINEL_API_KEY:-}" > "$STUB_OUT/api_key"
if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "error: GITHUB_TOKEN is not set" >&2
  exit 2
fi
exit 0
"""


@pytest.fixture
def stub_bin(tmp_path, monkeypatch):
    """A directory with a stub pr-sentinel on PATH; returns its out dir."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    stub = bindir / "pr-sentinel"
    stub.write_text(STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB_OUT", str(outdir))
    return outdir


def run_entrypoint(env):
    """Run entrypoint.sh with /bin/sh (dash, like the container)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    clean.pop("GITHUB_TOKEN", None)
    clean.pop("PR_SENTINEL_API_KEY", None)
    clean.pop("GITHUB_EVENT_PATH", None)
    clean.update(env)
    return subprocess.run(
        ["sh", str(ENTRYPOINT)],
        env=clean,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_underscore_inputs_reach_the_cli(stub_bin):
    """INPUT_GITHUB_TOKEN etc. (the renamed v0.1.1 inputs) are passed on."""
    result = run_entrypoint(
        {
            "INPUT_GITHUB_TOKEN": "ghp_test",
            "INPUT_API_KEY": "sk_test",
            "INPUT_REPOSITORY": "o/r",
            "INPUT_PR_NUMBER": "42",
        }
    )
    assert result.returncode == 0, result.stderr
    assert (stub_bin / "argv").read_text().strip() == "review --repo o/r --pr 42"
    assert (stub_bin / "github_token").read_text() == "ghp_test"
    assert (stub_bin / "api_key").read_text() == "sk_test"


def test_github_token_env_fallback(stub_bin):
    """Without the input, a GITHUB_TOKEN already in the env still works."""
    result = run_entrypoint(
        {
            "GITHUB_TOKEN": "ghp_env",
            "INPUT_API_KEY": "sk_test",
            "INPUT_REPOSITORY": "o/r",
            "INPUT_PR_NUMBER": "7",
        }
    )
    assert result.returncode == 0, result.stderr
    assert (stub_bin / "argv").read_text().strip() == "review --repo o/r --pr 7"
    assert (stub_bin / "github_token").read_text() == "ghp_env"


def test_missing_pr_number_exits_2():
    """No PR number anywhere is a usage error, not a silent no-op."""
    result = run_entrypoint({"INPUT_GITHUB_TOKEN": "ghp_test"})
    assert result.returncode == 2
    assert "no pull request number" in result.stderr


def test_hyphenated_input_names_do_not_survive_sh(stub_bin):
    """Documents why hyphenated input names cannot be used: dash drops
    INPUT_GITHUB-TOKEN from the environment, so the token never arrives
    and the run fails exactly like v0.1.0 did in production."""
    result = run_entrypoint(
        {
            "INPUT_GITHUB-TOKEN": "ghp_test",
            "INPUT_API-KEY": "sk_test",
            "INPUT_REPOSITORY": "o/r",
            "INPUT_PR_NUMBER": "42",
        }
    )
    assert result.returncode == 2
    assert "GITHUB_TOKEN is not set" in result.stderr


def test_config_path_is_forwarded(stub_bin, tmp_path):
    """A config-path input reaches the CLI as --config."""
    config = tmp_path / "custom.yaml"
    config.write_text("model: test-model\n")
    result = run_entrypoint(
        {
            "INPUT_GITHUB_TOKEN": "ghp_test",
            "INPUT_REPOSITORY": "o/r",
            "INPUT_PR_NUMBER": "3",
            "INPUT_CONFIG_PATH": str(config),
        }
    )
    assert result.returncode == 0, result.stderr
    argv = (stub_bin / "argv").read_text().strip()
    assert argv == f"review --repo o/r --pr 3 --config {config}"


def test_null_pull_request_in_event_json_exits_2(stub_bin, tmp_path):
    """A null pull_request in the event payload must not crash the
    script with AttributeError (v0.1.2): it is simply no PR number."""
    event = tmp_path / "event.json"
    event.write_text('{"pull_request": null}\n', encoding="utf-8")
    result = run_entrypoint(
        {"INPUT_GITHUB_TOKEN": "ghp_test", "GITHUB_EVENT_PATH": str(event)}
    )
    assert result.returncode == 2
    assert "no pull request number" in result.stderr
    assert "AttributeError" not in result.stderr
    assert "Traceback" not in result.stderr


def test_pr_number_from_event_json(stub_bin, tmp_path):
    """The PR number is read from the event payload when no input is set."""
    event = tmp_path / "event.json"
    event.write_text('{"pull_request": {"number": 55}}\n', encoding="utf-8")
    result = run_entrypoint(
        {
            "INPUT_GITHUB_TOKEN": "ghp_test",
            "INPUT_REPOSITORY": "o/r",
            "GITHUB_EVENT_PATH": str(event),
        }
    )
    assert result.returncode == 0, result.stderr
    assert (stub_bin / "argv").read_text().strip() == "review --repo o/r --pr 55"


def test_repository_falls_back_to_github_repository(stub_bin):
    """Without the repository input, $GITHUB_REPOSITORY is used."""
    result = run_entrypoint(
        {
            "INPUT_GITHUB_TOKEN": "ghp_test",
            "INPUT_PR_NUMBER": "9",
            "GITHUB_REPOSITORY": "o/r",
        }
    )
    assert result.returncode == 0, result.stderr
    assert (stub_bin / "argv").read_text().strip() == "review --repo o/r --pr 9"


def test_empty_token_is_not_exported(stub_bin):
    """An empty token input must not become an exported empty
    GITHUB_TOKEN: the CLI then fails with the clear 'not set' error
    instead of sending an empty Bearer header."""
    result = run_entrypoint(
        {
            "INPUT_GITHUB_TOKEN": "",
            "INPUT_API_KEY": "",
            "INPUT_REPOSITORY": "o/r",
            "INPUT_PR_NUMBER": "4",
        }
    )
    assert result.returncode == 2
    assert "GITHUB_TOKEN is not set" in result.stderr
    assert (stub_bin / "github_token").read_text() == ""


def test_error_message_names_pr_number_input():
    """The missing-PR error names the pr_number input (v0.1.1 wording)."""
    result = run_entrypoint({"INPUT_GITHUB_TOKEN": "ghp_test"})
    assert result.returncode == 2
    assert "pr_number" in result.stderr
    assert "pr-number" not in result.stderr
