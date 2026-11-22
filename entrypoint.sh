#!/bin/sh
# GitHub Action entrypoint: resolve the PR number and run the review.
set -e

PR_NUMBER="${INPUT_PR_NUMBER:-}"
if [ -z "$PR_NUMBER" ] && [ -n "$GITHUB_EVENT_PATH" ]; then
  PR_NUMBER=$(python3 -c "import json,os; print((json.load(open(os.environ['GITHUB_EVENT_PATH'])).get('pull_request') or {}).get('number', ''))")
fi

if [ -z "$PR_NUMBER" ]; then
  echo "error: no pull request number (set the pr_number input or run on a pull_request event)" >&2
  exit 2
fi

# Only export a token when it is non-empty: an empty GITHUB_TOKEN would
# pass the CLI's token check and fail later with a confusing 401 from
# GitHub, and an empty API key would be sent as "Bearer " to the model.
if [ -n "${INPUT_GITHUB_TOKEN:-}" ]; then
  export GITHUB_TOKEN="${INPUT_GITHUB_TOKEN}"
elif [ -n "${GITHUB_TOKEN:-}" ]; then
  export GITHUB_TOKEN
fi
if [ -n "${INPUT_API_KEY:-}" ]; then
  export PR_SENTINEL_API_KEY="${INPUT_API_KEY}"
elif [ -n "${PR_SENTINEL_API_KEY:-}" ]; then
  export PR_SENTINEL_API_KEY
fi

# The repository input defaults to the repo that triggered the run.
REPO="${INPUT_REPOSITORY:-${GITHUB_REPOSITORY:-}}"

# Build the CLI invocation; optional inputs are forwarded only when set.
set -- review --repo "$REPO" --pr "$PR_NUMBER"
if [ -n "${INPUT_CONFIG_PATH:-}" ]; then
  set -- "$@" --config "${INPUT_CONFIG_PATH}"
fi
if [ -n "${INPUT_FAIL_ON_SEVERITY:-}" ]; then
  set -- "$@" --fail-on-severity "${INPUT_FAIL_ON_SEVERITY}"
fi
if [ -n "${INPUT_SARIF_OUT:-}" ]; then
  set -- "$@" --sarif-out "${INPUT_SARIF_OUT}"
fi
exec pr-sentinel "$@"
