# Publishing pr-sentinel

Status as of 2026-11-23, v0.2.3. Two destinations: **PyPI** and the
**GitHub Marketplace**. Nothing in this document uploads anything — it is
the checklist for the first publish. The workflow file
`.github/workflows/publish.yml` lands in this PR
(`chore/add-publish-workflow`).

## 0. The name problem — read this first

The name `pr-sentinel` is taken on **both** registries, by two different
unrelated projects:

- **PyPI:** `pr-sentinel` exists already — a local PR-review CLI by
  Yarramaddi Kishor Kumar Reddy
  (<https://pypi.org/project/pr-sentinel/>,
  <https://github.com/kishor2004reddy/Pull-Request-Sentinel-CLI>). Our
  package cannot be uploaded under that name.
- **GitHub Marketplace:** an action named "PR Sentinel" already exists
  (hasunB/pr-sentinel,
  <https://github.com/marketplace/actions/pr-sentinel-ai-rules-based-pr-reviewer>).
  GitHub requires Marketplace action names to be unique, so our
  `action.yml` name `pr-sentinel` will almost certainly be rejected too.

Checked free on PyPI on 2026-11-23 (`https://pypi.org/pypi/<name>/json`
returns 404): `pr-sentinel-ai`, `pr-sentinel-bot`, `github-pr-sentinel`,
`pr-sentinel-review`, `ai-pr-sentinel`, `prsentinel`. No Marketplace
action named "pr-sentinel-ai" turned up in search either — but that is
not proof; GitHub enforces uniqueness at publish time and will reject a
duplicate name then. That is exactly what happened: when v0.2.2 was
submitted to the Marketplace, GitHub rejected the name
`pr-sentinel-ai` — not because of an existing action, but because an
**organization named `pr-sentinel-ai` already exists**
(`api.github.com/users/pr-sentinel-ai` → type Organization, 0 public
repos). GitHub requires action names to be unique across actions, users
and organizations, so the `pr-sentinel-ai` action name can never be
listed.

**Decision (2026-11-23, Jalal): the PyPI distribution name stays
`pr-sentinel-ai`, and the Marketplace action name is now
`ai-pr-sentinel`** (verified free on GitHub and on the Marketplace —
both 404 — before the v0.2.3 release). The `pr-sentinel` console
command stays as it is — the command name is independent of the
distribution name. v0.2.3 is the first release carrying the listable
action name, and the rename ships in its own PR before any publish.
v0.2.2 carries the blocked name and cannot be listed.

## 1. PyPI — current status

| Item | Status |
|---|---|
| `pyproject.toml` metadata (build backend, version 0.2.3, description, readme, license, requires-python, classifiers, authors, URLs, keywords, entry point) | Ready — fixed in this prep (see §4) |
| Distribution name | **`pr-sentinel-ai`** — renamed in v0.2.2 |
| Local build | Works — see §2 |
| `twine check` | PASSED for wheel and sdist |
| `.github/workflows/publish.yml` | Added in this PR (release trigger + manual dispatch, Trusted Publishing) |
| PyPI trusted publisher configured | **Done 2026-11-23** — pending publisher for `pr-sentinel-ai` (repo `vadofespanili-cyber/pr-sentinel`, workflow `publish.yml`, environment `pypi`) (§3) |
| `pypi` GitHub environment | **Done 2026-11-23** — created with `vadofespanili-cyber` as required reviewer (§3) |
| PyPI account + 2FA | **Done 2026-11-23** — account `bigjalal`, email verified, TOTP 2FA enabled (§3) |

## 2. Local build (verified 2026-11-23, NOT uploaded)

```bash
.venv/bin/pip install build twine
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

Artifacts (rebuilt from v0.2.3, after the Marketplace action rename to
`ai-pr-sentinel` — the distribution name is unchanged):

- `dist/pr_sentinel_ai-0.2.3-py3-none-any.whl` (35,344 bytes)
- `dist/pr_sentinel_ai-0.2.3.tar.gz` (35,894 bytes)

`twine check` PASSED for both. Wheel contents: only the `pr_sentinel`
package plus `dist-info` (including `LICENSE`, entry point, METADATA).
The sdist contains the package, `README.md`, `LICENSE`,
`pyproject.toml`; the `tests/` directory is excluded by `MANIFEST.in`
(it was included by default before this prep — tests contain fake
credential fixtures, so they must not ship). No `.env`, secrets,
screenshots, or keys in either artifact.

## 3. PyPI — what Jalal does (owner-only)

1. **Create a PyPI account** at <https://pypi.org/account/register/>
   (if he does not have one) and **verify the email address** — a
   verified email is required to register a project or upload.
2. **Enable 2FA** on the PyPI account — mandatory for all PyPI users
   since 2024-01-01, before any management action or upload. TOTP
   authenticator app or WebAuthn security key:
   <https://pypi.org/account/two-factor/>.
3. **Project name: `pr-sentinel-ai`** (decided 2026-11-23, §0) — use
   it in step 4.
4. **Add a Trusted Publisher on PyPI** for the new project name:
   PyPI account settings → Publishing → add a trusted publisher
   (GitHub): owner `vadofespanili-cyber`, repository `pr-sentinel`,
   workflow filename `publish.yml`, environment `pypi`. A *pending*
   publisher can be created before the project exists — the project is
   created automatically on the first upload. No API token is involved.
5. **Create the `pypi` environment on GitHub** (Settings →
   Environments → New environment `pypi`). Recommended: add yourself
   as a **required reviewer**, so a release cannot reach PyPI without
   an explicit human approval.
6. (Recommended) **Dry run on TestPyPI first:** same trusted-publisher
   setup on <https://test.pypi.org> (separate account, same email is
   fine), then upload the local `dist/` once with twine using a
   TestPyPI token to prove the flow end to end.

## 4. PyPI — what Muse does

- Rename PR (v0.2.3): ships the Marketplace action rename to
  `ai-pr-sentinel` (`action.yml` name only; the PyPI distribution name
  stays `pr-sentinel-ai`, as do the README/docs mentions and the
  install command).
- Push `.github/workflows/publish.yml` (workflow files go through the
  GitHub web UI — the App token cannot write them) and the rename PR;
  merge, tag, release as usual.
- Verify the publish workflow run, then verify `pip install pr-sentinel-ai`
  in a clean venv and that `pr-sentinel --version` works.

### Metadata gaps found and fixed in this prep

- `pyproject.toml` had **no `[project.optional-dependencies]`** although
  the README tells contributors to run `pip install -e ".[dev]"` —
  added `dev = ["pytest", "ruff", "mypy"]`.
- `license = { text = "MIT" }` plus the MIT classifier triggered a
  setuptools warning suggesting an SPDX license expression — changed to
  `license = "MIT"`, removed the redundant classifier, bumped the
  build-system requirement to `setuptools>=77` (needed for license
  expressions).
- Authors had no email — added `vadofespanili@gmail.com` (already
  public in the git history).
- Added the `Operating System :: OS Independent` classifier and a
  `Changelog` project URL (the releases page).
- `README.md` screenshot embeds used relative paths, which render as
  broken images on a PyPI project page — changed to absolute
  `raw.githubusercontent.com` URLs (they render identically on GitHub).
- Added `MANIFEST.in` (`prune tests`) so the test suite does not ship
  in the sdist.

### Still open (not a metadata fix)

- The distribution name is now `pr-sentinel-ai` (v0.2.2).
- The README install command is now `pip install pr-sentinel-ai`
  (this PR) — add the PyPI version badge after the first PyPI upload.

## 5. GitHub Marketplace — current status

| Item | Status |
|---|---|
| `action.yml`: name, description (31 chars; limit 125), author, `runs.docker` + `Dockerfile`, branding `eye` / `blue` | Valid |
| Repository public, tagged releases (v0.2.3 latest), action already used by the self-review workflow | Ready |
| Action name | **`ai-pr-sentinel`** — renamed in v0.2.3 (this PR); `pr-sentinel-ai` is blocked by an existing organization of that name |
| Marketplace Developer Agreement accepted | **Accepted** — during the v0.2.2 listing attempt (§6) |
| GitHub 2FA on Jalal's account | **Enabled 2026-11-23** — authenticator app (§6) |

**Key finding: a release can be listed by editing it — no new release
is needed for the listing itself.** The release edit page has a
"Publish this Action to the GitHub Marketplace" checkbox. But it must
be checked on a release carrying the listable name — v0.2.3, not
v0.2.2 (v0.2.2's `action.yml` says `pr-sentinel-ai`, which GitHub
rejects because an organization of that name exists; v0.2.1's says
`pr-sentinel`, which collides with the existing "PR Sentinel" action). Sources: GitHub Docs,
"Publishing actions in GitHub Marketplace"
(<https://docs.github.com/en/actions/how-tos/sharing-automations/creating-actions/publishing-actions-in-github-marketplace>),
and community write-ups confirming the edit-release path.

One nuance: the flag is **per release**. Later releases show the
checkbox again — check it on each new release. (Some community notes
claim later releases list automatically; GitHub's own docs describe
the flag per release, and removing a listing requires editing *every*
published release, so checking it each time is the safe rule.)

## 6. GitHub Marketplace — steps (Jalal, browser, ~2 minutes)

After the rename has been released (a tagged release carrying the new
`action.yml` name):

1. Releases → open the release → **Edit release**.
2. Check **Publish this Action to the GitHub Marketplace**.
3. The **GitHub Marketplace Developer Agreement** was already
   accepted during the v0.2.2 listing attempt — it should not appear
   again (if it does, accept it; first time only, and the checkbox is
   disabled until it is accepted).
4. Pick the **Primary category** (suggested: Continuous integration)
   and optionally a secondary category.
5. Click **Update release**. The action appears at
   `github.com/marketplace/actions/ai-pr-sentinel` within minutes.
6. GitHub 2FA must be enabled on the publishing account — enabled
   2026-11-23 (authenticator app), so this is already satisfied.

(Muse) After it is live: add the Marketplace badge to the README.

## 7. What is needed from Jalal (summary)

- [x] Names picked (2026-11-23): PyPI distribution `pr-sentinel-ai`;
  Marketplace action `ai-pr-sentinel` (after `pr-sentinel-ai` was
  rejected at publish time — an organization of that name exists).
  The `pr-sentinel` console command stays as it is.
- [x] PyPI account (`bigjalal`) + verified email + 2FA enabled (2026-11-23).
- [x] PyPI Trusted Publisher (pending publisher) for
  `pr-sentinel-ai` registered 2026-11-23.
- [x] `pypi` GitHub environment created 2026-11-23 with
  `vadofespanili-cyber` as required reviewer.
- [x] GitHub 2FA enabled 2026-11-23 (authenticator app) + Marketplace
  Developer Agreement accepted.
- [x] (Marketplace) "Publish this Action" box checked on the v0.2.3
  release — listing live at
  <https://github.com/marketplace/actions/ai-pr-sentinel>.

## Sources

- PyPI Trusted Publishing (how-to):
  <https://docs.pypi.org/trusted-publishers/using-a-publisher/>
- PyPI Trusted Publishers announcement:
  <https://blog.pypi.org/posts/2023-04-20-introducing-trusted-publishers/>
- PyPI 2FA mandatory for all users (2024-01-01):
  <https://blog.pypi.org/posts/2024-01-01-2fa-enforced/>,
  <https://docs.pypi.org/about/security-and-internals/>
- GitHub Marketplace publishing:
  <https://docs.github.com/en/actions/how-tos/sharing-automations/creating-actions/publishing-actions-in-github-marketplace>
- Existing PyPI `pr-sentinel`: <https://pypi.org/project/pr-sentinel/>
- Existing Marketplace "PR Sentinel":
  <https://github.com/marketplace/actions/pr-sentinel-ai-rules-based-pr-reviewer>
