# Changelog

All notable changes to pr-sentinel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Smart false-positive reduction in static checks.** An added line
  carrying `pr-sentinel: ignore` inside a comment (`#`, `//` or
  `/* ... */`, case-insensitive) suppresses static findings on that
  exact line — for intentional fixtures such as `eval()` in tests.
  Secret matches that are obvious placeholders (`test`/`dummy`/
  `example`/`fake` values, `changeme`, `your_...`, repeated
  characters, very short low-entropy values) are filtered by a
  stdlib-only Shannon entropy helper, while real high-entropy secrets
  (genuine AWS keys, private key headers, realistic `sk-proj-...` /
  `ghp_...` tokens) are still reported — with regression tests pinning
  both sides.
- **Chat Completions provider support (`api_format`).** New config
  setting `api_format` (`responses` by default, `chat_completions`
  for OpenRouter, Ollama, Groq, vLLM and any OpenAI-compatible
  provider), plus a `--api-format` flag on `review` / `review-diff`
  and an `api_format` Action input. Retry/backoff, timeout, JSON
  parsing and prompt-injection delimiters are identical for both
  formats; an invalid value is a config error.
- **Custom repo instructions.** New `instructions` (inline) and
  `instructions_file` (path) config settings (max 4,000 chars; the
  file wins over the inline string; a missing/unreadable file or an
  over-cap value is a config error). Instructions are injected into
  the reviewer prompt as repository guidelines, clearly separated
  from the untrusted diff delimiters.
- **Risk-prioritized diff ordering.** Before `max_diff_chars` /
  `max_files` truncation, files are sorted by risk tier (CI/CD and
  auth/security/crypto first, core source next, tests next, docs and
  lockfiles last; stable within a tier), so the most security-relevant
  files survive a cut. Hunk headers now keep their enclosing
  function/class context when rendered for the model.
- **Clean inline comment lifecycle.** Inline comments carry a hidden
  `<!-- pr-sentinel:inline -->` marker: re-pushes no longer duplicate
  an identical comment from the bot, and threads the bot opened for
  findings that are now fixed are resolved automatically via GraphQL
  (`resolveReviewThread`). Both are best effort — a failed listing or
  GraphQL call warns on stderr and the review still succeeds.
  `--dry-run` never lists, posts or resolves anything.

### Fixed

- **`Cargo.lock` was not recognized as a lockfile** in risk
  ordering: the lockfile set compared a lowercased basename against
  a mixed-case entry, so `Cargo.lock` landed in the core-source tier
  instead of the lowest tier.
- **`instructions_file` path traversal (external review).**
  `instructions_file` must now be a relative path that stays inside
  the config file's directory (absolute paths and `..` escapes are
  config errors), and undecodable bytes are a config error instead of
  crashing the review.
- **GraphQL bot-ownership check (external review).** In GitHub Actions
  the author login is `github-actions` (no `[bot]` suffix) and
  `GET /user` fails with 403 on `GITHUB_TOKEN`, so auto-resolving our
  own threads never matched. The query now fetches
  `author { __typename login }` and a thread counts as ours when its
  root comment (`nodes[0]`) carries the inline marker and its author
  is a `Bot` (or the token user, or a `[bot]` login).
- **PR-author prompt injection via repo `instructions` (external
  review).** Guidelines no longer go into the system prompt: they are
  placed in the user message as lower-priority style/focus
  preferences, and the system prompt states explicitly that they can
  never override the core security rules, the JSON output schema, or
  instruct the reviewer to suppress or ignore findings. The README
  advises repos with untrusted external PRs to load config from the
  base branch or a trusted `--config` path.
- **Inline deduplication now runs before `max_inline_comments`
  (external review).** Previously posted findings no longer crowd out
  new findings on re-pushes.
- **Static-check suppression and placeholder matching tightened
  (external review).** `pr-sentinel: ignore` inside a quoted string
  literal no longer suppresses a finding, and placeholder matching is
  now exact-component / word-plus-digits — real words like `contest`,
  `protest` and `latest` are reported again, while genuine fixtures
  (`ghp_test`, `test123`, the AWS docs example key) stay suppressed.
