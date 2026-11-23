# pr-sentinel v0.2.4 — Release notes

Patch release: `mypy --strict` now runs in CI, plus README cleanups
(naming clarification and a stale version reference). No behavior
changes — the reviewer, its findings and its output are the same as
v0.2.3.

## What is new

### 1. mypy --strict in CI

- The CI job now installs `mypy` and runs `python -m mypy --strict
  src` on every push and pull request (all four Python versions,
  3.10–3.13). Previously CI ran only pytest, ruff and the CLI smoke
  test, while `mypy --strict` was only run locally — so a type
  regression could have slipped through unnoticed. This closes the
  gap flagged by the independent external review (Google Gemini 4
  Neon) of v0.2.3.
- The check covers `src` only: the tests are not strict-clean (433
  errors, mostly missing annotations in test helpers), so scoping the
  check to `src` is deliberate, not an oversight.

### 2. README naming clarified

- The top of the README now states the three names plainly:
  repository and CLI command `pr-sentinel`, PyPI package
  `pr-sentinel-ai`, GitHub Marketplace action `ai-pr-sentinel` — so a
  newcomer is not confused by the three different names.

### 3. README Limitations wording fixed

- The "English prompts" item no longer says "in v0.1.0" (stale —
  prompts are still in English in v0.2.4).

### 4. Version pins

- The example workflow (`examples/github-workflow.yml`) and the README
  usage examples now pin `@v0.2.4`.

## Verification

- **183 pytest tests, all passing** (no code changes in this release —
  CI config, version bump and docs only).
- `ruff check` and `ruff format --check` clean.
- `mypy --strict src` clean (10 source files, no issues).
- `twine check` PASSED for the wheel and the sdist built from this
  version (`pr_sentinel_ai-0.2.4-py3-none-any.whl`, 35,682 bytes;
  `pr_sentinel_ai-0.2.4.tar.gz`, 36,579 bytes); both artifacts are
  still named `pr_sentinel_ai-0.2.4` (the distribution name is
  unchanged) and contain only the package plus metadata.
- CLI smoke test: `pr-sentinel 0.2.4`.

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
- **Old inline comments are not auto-resolved.** When a bug is fixed,
  the bot's summary comment updates to "No findings", but an inline
  comment on the earlier commit stays open as history (seen on PR
  #9). Slated for v0.3.0.
- **Test-fixture false positives.** Fake tokens and keys in `tests/`
  still trigger the credential patterns — there is no allowlist or
  entropy check yet. Slated for v0.3.0.

## Install

```bash
pip install pr-sentinel-ai
```

Or use the GitHub Action — see the README quick start.
