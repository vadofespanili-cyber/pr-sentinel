# pr-sentinel v0.2.2 — Release notes

Rename release: the distribution and the GitHub Marketplace action are
now called **pr-sentinel-ai**. Nothing else changes — the Python import
package stays `pr_sentinel`, the console command stays `pr-sentinel`,
and the GitHub repository stays
`vadofespanili-cyber/pr-sentinel`. This is the first release under the
new name, and the first version listed on the GitHub Marketplace.
PyPI publication follows once the trusted publisher is set up
(see `docs/publishing.md`).

## What is new

### 1. Renamed to pr-sentinel-ai

- The PyPI distribution name `pr-sentinel` is taken by an unrelated
  tool, and an action named "PR Sentinel" already exists on the
  GitHub Marketplace, so both names had to change. The distribution
  name in `pyproject.toml` and the `name` in `action.yml` are now
  `pr-sentinel-ai`.
- Install from PyPI once this release is published there:

```bash
pip install pr-sentinel-ai
```

- The `pr-sentinel` console command is unchanged — installing the
  `pr-sentinel-ai` package still provides the `pr-sentinel` command —
  and the GitHub Action is still used as
  `vadofespanili-cyber/pr-sentinel@v0.2.2`.

### 2. Publishing preparation

- The package builds cleanly and passes `twine check`; the test
  suite is excluded from the sdist by `MANIFEST.in` (it contains fake
  credential fixtures that must not ship).
- A `.github/workflows/publish.yml` workflow (Trusted Publishing via
  OIDC, no stored token) will publish to PyPI whenever a GitHub
  release is published — it lands in a small follow-up once the PyPI
  trusted publisher for `pr-sentinel-ai` is registered. See
  `docs/publishing.md` for the full checklist.

## Verification

- **183 pytest tests, all passing** (no code changes in this release —
  rename and version bump only).
- `ruff check` and `ruff format --check` clean.
- `mypy --strict` clean.
- `twine check` PASSED for the wheel and the sdist built from this
  version; both artifacts contain only the package plus metadata.
- CLI smoke test: `pr-sentinel 0.2.2`.

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