- **List-shaped `content` in `chat_completions` (external review).**
  Providers that return `message.content` as a list of content blocks
  (`[{"type": "text", "text": "..."}]`) now have their text blocks
  joined instead of the reply being dropped as "no output text".
- **`instructions_file` is read bounded (bot review).** The file was
  read completely before its 4,000-character cap was checked; it is
  now read at most cap+1 characters in both `validate()` and
  `resolve_instructions()`, so a very large file is rejected without
  ever loading it fully.
- **Ignore marker inside a multiline string (bot review).** String
  stripping is now stateful across the added lines of a file: a
  `pr-sentinel: ignore` inside a triple-quoted string no longer
  suppresses a finding on the line where the string closes. A real
  comment marker after the string closes still suppresses.
- **Null-line review threads now resolve when their file is clean
  (bot review).** A thread whose diff position is outdated (null
  line) was skipped, so a finding fixed by removing or moving the
  line stayed unresolved. It is now resolved when its file has no
  current findings at any line; while the file still has a finding
  it is left alone (the finding may have moved to another line).
- **Real privilege separation on the Responses API path (bot
  review).** The system prompt was concatenated with the user
  content into one `input` string, so the API treated it as user
  input and repo-controlled guidelines did not have the promised
  lower priority. The system prompt is now sent in the Responses
  API's separate `instructions` field, with the guidelines + diff
  in `input` — the same separation the Chat Completions path gets
  from its system/user roles. Note: `instructions` is part of the
  Responses API, but a minimal provider that rejects it can be used
  via `api_format: chat_completions` instead.

### Changed

- README documents `api_format` (with OpenRouter/Ollama examples),
  `instructions` / `instructions_file`, `pr-sentinel: ignore`, and
  the inline-comment lifecycle; the Limitations section now states
  the placeholder-filtering and GraphQL trade-offs honestly.
- The example config (`examples/pr-sentinel.example.yaml`) shows the
  new settings (commented where optional).

## [0.2.4] - 2026-11-23

### Added

- **`mypy --strict` now runs in CI.** The CI job installs `mypy` and
  type-checks `src` on every push and pull request, so a type
  regression can no longer slip through unnoticed (previously CI ran
  only pytest, ruff and the CLI smoke test, while `mypy --strict` was
  only run locally). The check is `python -m mypy --strict src`;
  tests are not covered — they are not strict-clean.

### Changed

- **README naming clarified.** The top of the README now states the
  three names plainly: repository and CLI command `pr-sentinel`,
  PyPI package `pr-sentinel-ai`, GitHub Marketplace action
  `ai-pr-sentinel`.
- **README Limitations wording fixed.** The "English prompts" item no
  longer says "in v0.1.0" (stale — prompts are still in English).
- The example workflow (`examples/github-workflow.yml`) and the README
  usage examples now pin `@v0.2.4`.

## [0.2.3] - 2026-11-23

### Changed

