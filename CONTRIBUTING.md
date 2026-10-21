# Contributing to VerifyCraft

Thank you for your interest in contributing to VerifyCraft!

VerifyCraft is designed to bring composable, typed, and verifiable capabilities to AI agents. To ensure stability and trust, all contributions must adhere to our engineering principles and quality standards.

---

## 1. Code of Conduct

We are committed to providing a welcoming, constructive, and respectful environment for all contributors. Treat everyone with respect, adhere to open-source etiquette, and keep discussions focused on engineering soundness and evidence-backed claims.

---

## 2. Branch and Commit Conventions

To maintain clean repository history and enable automated changelog generation, we enforce strict naming and commit conventions via CI.

### Branch Naming

All pull request branches must follow this pattern:
```text
^(feature|bugfix|fix|hotfix|docs|chore|refactor|test|ci|dependabot)/.+$
```

Examples:
- `feature/pytest-isolation`
- `fix/stale-evidence-invalidation`
- `docs/clarify-releasing`
- `chore/update-dependencies`

### Commit Messages

Commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
```text
<type>(<optional scope>): <description>
```

Allowed types:
`build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`.

Examples:
- `feat(core): add independent provenance authenticator`
- `fix(fingerprint): fail closed when scope exceeds limit`
- `docs(releasing): document PyPI Trusted Publishing setup`

---

## 3. Development Workflow

### Prerequisites

- Python 3.11 or 3.12
- [uv](https://docs.astral.sh/uv/) (recommended) or standard `pip`

### Setup

```bash
# Clone the repository
git clone https://github.com/apply-the/verifycraft.git
cd verifycraft

# Install dependencies in development mode using uv
uv sync --extra dev

# Alternatively, using pip:
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## 4. Quality Gates

Before opening a pull request, all automated quality gates must pass locally:

```bash
# 1. Linting and formatting
uv run ruff check .
uv run ruff format --check .

# 2. Static type checking
uv run mypy

# 3. Unit and regression tests
uv run pytest --cov=verifycraft

# 4. Benchmark contract evaluation
uv run python -m benchmarks.evaluate

# 5. Build and distribution verification
uv run python -m build
uv run twine check --strict dist/*
```

---

## 5. Security & Verification Rules

VerifyCraft is a security-sensitive verification engine:
1. **Never bypass execution policies**: `untrusted_code=True` must never execute on a bare host.
2. **Fail closed**: If file discovery, fingerprinting, or evidence authentication cannot be verified, fail closed (`FingerprintError` or `Verdict.BLOCKED`).
3. **Preserve test independence**: Deterministic checks must remain decoupled from model judgment or stochastic prompt output.
4. **No unauthorized dependencies**: Keep the core dependency footprint minimal (`pydantic`). Optional integrations belong in separate adapters.

---

## 6. Pull Request Process

1. Fork the repo and create your feature/fix branch from `main`.
2. Ensure new features or bug fixes include executable tests under `tests/`.
3. Verify that all 5 quality gates above pass cleanly.
4. Push your branch and open a Pull Request targeting `main`.
5. Address review feedback and ensure all GitHub Actions CI checks pass.
