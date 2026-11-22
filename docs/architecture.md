# Architecture

## Module map

```
src/pr_sentinel/
  __init__.py      version
  __main__.py      `python -m pr_sentinel` entry point
  cli.py           argparse CLI: review / review-diff / check-config
  config.py        .pr-sentinel.yaml loading + validation (own YAML subset parser)
  diff.py          unified diff parsing, patch positions, chunking
  findings.py      Finding model, severity ordering, filtering
  static_checks.py deterministic checks over added lines (secrets, dangerous calls)
  reviewer.py      Responses API client + review prompt + findings parsing
  github_client.py GitHub REST API over urllib
  reporter.py      Markdown summary, inline comments, JSON output
```

The package uses only the Python standard library. The only external
services are the GitHub API and the model provider's Responses API, both
reached over HTTPS with `urllib`.

## Data flow

```
PR diff (GitHub API or local .patch)
  -> diff.parse_diff()            files / hunks / numbered lines
  -> static_checks.run_static_checks()   findings from added lines only
  -> reviewer.Reviewer.review()          chunked AI review (Responses API)
  -> findings.filter_findings()   severity threshold + ignore globs
  -> reporter.build_summary() / build_inline_comments()
  -> github_client.create_review()  (or stdout / --json-out in local mode)
```

## Key decisions

### Diff positions, not line numbers

GitHub inline comments address a line by its 1-based **position inside the
file's patch**, where hunk headers (`@@ ... @@`) count as lines. `diff.py`
computes that position while parsing, so `reporter.build_inline_comments`
never has to guess. A finding whose line is not an added line in the diff
has no position and is reported in the summary only.

### Chunking never splits a hunk

`diff.chunk_diff` packs whole hunks into chunks of at most `max_diff_chars`
characters (default 30,000 per review call). A single hunk larger than the
limit goes into a chunk of its own, whole — splitting a hunk would lose the
context the model needs to judge the change.

### Two review layers, independent failures

Static checks always run. The AI reviewer runs only when
`PR_SENTINEL_API_KEY` is set; if the model request fails, the CLI prints a
warning and continues with static findings only. A review is never lost
because the model was unreachable — it is only thinner.

### Structured model output, strictly parsed

The review prompt asks for a JSON array of findings and nothing else.
`reviewer.parse_findings_json` strips ` ```json ` fences, rejects entries
with missing keys or invalid severity/category, and raises `ReviewerError`
when the output is not JSON at all. The model cannot inject anything other
than finding fields: no markdown, no commands, no extra keys survive.

### Secrets never echo

Static-check messages describe the rule ("Possible OpenAI API key in added
code."), never the matched value. Findings are safe to post as public
review comments and to write to logs.

### Config without PyYAML

The config file is YAML, but the package has no dependencies, so `config.py`
contains a small parser for the subset the config needs: mappings, nested
mappings, sequences, quoted/plain scalars, numbers, booleans and flow
lists. Unknown keys and invalid values raise `ConfigError` instead of
being silently ignored.

### Exit codes

The CLI exits `0` on success (even when findings exist — findings are not
build failures), `2` on config/usage errors and `3` on API errors.

## Testing

- `tests/` use pytest with in-process fake HTTP servers
  (`http.server.ThreadingHTTPServer` in a thread) for the model and GitHub
  APIs — no network, no real keys; fake values only.
- `test_diff.py` locks patch-position arithmetic; `test_static_checks.py`
  locks every secret/danger rule; `test_cli.py` drives the real CLI against
  the fake servers end to end.
- Run: `python -m pytest -q`, `ruff check src tests`,
  `ruff format --check src tests`.
