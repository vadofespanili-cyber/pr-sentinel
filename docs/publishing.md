# Publishing pr-sentinel

Status as of 2026-11-23, v0.2.2. Two destinations: **PyPI** and the
**GitHub Marketplace**. Nothing in this document uploads anything — it is
the checklist for the first publish. The workflow file
`.github/workflows/publish.yml` is prepared locally and not yet pushed.

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
duplicate name then.

**Decision (2026-11-23, Jalal): the new name is `pr-sentinel-ai`** for
both the PyPI distribution name and the `action.yml` name. The
`pr-sentinel` console command stays as it is — the command name is
independent of the distribution name. v0.2.2 is the first release
under the new name, and the rename ships in its own PR before any
publish.

## 1. PyPI — current status

| Item | Status |
|---|---|
| `pyproject.toml` metadata (build backend, version 0.2.2, description, readme, license, requires-python, classifiers, authors, URLs, keywords, entry point) | Ready — fixed in this prep (see §4) |
| Distribution name | **`pr-sentinel-ai`** — renamed in v0.2.2 (this PR) |
| Local build | Works — see §2 |
| `twine check` | PASSED for wheel and sdist |
| `.github/workflows/publish.yml` | Prepared locally (release trigger, Trusted Publishing); **not pushed** |
| PyPI trusted publisher configured | **Missing** — Jalal, PyPI side (§3) |
| `pypi` GitHub environment | **Missing** — needs creating (§3) |
| PyPI account + 2FA | **Missing** — Jalal (§3) |

## 2. Local build (verified 2026-11-23, NOT uploaded)

```bash
.venv/bin/pip install build twine
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

Artifacts (rebuilt from v0.2.2, after the rename to `pr-sentinel-ai`):

- `dist/pr_sentinel_ai-0.2.2-py3-none-any.whl` (35,342 bytes)
- `dist/pr_sentinel_ai-0.2.2.tar.gz` (35,901 bytes)

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

- Rename PR (this PR): ships the rename to `pr-sentinel-ai` as
  v0.2.2 (`pyproject.toml` name, `action.yml` name, README/docs
  mentions, install command).
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

- The distribution name is now `pr-sentinel-ai` (v0.2.2, this PR).
- The README install command is now `pip install pr-sentinel-ai`
  (this PR) — add the PyPI version badge after the first PyPI upload.

## 5. GitHub Marketplace — current status

| Item | Status |
|---|---|
| `action.yml`: name, description (31 chars; limit 125), author, `runs.docker` + `Dockerfile`, branding `eye` / `blue` | Valid |
| Repository public, tagged releases (v0.2.2 latest), action already used by the self-review workflow | Ready |
| Action name | **`pr-sentinel-ai`** — renamed in v0.2.2 (this PR) |
| Marketplace Developer Agreement accepted | **Missing** — Jalal, at publish time (§6) |
| GitHub 2FA on Jalal's account | **Required** — publishing requires it (§6) |

**Key finding: a release can be listed by editing it — no new release
is needed for the listing itself.** The release edit page has a
"Publish this Action to the GitHub Marketplace" checkbox. But it must
be checked on a release carrying the new name — v0.2.2, not v0.2.1
(v0.2.1's `action.yml` still says `pr-sentinel`, so listing that tag
would publish the old, conflicting name). Sources: GitHub Docs,
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
3. Accept the **GitHub Marketplace Developer Agreement** (first time
   only; the checkbox is disabled until it is accepted).
4. Pick the **Primary category** (suggested: Continuous integration)
   and optionally a secondary category.
5. Click **Update release**. The action appears at
   `github.com/marketplace/actions/<slug>` within minutes.
6. GitHub 2FA must be enabled on the publishing account — publishing
   requires it.

(Muse) After it is live: add the Marketplace badge to the README.

## 7. What is needed from Jalal (summary)

- [x] New name picked: `pr-sentinel-ai` (2026-11-23); the
  `pr-sentinel` console command stays as it is.
- [ ] PyPI account + verified email + 2FA enabled.
- [ ] Add the PyPI Trusted Publisher (pending publisher) for
  `pr-sentinel-ai` — needs his PyPI login; Muse can walk him through it.
- [ ] Create the `pypi` GitHub environment (or approve Muse doing it
  via the browser).
- [ ] GitHub 2FA enabled (needed to publish to the Marketplace) +
  accept the Marketplace Developer Agreement at publish time.
- [ ] (Marketplace) Check the "Publish this Action" box on the release
  (browser, ~2 min) — or let Muse do it via the browser.

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
