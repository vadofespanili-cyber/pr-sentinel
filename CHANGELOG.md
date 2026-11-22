# Changelog

All notable changes to pr-sentinel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
