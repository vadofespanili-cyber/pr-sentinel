# pr-sentinel v0.2.0 — Release notes

Minor release: three high-impact features, all stdlib-only and backward
compatible with v0.1.2 (default behaviour unchanged: no gate, exit 0,
same summary output). Developed on PR #3, reviewed externally, then
squash-merged.

## What is new

### 1. Optional CI gate (`fail_on_severity`)

- Config key `fail_on_severity`, `--fail-on-severity` flag on `review`
  and `review-diff` (the flag wins over the config file), and the
  `fail_on_severity` Action input. Valid values: `critical`, `high`,
  `medium`, `low`; `none` (or unset) disables the gate.
- Exit codes: `0` success (no gate, or no finding meets it), `1` at
  least one reported finding meets or exceeds the threshold, `2`
  config/usage error, `3` API error.
- The review is always posted and `--json-out` / `--sarif-out` are
  always written before exit `1`. The gate only sees reported
  findings (after `severity_threshold` + `ignore_paths` filtering).
- A warning is printed to stderr when `fail_on_severity` is set lower
  than `severity_threshold`, since those findings are filtered out
  before the gate runs.

### 2. Idempotent summary comment

- The summary is posted as an issue comment with a hidden
  `<!-- pr-sentinel:summary -->` marker and updated in place on
  re-push — no more one summary comment per push.
- Only a comment authored by the bot (or the token user) is ever
  updated, so an unrelated user quoting the marker cannot be
  overwritten; if updating fails with 403/404, a new comment is
  created instead of crashing the review.
- In `inline` mode, `create_review` runs only when there are inline
  comments to submit, with a brief body; the full table lives in the
  summary comment. `summary` mode no longer creates a review.
- A clean re-push updates the comment to "No findings" instead of
  leaving a stale table. `--dry-run` creates or updates nothing.

### 3. SARIF 2.1.0 output (`--sarif-out`)

- `findings_to_sarif()` emits valid SARIF 2.1.0 (stdlib `json` only):
  deduplicated rules, `critical`/`high` → `error`, `medium` →
  `warning`, `low` → `note`, file + 1-based line locations.
- `region.startLine` is only emitted for valid lines (>= 1);
  `artifactLocation.uri` is normalized (`\` → `/`, leading `./`
  stripped); shared rule ids (like `ai-review`) get generic rule
  descriptions instead of the first finding's message.
- Available on both `review` and `review-diff` and as the `sarif_out`
  Action input. `--sarif-out` and `--json-out` now create missing
  parent directories, so nested paths like
  `reports/sentinel.sarif` work. See the README for the
  `github/codeql-action/upload-sarif@v3` example.

## Verification

- **174 pytest tests, all passing** (116 in v0.1.2 + 37 new for the
  three features + 21 new regression tests for the review fixes).
- `ruff check` and `ruff format` clean.
- `mypy --strict` clean on `src/pr_sentinel`.
- CLI smoke tests: `pr-sentinel 0.2.0`, gate exit `1` with outputs
  written first, nested `--sarif-out` / `--json-out` paths.
- PR #3 (squash-merged): all 5 checks green (CI on Python 3.10–3.13
  + the PR review). The v0.1.2 self-review posted 12 findings on the
  PR, all test-fixture false positives (fake tokens/keys and
  deliberate `eval()` calls in the tests) — no real secrets.

## Not fixed / known limitations

- **No AST-based static checks.** Multi-line patterns (for example
  `shell=True` or `yaml.load(` split across lines) are still not
  detected, and the generic credential pattern can fire on harmless
  test values. A full AST migration was deliberately not done: the
  reviewer only sees the diff, not full file contents, so AST on
  partial content would still miss cases.
- **No CI gate on critical findings in `summary` mode beyond
  `fail_on_severity`.** The exit code is `0` on a completed review
  unless the gate is configured.

## Install

```bash
pip install git+https://github.com/vadofespanili-cyber/pr-sentinel.git@v0.2.0
```

Or use the GitHub Action — see the README quick start.
