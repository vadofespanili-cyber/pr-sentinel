#!/bin/sh
# GitHub Action entrypoint: resolve the PR number and run the review.
set -e

PR_NUMBER="${INPUT_PR_NUMBER:-}"
if [ -z "$PR_NUMBER" ] && [ -n "$GITHUB_EVENT_PATH" ]; then
  PR_NUMBER=$(python3 -c "import json,os; print(json.load(open(os.environ['GITHUB_EVENT_PATH'])).get('pull_request', {}).get('number', ''))")
fi

if [ -z "$PR_NUMBER" ]; then
  echo "error: no pull request number (set the pr-number input or run on a pull_request event)" >&2
  exit 2
fi

export GITHUB_TOKEN="${INPUT_GITHUB_TOKEN:-$GITHUB_TOKEN}"
export PR_SENTINEL_API_KEY="${INPUT_API_KEY:-$PR_SENTINEL_API_KEY}"

if [ -n "${INPUT_CONFIG_PATH:-}" ]; then
  exec pr-sentinel review --repo "${INPUT_REPOSITORY}" --pr "$PR_NUMBER" --config "${INPUT_CONFIG_PATH}"
else
  exec pr-sentinel review --repo "${INPUT_REPOSITORY}" --pr "$PR_NUMBER"
fi
