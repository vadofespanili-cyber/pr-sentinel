# pr-sentinel

[![CI](https://github.com/vadofespanili-cyber/pr-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/vadofespanili-cyber/pr-sentinel/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

AI-powered pull request review bot — as a GitHub Action and a CLI.

pr-sentinel reviews a pull request's diff with an AI model (via a
Responses-compatible API) and deterministic static checks. It finds bugs,
security issues and performance problems, posts a summary comment, adds
line-level review comments, and can also run fully locally on a patch file.

## Why pr-sentinel?

Code review is the bottleneck of open-source maintenance. Maintainers
drown in pull requests: every PR needs a careful read, but reviewers are
few and their time is the scarcest resource in the ecosystem. Small
projects wait days for a first review; large ones merge risky code
because nobody had time to look closely.

pr-sentinel gives every pull request a first review within seconds —
catching leaked secrets, dangerous calls and obvious bugs before a human
spends time on the diff, so maintainers can focus their review on
design and correctness instead of hunting for mistakes. It never merges,
closes or changes code; it only comments, and the human always decides.

- **Two layers:** fast static checks (leaked secrets, `eval`/`exec`,
  `shell=True`, `pickle`, huge files) plus an AI reviewer for the things
  regexes cannot see.
- **Zero-dependency core:** the package uses only the Python standard
  library. No extra packages to install, audit or keep up to date.
- **Works anywhere:** GitHub Action for automatic reviews, CLI for local
  use and CI systems other than GitHub Actions.
- **Safe by default:** API keys come from environment variables only, never
  from config files or the command line. Secret values are never echoed in
  findings or logs.

## Quick start — GitHub Action

```yaml
# .github/workflows/pr-review.yml
name: PR review
on:
  pull_request:
    types: [opened, synchronize]

permissions:
  pull-requests: write
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: vadofespanili-cyber/pr-sentinel@v0.1.1
        with:
          api_key: ${{ secrets.PR_SENTINEL_API_KEY }}
```

`PR_SENTINEL_API_KEY` must be a key for a Responses-compatible model
provider. The action posts one summary comment and line-level comments on
the pull request. It never merges, closes or changes code — it only
comments.

A full example is in [examples/github-workflow.yml](examples/github-workflow.yml).

## Quick start — CLI

```bash
pip install git+https://github.com/vadofespanili-cyber/pr-sentinel.git
export PR_SENTINEL_API_KEY="sk-..."
export GITHUB_TOKEN="ghp_..."

# Review a pull request and post the review on GitHub
pr-sentinel review --repo octocat/hello-world --pr 42

# Review a local patch file without touching GitHub
pr-sentinel review-diff --file changes.patch

# Preview without posting anything
pr-sentinel review --repo octocat/hello-world --pr 42 --dry-run

# Validate a config file
pr-sentinel check-config --config .pr-sentinel.yaml
```

The CLI exits `0` on success, `2` on config or usage errors and `3` on
API errors. Findings never change the exit code: a PR with findings still
exits `0`, so the review never fails your build.

## Configuration

Optional file `.pr-sentinel.yaml` (or `.pr-sentinel.yml`) in the working
directory, or `--config PATH`. An example is in
[examples/pr-sentinel.example.yaml](examples/pr-sentinel.example.yaml).

```yaml
model: gpt-5                 # model name sent to the Responses API
severity_threshold: medium   # lowest severity to report: critical|high|medium|low
ignore_paths:                # fnmatch globs, e.g. "vendor/**", "*.min.js"
  - "docs/**"
max_files: 50                # review at most this many files per PR
max_diff_chars: 100000       # truncate larger diffs with a warning
mode: inline                 # inline = summary + line comments, summary = summary only
max_inline_comments: 20      # cap on line-level comments
api_base_url: https://api.openai.com/v1
```

| Setting | Default |
| --- | --- |
| `model` | `gpt-5` |
| `severity_threshold` | `medium` |
| `ignore_paths` | `[]` |
| `max_files` | `50` |
| `max_diff_chars` | `100000` |
| `mode` | `inline` |
| `max_inline_comments` | `20` |
| `api_base_url` | `https://api.openai.com/v1` |

Environment variables:

| Variable | Purpose |
| --- | --- |
| `PR_SENTINEL_API_KEY` | API key for the model provider (required for AI review; without it only static checks run) |
| `GITHUB_TOKEN` | GitHub token with pull-request read/write access (required for `review`) |

## Example output

Reviewing the sample patch in [examples/sample-pr.diff](examples/sample-pr.diff)
produces this summary (the full JSON is in
[examples/sample-findings.json](examples/sample-findings.json)):

```markdown
# pr-sentinel review

Found 4 findings: 1 critical, 1 high, 1 medium, 1 low.

| Severity | File | Line | Message |
| --- | --- | --- | --- |
| critical | app.py | 3 | Possible OpenAI API key in added code. |
| high | app.py | 4 | Use of eval() on added code. |
| medium | app.py | 6 | Nested loops run 1,000,000 iterations to build a list. |
| low | app.py | 6 | Magic number 1000 is used without explanation. |

> **Note:** These findings are AI-generated suggestions. Please verify them before acting on them.

---
Reviewed by pr-sentinel
```

Each finding also becomes a line-level comment at the exact diff position,
for example `**critical · security** — Possible OpenAI API key in added
code.` with a suggested fix.

## How it works

1. **Fetch the diff** — from the GitHub API (`review`) or a local patch
   file (`review-diff`).
2. **Parse** — the unified diff is parsed into files, hunks and numbered
   lines; each added line is mapped to its GitHub diff position so inline
   comments land on the right line.
3. **Static checks** — deterministic, no-AI checks over added lines only:
   leaked secrets, dangerous calls, oversized files.
4. **AI review** — the diff is split into chunks that fit the model's
   context and each chunk is reviewed through the Responses API. The model
   must return a JSON array of findings; anything unparseable is rejected.
5. **Filter and report** — findings below the severity threshold or in
   ignored paths are dropped, then a Markdown summary and inline comments
   are posted (or printed in `--dry-run` / `review-diff` mode).

See [docs/architecture.md](docs/architecture.md) for the module map and
design decisions.

## Limitations (honest)

- **Findings are suggestions, not verdicts.** The AI reviewer can miss real
  bugs and can flag code that is fine. Always verify before acting.
- **No code is executed.** pr-sentinel reads diffs only; it cannot run
  tests, reproduce a bug or prove a finding.
- **Function-level scope.** The reviewer sees one diff chunk at a time, so
  it cannot reliably reason about cross-file architecture or whole-repo
  invariants.
- **Static checks are single-line regexes.** Multi-line patterns (for
  example `yaml.load(` split across lines) are not detected, and the
  generic credential pattern can fire on harmless test values.
- **Model-dependent.** Review quality depends on the model and on the diff
  fitting into context; very large PRs are truncated at `max_diff_chars`.
- **No retries.** A failed AI request (network error, rate limit) falls
  back to static checks only, with a warning — it does not retry.
- **English prompts.** Review prompts and messages are in English in
  v0.1.0.

## Releases

Versioned releases live on the
[Releases page](https://github.com/vadofespanili-cyber/pr-sentinel/releases).
The current release is **v0.1.1** — see the
[Changelog](CHANGELOG.md) and the
[release notes](docs/release-notes-v0.1.1.md) for what it includes.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

Python 3.10+; the package itself has no runtime dependencies. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Report vulnerabilities as described in [SECURITY.md](SECURITY.md). Never
put real API keys or tokens in issues, pull requests or test files.

## License

MIT — see [LICENSE](LICENSE).
