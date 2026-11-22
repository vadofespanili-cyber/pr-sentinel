# pr-sentinel v0.1.1 — Release notes

Bugfix release: v0.1.0's GitHub Action could not run at all — every run
failed immediately with `error: GITHUB_TOKEN is not set`.

## What broke

The action inputs used hyphenated names (`github-token`, `api-key`,
`pr-number`, `config-path`). The GitHub runner passes action inputs to a
Docker container as `INPUT_<NAME>` environment variables, and `/bin/sh`
(dash, the shell in the `python:3.12-slim` image) silently drops
environment variables whose names are not valid shell identifiers. So
`INPUT_GITHUB-TOKEN` never reached `entrypoint.sh`, the token stayed
empty, and the CLI refused to run. The bug was found by dogfooding: the
first real pull request on pr-sentinel's own repository (PR #1) triggered
the action, and the run failed in 14 seconds.

## The fix

Inputs are now underscore names: `github_token`, `api_key`, `pr_number`,
`config_path`. The README quick start and the example workflow are
updated to match. No CLI behaviour changes; `review`, `review-diff` and
`check-config` are unchanged.

If you copied the v0.1.0 workflow example, rename the `with:` keys:
`api-key` becomes `api_key` (and `github-token`, if you set it,
becomes `github_token`).

## Verification

- 83 pytest tests, all passing, including new `tests/test_entrypoint.py`
  regression tests that run `entrypoint.sh` under `sh` and prove the
  renamed inputs reach the CLI — plus a test documenting that the old
  hyphenated names do not survive dash.
- `ruff check` and `ruff format` clean.
- Live verification on the project's own repository is in progress: the
  self-review workflow was upgraded to v0.1.1 and the action's first live
  review on PR #1 is being checked.

## Install

```bash
pip install git+https://github.com/vadofespanili-cyber/pr-sentinel.git@v0.1.1
```

Or use the GitHub Action — see the README quick start.
