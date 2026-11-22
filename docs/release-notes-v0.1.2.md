# pr-sentinel v0.1.2 — Release notes

Patch release: fixes for every verified finding from an independent
external review of v0.1.1 (Google Gemini 4 Neon; the full review text is
in `docs/reviews/gemini-2026-11-22.md`).

## What was wrong

The review found 18 real issues in v0.1.1, from a pipeline-order bug
that let ignored files consume the file quota and AI tokens, to an
empty-token hole, a crash on one bad model line number, and a missing
`sk-proj-` secret pattern. Every finding was checked against the code
before fixing; the review's "Dockerfile 100% in the language bar" claim
could not be verified and was ignored.

## The fixes

- **Review pipeline order.** Ignored files are dropped right after
  parsing, before the `max_files` limit and before any review work.
  The size limit is applied after parsing by dropping whole trailing
  hunks/files, never by cutting the raw diff mid-hunk.
- **Empty tokens rejected** in the CLI, in `GitHubClient`, and in
  `entrypoint.sh` (no more empty Bearer header and confusing 401).
- **`entrypoint.sh`:** null-safe PR extraction from the event JSON,
  `--repo` falls back to `$GITHUB_REPOSITORY`, stale `pr-number`
  wording fixed.
- **`action.yml`:** `api_key` is now optional (`required: false`),
  matching the CLI's static-only mode.
- **`api_base_url`** must now be a real URL with an `http`/`https`
  scheme and a host; plain `http://` still works (local test servers)
  but warns that the API key is sent in cleartext.
- **YAML parser:** escaped quotes (`\"`) no longer break comment
  stripping.
- **Filenames with spaces** in `diff --git` and `---`/`+++` lines are
  parsed correctly (C-style quoting).
- **Inline comments** now cover context lines too, and use the modern
  `line` + `side` fields instead of the legacy `position` field.
- **GitHub client:** `JSONDecodeError` and `TimeoutError` are converted
  to `GitHubError`; transient failures (429/5xx, network errors) are
  retried up to 3 attempts with exponential backoff, honoring
  `Retry-After` — but a POST is retried only on 429 or a connection
  error, never on a 5xx or a timeout, so a lost response cannot make
  `create_review` post the same review twice.
- **AI reviewer:** the diff is wrapped in delimiters and marked as
  untrusted data (prompt-injection hardening); one bad line number no
  longer kills a whole chunk's parse; transient failures are retried
  the same way as the GitHub client.
- **Static checks:** `sk-proj-...` keys are detected; `eval()`/`exec()`
  are now categorized as `security`.
- **Summary note** now says which kind of findings these are: the
  "AI-generated" note appears only when the AI reviewer ran.
- **Dockerfile:** base image pinned to `python:3.12.16-slim` by SHA256
  digest (verified against Docker Hub).
- `mypy --strict` is now clean (31 errors fixed, mostly pre-existing
  bare-generic annotations; annotation-only changes).

## Verification

- **116 pytest tests, all passing** (83 in v0.1.1 + 33 new regression
  tests, including entrypoint tests run under `sh` and retry tests
  against scripted fake servers).
- `ruff check` and `ruff format` clean.
- `mypy --strict` clean on `src/pr_sentinel`.
- CLI smoke test: `pr-sentinel 0.1.2` and `check-config` work.
- Not yet verified live: the action has not run on a real pull request
  with v0.1.2 yet — that happens when v0.1.2 is published and the
  self-review workflow is upgraded to it.

## Not fixed / known limitations

- **No AST-based static checks.** Multi-line patterns (for example
  `shell=True` or `yaml.load(` split across lines) are still not
  detected, and the generic credential pattern can fire on harmless
  test values. A full AST migration was deliberately not done: the
  reviewer only sees the diff, not full file contents, so AST on
  partial content would still miss cases.
- **No CI gate on critical findings.** The exit code is always 0 on a
  completed review, and the summary comment is not updated when new
  commits are pushed. Both are feature work for a later minor version.

## Install

```bash
pip install git+https://github.com/vadofespanili-cyber/pr-sentinel.git@v0.1.2
```

Or use the GitHub Action — see the README quick start.
