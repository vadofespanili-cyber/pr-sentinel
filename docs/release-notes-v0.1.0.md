# pr-sentinel v0.1.0 — Release notes

First public release of pr-sentinel: an AI-powered pull request review
bot, as a GitHub Action and a CLI.

## What it does

Every pull request gets a first review within seconds. pr-sentinel reads
the PR diff, runs deterministic static checks, asks an AI model (via a
Responses-compatible API) to review the changes, and posts a Markdown
summary plus line-level review comments — with severity
(critical/high/medium/low) and category (bug/security/performance/
style/maintainability) on every finding. It never merges, closes or
changes code; it only comments, and the human always decides.

## Highlights

- **GitHub Action** (`action.yml` + `Dockerfile`): automatic review on
  every pull request, with one summary comment and inline comments.
- **CLI** (`pr-sentinel`): `review --repo OWNER/REPO --pr NUMBER`,
  `review-diff --file diff.patch` for local use without GitHub, and
  `check-config` to validate a config file.
- **Two review layers:** fast static checks (leaked secrets, `eval`/`exec`,
  `shell=True`, `pickle`, oversized files) plus an AI reviewer for the
  things regexes cannot see.
- **Zero-dependency core:** Python standard library only — nothing extra
  to install, audit or keep up to date.
- **Safe by default:** API keys come from environment variables only;
  secret values are never echoed in findings or logs.

## Verification

- 78 pytest tests, all passing.
- `ruff check` and `ruff format` clean.
- A full review cycle was verified end to end against test servers:
  a sample patch produced 4 findings (1 critical leaked API key,
  1 high `eval()` use, 1 medium and 1 low from the AI reviewer),
  and the review was posted with the correct inline-comment positions.

## Install

```bash
pip install git+https://github.com/vadofespanili-cyber/pr-sentinel.git
```

Or use the GitHub Action — see the README quick start.

## Known limitations

Findings are suggestions, not verdicts: the AI reviewer can miss real
bugs and can flag code that is fine. No code is executed; review scope
is function-level, one diff chunk at a time. See the README
"Limitations (honest)" section for the full list.
