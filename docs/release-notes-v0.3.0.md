# pr-sentinel v0.3.0 — Release notes

Minor release: fewer false positives, Chat Completions providers, repo
instructions, and a clean inline-comment lifecycle. The reviewer, its
findings and its output format are the same shape as v0.2.4 — what
changed is what it reports, which providers it can talk to, and how it
treats its own comments on re-pushes.

## What is new

### 1. Smart false-positive reduction in static checks

- An added line carrying `pr-sentinel: ignore` inside a comment (`#`,
  `//` or `/* ... */`, case-insensitive) suppresses static findings on
  that exact line — for intentional fixtures such as `eval()` in tests.
- Secret matches that are obvious placeholders (`test`/`dummy`/
  `example`/`fake` values, `changeme`, `your_...`, repeated
  characters, very short low-entropy values) are filtered by a
  stdlib-only Shannon entropy helper, while real high-entropy secrets
  (genuine AWS keys, private key headers, realistic `sk-proj-...` /
  `ghp_...` tokens) are still reported — with regression tests pinning
  both sides.
- The suppression is string-aware: an ignore marker inside a quoted
  string literal (single, double, triple-quoted, or a JavaScript
  backtick template literal) no longer suppresses a finding, while a
  real comment marker after the string closes still does. `${...}`
  interpolations inside template literals stay visible as code.

### 2. Chat Completions provider support (`api_format`)

- New config setting `api_format` (`responses` by default,
  `chat_completions` for OpenRouter, Ollama, Groq, vLLM and any
  OpenAI-compatible provider), plus a `--api-format` flag on `review` /
  `review-diff` and an `api_format` Action input. Retry/backoff,
  timeout, JSON parsing and prompt-injection delimiters are identical
  for both formats; an invalid value is a config error.

### 3. Custom repo instructions

- New `instructions` (inline) and `instructions_file` (path) config
  settings (max 4,000 chars; the file wins over the inline string; a
  missing/unreadable file or an over-cap value is a config error).
  Instructions are injected into the reviewer prompt as repository
  guidelines, clearly separated from the untrusted diff delimiters.
  The file path must stay inside the config file's directory
  (absolute paths and `..` escapes are config errors), and the file
  is read bounded (at most cap+1 characters), so a very large file is
  rejected without ever loading it fully.

### 4. Risk-prioritized diff ordering

- Before `max_diff_chars` / `max_files` truncation, files are sorted
  by risk tier (CI/CD and auth/security/crypto first, core source
  next, tests next, docs and lockfiles last; stable within a tier),
  so the most security-relevant files survive a cut. Hunk headers now
  keep their enclosing function/class context when rendered for the
  model.

### 5. Clean inline comment lifecycle

- Inline comments carry a hidden `<!-- pr-sentinel:inline -->`
  marker: re-pushes no longer duplicate an identical comment from the
  bot, and threads the bot opened for findings that are now fixed are
  resolved automatically via GraphQL (`resolveReviewThread`). Both are
  best effort — a failed listing or GraphQL call warns on stderr and
  the review still succeeds. `--dry-run` never lists, posts or
  resolves anything.
- Deduplication runs before the `max_inline_comments` cut, so
  previously posted findings no longer crowd out new findings on
  re-pushes.
- The resolver only closes threads the review actually saw: it uses
  the pre-filter findings and the review coverage, so a finding hidden
  by the severity threshold — or in a file/hunk dropped by
  `max_diff_chars` / `max_files` / `ignore_paths` — no longer looks
  "fixed". A thread whose diff position is outdated (null line) is
  resolved only when its file was fully reviewed and has no current
  findings left.

## Fixes from the external review and the bot's own reviews

- **Prompt injection via repo `instructions` (external review,
  Google Gemini 4 Neon).** Guidelines no longer go into the system
  prompt: they are placed in the user message as lower-priority
  style/focus preferences, and the system prompt states explicitly
  that they can never override the core security rules, the JSON
  output schema, or instruct the reviewer to suppress or ignore
  findings. On the Responses path the system prompt is sent in the
  API's separate `instructions` field, with the guidelines + diff in
  `input` — the same separation the Chat Completions path gets from
  its system/user roles. The README advises repos with untrusted
  external PRs to load config from the base branch or a trusted
  `--config` path.
- **GraphQL bot-ownership check (external review).** In GitHub Actions
  the author login is `github-actions` (no `[bot]` suffix) and
  `GET /user` fails with 403 on `GITHUB_TOKEN`, so auto-resolving our
  own threads never matched. The query now fetches
  `author { __typename login }` and a thread counts as ours when its
  root comment carries the inline marker and its author is a `Bot`
  (or the token user, or a `[bot]` login).
- **List-shaped `content` in `chat_completions` (external review).**
  Providers that return `message.content` as a list of content blocks
  now have their text blocks joined instead of the reply being
  dropped as "no output text".
- **Two lockfile/entry bugs found while building v0.3.0.**
  `Cargo.lock` was not recognized as a lockfile in risk ordering
  (case mismatch), and the gate-value normalizer mangled its input.
  Both fixed with regression tests.

## Verification

- **282 pytest tests, all passing** (183 in v0.2.4; the rest are new
  v0.3.0 coverage: suppression, entropy/placeholder filtering,
  `api_format` on both paths, instructions, risk ordering, inline
  lifecycle, and the resolver coverage rules).
- `ruff check` and `ruff format --check` clean.
- `mypy --strict src` clean (10 source files, no issues).
- `twine check` PASSED for the wheel and the sdist built from this
  version (`pr_sentinel_ai-0.3.0-py3-none-any.whl`, 50,763 bytes;
  `pr_sentinel_ai-0.3.0.tar.gz`, 53,723 bytes); both artifacts are
  still named `pr_sentinel_ai-0.3.0` (the distribution name is
  unchanged) and contain only the package plus metadata.
- CLI smoke test: `pr-sentinel 0.3.0`.
- The v0.3.0 static checks report **0 findings on the full PR #12
  diff**, while a synthetic diff with a realistic `ghp_` token and a
  bare `eval()` is still caught.

## Not fixed / known limitations

- **A triple-quoted string opened on a context line is not tracked.**
  The ignore-marker scanner only sees added lines, so a marker inside
  a string that opens on an unchanged (context) line can still
  suppress. Documented in the code; a full fix needs the surrounding
  file content, which the reviewer does not have.
- **The Responses `instructions` field was proven only on a fake
  server.** Most OpenAI-compatible providers accept it, but a strict
  provider could reject it — use `api_format: chat_completions` for
  such a provider.
- **The bot's self-review still reports static false positives.**
  Its review runs the v0.2.4 action, so the ~20 static findings on
  PR #12 (placeholder tokens in fixtures/examples, `eval()` in docs
  and tests) are v0.2.4's checks, not v0.3.0's. The v0.3.0 checks
  themselves give 0 findings on the same diff.
- **Findings are suggestions, not verdicts.** The AI reviewer can
  miss real bugs (experimental PR #5's unbounded-cache leak is a live
  example) and can flag code that is fine. Always verify before
  acting.

## Install

```bash
pip install pr-sentinel-ai
```

Or use the GitHub Action — see the README quick start.
