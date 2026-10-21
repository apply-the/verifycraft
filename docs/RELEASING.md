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
$$\text{Git Tag (\texttt{vX.Y.Z})} == \text{\texttt{pyproject.toml}} == \text{\texttt{verifycraft.\_\_version\_\_}} == \text{\texttt{importlib.metadata.version("verifycraft")}}$$
Any mismatch will immediately abort the release gate.

---

## 2. Security & Trusted Publishing Configuration

VerifyCraft publishes to PyPI exclusively using **PyPI Trusted Publishing** via OpenID Connect (OIDC). **No permanent, long-lived API tokens or passwords are used.**

### PyPI Trusted Publisher Configuration (One-Time Setup)

Before a release workflow can publish, the PyPI publisher must be registered in the PyPI project management interface:

1. **PyPI Admin**: Navigate to [PyPI Publishing Settings](https://pypi.org/manage/account/publishing/).
2. **Add a Publisher**:
   - **PyPI Project Name**: `verifycraft` (or pending publisher for first release)
   - **Owner / Organization**: `apply-the`
   - **Repository Name**: `verifycraft`
   - **Workflow Name**: `release.yml`
   - **Environment Name**: `pypi`

### GitHub Environment Protection (One-Time Setup)

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
git pull --ff-only

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

Releases are triggered **strictly by pushing a signed or annotated Git tag from the current HEAD of `main`**:

```bash
# Ensure you are on the latest verified commit on main
git checkout main
git pull --ff-only

# Create an annotated tag matching the version
git tag -a v0.1.0a1 -m "VerifyCraft 0.1.0a1"

# Push the tag to GitHub
git push origin v0.1.0a1
```

> [!IMPORTANT]
> The release workflow verifies that:
> 1. The tag matches the release naming convention: `^v[0-9]+\.[0-9]+\.[0-9]+(a[0-9]+|b[0-9]+|rc[0-9]+)?$`.
> 2. The tag commit is the exact current HEAD of `origin/main` (`test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"`). Tags created from older commits, arbitrary branches, or PRs will be rejected immediately.

### Step 4: Monitor the Release Workflow

1. Navigate to **Actions** > **Release to PyPI** on GitHub.
2. The workflow executes three sequential stages:
   - **`test-matrix`**: Runs pytest across Python 3.11 and Python 3.12 on Linux.
   - **`verify`**: Verifies release ref and `origin/main` HEAD match, checks package metadata and version alignment, runs quality gates (`ruff`, `mypy`, `pytest --cov`, `benchmarks.evaluate`), builds wheel and sdist once, checks distributions with `twine check --strict`, and uploads the `release-distributions` artifact.
   - **`publish`**: Gated by the `pypi` environment (and maintainer approvals), downloads the verified `release-distributions`, mints an OIDC token, and publishes to PyPI via `pypa/gh-action-pypi-publish`.

### Step 5: Verify Package Installation & Create GitHub Release

After publication completes:
```bash
# Verify installation from PyPI
pip install verifycraft==0.1.0a1
verifycraft --version

# Create GitHub Release page (distinct from Git tag)
gh release create v0.1.0a1 --prerelease --generate-notes
```

---

## 4. TestPyPI Validation (Optional)

To test the packaging and publication workflow against TestPyPI before a production release:
1. Register a Trusted Publisher on [test.pypi.org](https://test.pypi.org/manage/account/publishing/) with environment `testpypi`.
2. Configure a test workflow pointing `repository-url` to `https://test.pypi.org/legacy/`.
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

1. **Failure during `test-matrix` or `verify` (Before Publish)**:
   - No artifacts were uploaded to PyPI.
   - Delete the remote tag:
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
