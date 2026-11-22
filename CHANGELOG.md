# Changelog

All notable changes to pr-sentinel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.1] - 2026-11-22

### Fixed

- The GitHub Action failed on every run with `error: GITHUB_TOKEN is not
  set`. The action inputs used hyphenated names (`github-token`,
  `api-key`, `pr-number`, `config-path`); the runner passes inputs as
  `INPUT_<NAME>` environment variables, and `/bin/sh` (dash, the shell in
  the `python:3.12-slim` image) silently drops environment variables whose
  names are not valid shell identifiers, so `INPUT_GITHUB-TOKEN` never
  reached `entrypoint.sh`. Inputs are now underscore names
  (`github_token`, `api_key`, `pr_number`, `config_path`); the README and
  the example workflow are updated to match. Found by dogfooding the
  action on its own repository (PR #1).
- Version strings in tests now derive from `__version__` instead of
  hard-coded literals.

### Added

- Regression tests for `entrypoint.sh` (`tests/test_entrypoint.py`),
  including a test that documents the dash/hyphenated-name behaviour.

## [0.1.0] - 2026-11-22

### Added

- GitHub Action (`action.yml` + `Dockerfile`) that reviews pull requests and
  posts a summary comment plus line-level review comments.
- CLI (`pr-sentinel`): `review --repo OWNER/REPO --pr NUMBER`,
  `review-diff --file diff.patch` (local mode, no GitHub), `check-config`.
- AI reviewer through a Responses-compatible API (stdlib `urllib` only),
  with chunked diff review and strict JSON findings parsing.
- Deterministic static checks over added lines: leaked secrets (OpenAI,
  GitHub, AWS, Slack, private keys, hardcoded credentials), dangerous calls
  (`eval`, `exec`, `os.system`, `subprocess` with `shell=True`, `pickle`,
  `yaml.load` without a Loader), oversized files.
- YAML config (`.pr-sentinel.yaml`): model, severity threshold, ignore
  globs, file/diff limits, inline vs summary mode, inline-comment cap.
- Markdown summary reporter, inline-comment builder and JSON output.
- Tests (78, pytest) and CI (Python 3.10–3.13, ruff check + format).
