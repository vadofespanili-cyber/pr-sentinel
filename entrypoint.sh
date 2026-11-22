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

# Trim leading/trailing whitespace (POSIX parameter expansion, works
# in dash): a whitespace-only input counts as empty and is not
# forwarded to the CLI.
_trim_ws() {
  _v=$1
  _v=${_v#"${_v%%[![:space:]]*}"}
  _v=${_v%"${_v##*[![:space:]]}"}
  printf '%s' "$_v"
}

# Build the CLI invocation; optional inputs are forwarded only when set.
set -- review --repo "$REPO" --pr "$PR_NUMBER"
if [ -n "${INPUT_CONFIG_PATH:-}" ]; then
  set -- "$@" --config "${INPUT_CONFIG_PATH}"
fi
FAIL_ON_SEVERITY=$(_trim_ws "${INPUT_FAIL_ON_SEVERITY:-}")
if [ -n "$FAIL_ON_SEVERITY" ]; then
  set -- "$@" --fail-on-severity "$FAIL_ON_SEVERITY"
fi
SARIF_OUT=$(_trim_ws "${INPUT_SARIF_OUT:-}")
if [ -n "$SARIF_OUT" ]; then
  set -- "$@" --sarif-out "$SARIF_OUT"
fi
exec pr-sentinel "$@"
