# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting a vulnerability

Do **not** open a public issue for a security problem. Instead, open a
private security advisory on GitHub or email the maintainer. Include a
description, reproduction steps and, if known, the affected versions.

## Handling of secrets

pr-sentinel reads API keys only from environment variables
(`PR_SENTINEL_API_KEY`, `GITHUB_TOKEN`). Never put real keys in config
files, issues, pull requests or tests. Static-check findings describe the
rule that fired and never repeat the matched secret value.