- **Marketplace action name is now `ai-pr-sentinel`.** GitHub rejected
  `pr-sentinel-ai` at Marketplace publish time: an organization named
  `pr-sentinel-ai` already exists, and GitHub requires action names to
  be unique across actions, users and organizations ("Name must be
  unique. Cannot match an existing action, user or organization
  name."). Only the `action.yml` name changes — the PyPI distribution
  name stays `pr-sentinel-ai`, the Python import package stays
  `pr_sentinel`, the console command stays `pr-sentinel`, and the
  repository stays `vadofespanili-cyber/pr-sentinel`. v0.2.3 is the
  first release carrying the listable name; check the Marketplace box
  on this release, not on v0.2.2.
- The example workflow (`examples/github-workflow.yml`) now pins
  `@v0.2.3`.

## [0.2.2] - 2026-11-23

### Changed

- **Renamed the distribution to `pr-sentinel-ai`.** The PyPI name
  `pr-sentinel` is taken by an unrelated tool, and an action named
  "PR Sentinel" already exists on the GitHub Marketplace, so the
  distribution name and the Marketplace action name are now
  `pr-sentinel-ai` — install with `pip install pr-sentinel-ai`.
  Nothing else changes: the Python import package stays
  `pr_sentinel`, the console command stays `pr-sentinel`, and the
  GitHub repository stays `vadofespanili-cyber/pr-sentinel`. This is
  the first release under the new name; it will be the first version
  published to PyPI and to the GitHub Marketplace.
- The example workflow (`examples/github-workflow.yml`) now pins
  `@v0.2.2`.

## [0.2.1] - 2026-11-23

### Added

- **Reviewed commit line in the summary comment.** The summary now
  ends with `Reviewed commit: <short-sha>` (first 7 characters of
  the reviewed commit), so readers can see at a glance which commit
  a review covers — especially after re-pushes, when the comment is
  updated in place. Set it with the `reviewed_commit` Action input
  (the self-review workflow passes
  `${{ github.event.pull_request.head.sha }}`), or `--reviewed-commit`
  on `review` / `review-diff`. It is optional: without it the summary
  is byte-identical to v0.2.0. The full merge-commit SHA is never
  used as a fallback — for `pull_request` events `GITHUB_SHA` is the
  merge commit, not the reviewed head, and would be misleading.
- **Real review screenshots in the README.** The top of the README
  now shows the bot's live inline comment and summary comment from
  experimental PR #5 (in `docs/screenshots/`).

### Changed

- Bumped `actions/checkout` from v4 to v5 and `actions/setup-python`
  from v5 to v6 in the CI and self-review workflows (removes the
  Node.js 20 deprecation warnings).
- The example workflow (`examples/github-workflow.yml`) now pins
  `@v0.2.1`.

## [0.2.0] - 2026-11-22

### Added

- **Optional CI gate (`fail_on_severity`).** Set `fail_on_severity` in
  the config file, pass `--fail-on-severity` to `review` /
  `review-diff` (the flag wins over the config file), or set the
  `fail_on_severity` Action input. The run exits `1` when a reported
  finding meets or exceeds the threshold (`critical` > `high` >
  `medium` > `low`; `none` disables the gate). The review is always
  posted and `--json-out` / `--sarif-out` are always written before
  exit `1`. Default behaviour is unchanged: no gate, exit `0`.
- **Idempotent summary comment.** The summary is posted as an issue
  comment with a hidden `<!-- pr-sentinel:summary -->` marker and
  updated in place on re-push, so the PR is no longer spammed with a
  new summary per push. In `inline` mode, `create_review` is only
  called when there are inline comments to submit, with a brief body
  instead of the full summary table; `summary` mode no longer creates
  a review at all. `--dry-run` creates or updates nothing.
- **SARIF 2.1.0 output (`--sarif-out`).** `findings_to_sarif()` emits
  valid SARIF 2.1.0 (stdlib `json` only) with deduplicated rules, the
  `critical`/`high` → `error`, `medium` → `warning`, `low` → `note`
  mapping, and file + 1-based line locations. Available on both
  `review` and `review-diff` and as the `sarif_out` Action input; see
  the README for the `github/codeql-action/upload-sarif@v3` example.

### Changed

- The example workflow (`examples/github-workflow.yml`) now pins
  `@v0.2.0` and demonstrates the new inputs and the SARIF upload step.

## [0.1.2] - 2026-11-22

Fixes for every verified finding from an independent external review of
v0.1.1 (Google Gemini 4 Neon; full text in
`docs/reviews/gemini-2026-11-22.md`).

### Fixed

- **Review pipeline order.** Ignored files (`ignore_paths`) are now
  dropped right after parsing, before the `max_files` limit and before
  any review work — they no longer consume the file quota or AI tokens.
  The size limit is applied after parsing by dropping whole trailing
  hunks/files, never by cutting the raw diff mid-hunk (a single hunk
  larger than the limit is kept whole, with a warning).
- **Empty tokens rejected.** An empty-string `GITHUB_TOKEN` is now
  rejected with the clear "GITHUB_TOKEN is not set" error in the CLI and
  in `GitHubClient`, instead of being sent as an empty Bearer header and
  failing with a confusing 401. `entrypoint.sh` no longer exports an
  empty token either.
- **`entrypoint.sh` robustness.** A null `pull_request` in the event
  JSON no longer crashes the script; `--repo` falls back to
  `$GITHUB_REPOSITORY` when the input is empty; the stale `pr-number`
  wording in the error message is fixed to `pr_number`.
- **`action.yml`:** `api_key` is now `required: false` (default `""`),
  matching the CLI's static-only mode, which needs no key.
- **`api_base_url` validation.** The value must now parse as a real URL
  with an `http`/`https` scheme and a host (`httpfoo` is rejected);
  plain `http://` is still allowed for local test servers, but the CLI
  warns that the API key is sent in cleartext when the AI reviewer runs.
- **YAML comment parser.** A backslash-escaped quote (`\"`) inside a
  quoted string no longer toggles the quote state and truncates the
  value at a following `#`.
- **Filenames with spaces.** `diff --git` headers with C-style-quoted
  paths (`"a/my file.txt"`) are parsed correctly, including in the
  `---`/`+++` lines; previously the path was wrong, mainly for new and
  deleted files.
- **Inline comments on context lines.** `new_line_position` now maps
  context lines too (they exist in the new file), so an AI finding on a
  context line gets an inline comment instead of being silently dropped
  from the comment list.
- **GitHub client errors.** `json.JSONDecodeError` and `TimeoutError`
  are converted to `GitHubError` instead of crashing unhandled.
- **AI reply parsing.** One model entry with an invalid line number
  (0 or negative) no longer kills the whole chunk's parse; the single
  bad item is skipped.
- **Prompt injection hardening.** The diff is wrapped in explicit
  delimiters and the prompt states that the diff is untrusted data and
  any instructions inside it must be ignored.
- **Static checks.** New OpenAI project keys (`sk-proj-...`) are now
  detected, and `eval()`/`exec()` findings are categorized as
  `security` instead of `bug`.
- **Reporter.** Inline comments now use the modern `line` + `side`
  fields instead of the legacy `position` field. The summary note now
  says which kind of findings these are: the "AI-generated" note
  appears only when the AI reviewer ran; static-only reviews say the
  findings are from deterministic static checks.
- **Dockerfile.** The base image is pinned to `python:3.12.16-slim`
  by SHA256 digest (verified against Docker Hub), so rebuilds are
  reproducible.
- Removed dead code: the unused `cur_paths` variable in `chunk_diff`.
- `mypy --strict` is now clean (31 errors fixed, mostly pre-existing
  bare-generic annotations; annotation-only changes, no behaviour
  change).

### Added

- **Retries with backoff.** Transient failures are retried up to 3
  attempts with exponential backoff (1s, 2s): HTTP 429 and 5xx in both
  the GitHub client and the AI reviewer (honoring `Retry-After` on
  429), plus network errors and timeouts. Delays are capped at 60s.
- 30 new regression tests (113 total), including entrypoint tests run
  under `sh` and retry tests against scripted fake servers.

## [0.1.1] - 2026-11-22

### Fixed

- The GitHub Action failed on every run with `error: GITHUB_TOKEN is not
  set`. The action inputs used hyphenated names (`github-token`,
  `api-key`, `pr-number`, `config-path`); the runner passes inputs as
  `INPUT_<NAME>` environment variables, and `/bin/sh` (dash, the shell in
  the `python:3.12-slim` image) silently drops environment variables whose
  names are not valid shell identifiers, so `INPUT_GITHUB-TOKEN` never
  reached `entrypoint.sh`. Inputs are now underscore names
  (`github_token`, `api_key`, `pr_number`, `config_path`); the README and
  the example workflow are updated to match. Found by dogfooding the
  action on its own repository (PR #1).
- Version strings in tests now derive from `__version__` instead of
  hard-coded literals.

### Added

- Regression tests for `entrypoint.sh` (`tests/test_entrypoint.py`),
  including a test that documents the dash/hyphenated-name behaviour.

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
