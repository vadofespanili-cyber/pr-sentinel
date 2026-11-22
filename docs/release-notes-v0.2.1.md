# pr-sentinel v0.2.1 — Release notes

Patch release: the summary comment now names the commit it reviewed,
and the README shows real screenshots of the bot's live review. Both
changes are backward compatible (default behaviour unchanged: no
commit line unless one is given, same summary output otherwise).

## What is new

### 1. "Reviewed commit" line in the summary comment

- The summary ends with `Reviewed commit: <short-sha>` (first 7
  characters of the reviewed commit), after "Reviewed by
  pr-sentinel" and before the hidden `<!-- pr-sentinel:summary -->`
  marker — so the marker stays findable and re-pushes still update
  the comment in place.
- New `reviewed_commit` Action input (optional, default `""`) and
  `--reviewed-commit` flag on `review` and `review-diff`; the
  self-review workflow passes `${{ github.event.pull_request.head.sha }}`.
  The entrypoint forwards the input only when non-empty (whitespace
  is trimmed, like the other optional inputs).
- No fallback to `GITHUB_SHA`: for `pull_request` events it is the
  merge commit, not the reviewed head, and would be misleading.
  Without a commit the summary is byte-identical to v0.2.0.

### 2. Real review screenshots in the README

- The top of the README now embeds the bot's live inline comment
  (`docs/screenshots/pr5-inline-comment.png`) and a "See it in
  action" section shows the summary comment
  (`docs/screenshots/pr5-summary-comment.png`).
- Both come from experimental PR #5, which intentionally contained
  two logic bugs (an off-by-one and an unbounded in-memory cache).
  The reviewer caught the off-by-one on line 16 with the correct fix
  and missed the cache leak — 1 of 2. The PR was closed without
  merging and its branch deleted.

### 3. Actions bumps (from the unreleased changes)

- `actions/checkout` v4 → v5 and `actions/setup-python` v5 → v6 in
  the CI and self-review workflows (removes the Node.js 20
  deprecation warnings).

## Verification

- **183 pytest tests, all passing** (174 in v0.2.0 + 9 new: 3 reporter,
  3 CLI and 3 entrypoint tests for the Reviewed commit line).
- `ruff check` and `ruff format --check` clean.
- `mypy --strict` clean.
- CLI smoke tests: `pr-sentinel 0.2.1`; a local `review-diff` run
  prints `Reviewed commit: <short-sha>` when `--reviewed-commit` is
  passed and omits the line without it.

## Not fixed / known limitations

- **No AST-based static checks.** Multi-line patterns (for example
  `shell=True` or `yaml.load(` split across lines) are still not
  detected, and the generic credential pattern can fire on harmless
  test values. A full AST migration was deliberately not done: the
  reviewer only sees the diff, not full file contents, so AST on
  partial content would still miss cases.
- **Findings are suggestions, not verdicts.** The AI reviewer can miss
  real bugs (experimental PR #5's unbounded-cache leak is a live
  example) and can flag code that is fine. Always verify before
  acting.

## Install

```bash
pip install git+https://github.com/vadofespanili-cyber/pr-sentinel.git@v0.2.1
```

Or use the GitHub Action — see the README quick start.
