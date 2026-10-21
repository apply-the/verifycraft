# VerifyCraft Release Guide

This document outlines the release process, versioning policy, and distribution security controls for VerifyCraft.

---

## 1. Version Management & Release Conventions

VerifyCraft follows [Semantic Versioning 2.0.0](https://semver.org/) and [PEP 440](https://peps.python.org/pep-0440/):

- **Format**: `MAJOR.MINOR.PATCH[aN | bN | rcN]`
  - Example pre-release: `0.1.0a1` (first alpha of 0.1.0)
  - Example release candidate: `0.1.0rc1`
  - Example stable release: `0.1.0`
- **Git Tags**: Release tags must be prefixed with `v` (e.g., `v0.1.0a1`).

### Single Source of Truth

The package version is declared in two synchronized locations:
1. `pyproject.toml` under `[project].version`:
   ```toml
   [project]
   name = "verifycraft"
   version = "0.1.0a1"
   ```
2. `src/verifycraft/__init__.py`:
   ```python
   __version__ = "0.1.0a1"
   ```

The automated release workflow validates that:
$$\text{Git Tag (\texttt{vX.Y.Z})} == \text{\texttt{pyproject.toml}} == \text{\texttt{verifycraft.\_\_version\_\_}}$$
Any mismatch will immediately abort the release gate.

---

## 2. Security & Trusted Publishing Configuration

VerifyCraft publishes to PyPI exclusively using **PyPI Trusted Publishing** via OpenID Connect (OIDC). **No permanent, long-lived API tokens or passwords are used.**

### PyPI Trusted Publisher Configuration

Before a release workflow can publish, the PyPI publisher must be registered in the PyPI project management interface:

1. **PyPI Admin**: Navigate to [PyPI Publishing Settings](https://pypi.org/manage/account/publishing/).
2. **Add a Publisher**:
   - **PyPI Project Name**: `verifycraft` (or pending publisher for first release)
   - **Owner / Organization**: `apply-the`
   - **Repository Name**: `verifycraft`
   - **Workflow Name**: `release.yml`
   - **Environment Name**: `pypi`

### GitHub Environment Protection

The release workflow is bound to the GitHub Environment `pypi`:

1. In the GitHub repository, navigate to **Settings** > **Environments** > **`pypi`**.
2. **Deployment Branches**: Restrict deployment to `main` branch state only.
3. **Required Reviewers**: Configure designated maintainers whose approval is required before the publish job executes.
4. **Environment Secrets**: None required; authentication is handled via short-lived OIDC tokens exchanged directly between GitHub Actions and PyPI.
5. **Permissions**: The `id-token: write` permission is strictly isolated to the `publish` job. All upstream verification jobs run with read-only access.

---

## 3. Pre-Release & Release Procedure

Follow this step-by-step checklist to perform a release:

### Step 1: Pre-Release Verification on `main`

Ensure the target release commit is already merged to `main` and passes all CI workflows:

```bash
# Check out the main branch and ensure it is up to date
git checkout main
git pull origin main

# Run local quality gates
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=verifycraft
uv run python -m benchmarks.evaluate
uv run python -m build
uv run twine check --strict dist/*
```

### Step 2: Version Bump

If version updates are needed:
1. Create a branch: `chore/bump-version-to-0.1.0a1`
2. Update `pyproject.toml` and `src/verifycraft/__init__.py`.
3. Open a PR into `main`, obtain review, and merge after CI succeeds.

### Step 3: Tag Creation & Push

Releases are triggered **strictly by pushing a signed or annotated Git tag**:

```bash
# Ensure you are on the latest verified commit on main
git checkout main
git pull origin main

# Create an annotated tag matching the version
git tag -a v0.1.0a1 -m "Release VerifyCraft v0.1.0a1"

# Push the tag to GitHub
git push origin v0.1.0a1
```

> [!IMPORTANT]
> The release workflow verifies that the tag commit exists on `origin/main`. Tags created from arbitrary branches or pull requests will fail the ancestor check and will be rejected.

### Step 4: Monitor the Release Workflow

1. Navigate to **Actions** > **release** on GitHub.
2. The workflow executes three sequential stages:
   - **`test-gate`**: Verifies commit ancestry on `origin/main`, runs full linting, type checks, unit/regression tests, and benchmark evaluation.
   - **`build`**: Validates tag-version consistency with `tomllib`, builds source distribution (`.tar.gz`) and wheel (`.whl`), validates package metadata with `twine check --strict`, and uploads distribution artifacts.
   - **`publish`**: Gated by the `pypi` environment (and any configured approvals), downloads the exact built artifacts, mints an OIDC token, and publishes to PyPI via `pypa/gh-action-pypi-publish`.

---

## 4. TestPyPI Validation (Optional)

To test the packaging and publication workflow against TestPyPI before a production release:
1. Register a Trusted Publisher on [test.pypi.org](https://test.pypi.org/manage/account/publishing/).
2. You may configure a dedicated testing workflow or point the action's `repository-url` input to `https://test.pypi.org/legacy/`.
3. Validate wheel installation from TestPyPI in an isolated environment:
   ```bash
   uv venv test-env --python 3.11
   source test-env/bin/activate
   pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ verifycraft==0.1.0a1
   verifycraft --version
   ```

---

## 5. Handling Failed Publications & Artifact Immutability

### PyPI Immutability Rule

> [!CAUTION]
> **PyPI releases are immutable.** Once a file (e.g., `verifycraft-0.1.0a1-py3-none-any.whl` or `verifycraft-0.1.0a1.tar.gz`) is uploaded to PyPI, it can **never** be re-uploaded, overwritten, or replaced—even if the release is deleted from PyPI.

### Failure Scenarios & Recovery

1. **Failure during `test-gate` or `build` (Before Publish)**:
   - No artifacts were uploaded to PyPI.
   - Delete the remote tag if appropriate:
     ```bash
     git tag -d v0.1.0a1
     git push origin :refs/tags/v0.1.0a1
     ```
   - Fix the issue on a branch, merge to `main`, and re-tag.

2. **Failure during `publish` (After Partial Upload)**:
   - If the wheel or source archive was accepted by PyPI before failure, that version number is permanently locked.
   - **Do NOT attempt to re-push the same tag.**
   - Increment the patch or pre-release version (e.g., from `0.1.0a1` to `0.1.0a2`).
   - Merge the version increment to `main` and tag the new version.
