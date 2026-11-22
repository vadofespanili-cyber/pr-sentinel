# Contributing to pr-sentinel

Thanks for helping! Short version: keep it small, keep it tested, keep
secrets out.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install pytest ruff
.venv/bin/pip install -e .
```

## Workflow

1. Fork the repo and create a branch from `main`.
2. Make your change. The package uses **only the Python standard library**
   — do not add runtime dependencies.
3. Add or update tests in `tests/`. Tests use in-process fake HTTP servers;
   never use real API keys, tokens or network access in tests.
4. Run the checks:
   ```bash
   .venv/bin/python -m pytest -q
   .venv/bin/ruff check src tests
   .venv/bin/ruff format --check src tests
   ```
5. Update docs (`README.md`, `docs/architecture.md`, `CHANGELOG.md`) when
   behavior changes.
6. Open a pull request using the PR template.

## Rules

- Never commit real secrets. Test fixtures use obviously fake values
  (`sk-fake-...`, `ghp-fake-...`).
- Findings are suggestions, not verdicts — keep messages factual and
  verifiable, and never echo a matched secret value.
- Keep the CLI exit codes stable: `0` success, `2` config/usage error,
  `3` API error.
