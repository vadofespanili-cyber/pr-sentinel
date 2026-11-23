# pr-sentinel v0.2.3 — Release notes

Patch release: the GitHub Marketplace action is now called
**ai-pr-sentinel**. Nothing else changes — the Python import package
stays `pr_sentinel`, the console command stays `pr-sentinel`, the
GitHub repository stays `vadofespanili-cyber/pr-sentinel`, and the
PyPI distribution name stays `pr-sentinel-ai`. This is the first
release carrying a Marketplace-listable action name.

## What is new

### 1. Marketplace action renamed to ai-pr-sentinel

- GitHub rejected the name `pr-sentinel-ai` when v0.2.2 was submitted
  to the Marketplace: an organization named `pr-sentinel-ai` already
  exists on GitHub, and action names must be unique across actions,
  users and organizations ("Name must be unique. Cannot match an
  existing action, user or organization name."). The 2FA requirement
  was already met (two-factor authentication is enabled on the
  account and the Marketplace Developer Agreement is accepted), so
  the name was the only blocker.
- The `name` in `action.yml` is now `ai-pr-sentinel` (verified free on
  GitHub and on the Marketplace before this release). Check the
  "Publish this Action to the GitHub Marketplace" box on **this**
  release — v0.2.2 carries the blocked name and cannot be listed.
- The PyPI distribution name is unaffected: `pip install
  pr-sentinel-ai` still installs this version once it is published
  there, and the GitHub Action is still used as
  `vadofespanili-cyber/pr-sentinel@v0.2.3`.

## Verification

- **183 pytest tests, all passing** (no code changes in this release —
  action name and version bump only).
- `ruff check` and `ruff format --check` clean.
- `mypy --strict` clean.
- `twine check` PASSED for the wheel and the sdist built from this
  version; both artifacts are still named `pr_sentinel_ai-0.2.3`
  (the distribution name is unchanged) and contain only the package
  plus metadata.
- CLI smoke test: `pr-sentinel 0.2.3`.

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
pip install pr-sentinel-ai
```

Or use the GitHub Action — see the README quick start.
