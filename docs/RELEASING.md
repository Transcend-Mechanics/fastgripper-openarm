# Releasing to PyPI — step-by-step

Same trusted-publishing setup as `fastgripper-lerobot` (GitHub OIDC: PyPI
trusts this repo's `release.yml` directly; no API tokens anywhere).

## Before any of this matters

Two decisions are still open (flagged in `pyproject.toml`):
- **License** — currently `Proprietary`; sister repo uses Apache-2.0.
  Publishing to PyPI with a proprietary license is legal but unusual; decide
  deliberately.
- The repo is **private** today. Trusted publishing works from private repos,
  but a PyPI release makes the *package* public worldwide even if the source
  stays private. Until then, the friend installs via
  `pip install git+ssh://...` (see README) — no release needed.

## One-time setup (~10 minutes, in a browser)

You need [pypi.org](https://pypi.org) and [test.pypi.org](https://test.pypi.org)
accounts (separate signups) with 2FA.

On **both** sites, register a *pending publisher* (Your account → Publishing
→ Add a new pending publisher, GitHub tab):

| Field | Value |
| --- | --- |
| PyPI Project Name | `fastgripper-openarm` |
| Owner | `Transcend-Mechanics` |
| Repository name | `fastgripper-openarm` |
| Workflow name | `release.yml` |
| Environment name | `pypi` (use `testpypi` on test.pypi.org) |

Then in the GitHub repo: **Settings → Environments** → create `pypi` and
`testpypi` (no secrets; optionally require your approval on `pypi`).

## Routine packaging health (automatic)

CI's `package-smoke` job builds the wheel, installs it in a clean venv, and
runs every console entry point on every push (Ubuntu + macOS, Python 3.10
and 3.13) — packaging breakage is caught continuously without publishing.

## Dry run against TestPyPI (before the first real release)

GitHub → **Actions → Release → Run workflow** (branch = main) publishes the
current `0.1.0.dev0` to TestPyPI. Verify in a clean venv:

```sh
uv venv /tmp/fgo --python 3.10
uv pip install -p /tmp/fgo/bin/python python-can
uv pip install -p /tmp/fgo/bin/python \
  --index-url https://test.pypi.org/simple/ --no-deps fastgripper-openarm
/tmp/fgo/bin/fastgripper-autocal --help
/tmp/fgo/bin/python -c "import fastgripper; print(fastgripper.__version__)"
```

## The real release

1. Bump the version in `pyproject.toml` (`0.1.0.dev0` → `0.1.0`), commit,
   push, wait for CI green.
2. Tag and push the tag — this IS the release trigger:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. Watch **Actions → Release**; when `publish-pypi` finishes,
   `pip install fastgripper-openarm` is live (and the name is permanently
   claimed).
4. Optionally: `gh release create v0.1.0 --generate-notes`.

## Future releases

Bump version → commit → tag `vX.Y.Z` → push tag.

Note in release notes any change to hardware-validated constants (probe
torque defaults, contact thresholds, stall timing) — customers' grippers
depend on them.
