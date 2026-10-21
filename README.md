# VerifyCraft

> **Composable review, testing, and verification capabilities for AI agents.**

[![Version](https://img.shields.io/github/v/release/apply-the/verifycraft?color=blue&label=version)](https://github.com/apply-the/verifycraft/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python: 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/apply-the/verifycraft/actions/workflows/ci.yml/badge.svg)](https://github.com/apply-the/verifycraft/actions/workflows/ci.yml)
[![Lint](https://github.com/apply-the/verifycraft/actions/workflows/lint.yml/badge.svg)](https://github.com/apply-the/verifycraft/actions/workflows/lint.yml)
[![Vulnerabilities](https://github.com/apply-the/verifycraft/actions/workflows/vulnerabilities.yml/badge.svg)](https://github.com/apply-the/verifycraft/actions/workflows/vulnerabilities.yml)
[![Coverage](https://codecov.io/gh/apply-the/verifycraft/branch/main/graph/badge.svg)](https://codecov.io/gh/apply-the/verifycraft)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=apply-the_verifycraft&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=apply-the_verifycraft)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=apply-the_verifycraft&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=apply-the_verifycraft)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=apply-the_verifycraft&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=apply-the_verifycraft)

VerifyCraft is a Python-first, framework-agnostic collection of focused capabilities designed to make AI-assisted software engineering **more verifiable without requiring developers to adopt a new agent framework**.

Each capability is designed to be useful **standalone** (via CLI and Python SDK) and **embedded** (such as inside a LangGraph workflow or GitHub Actions automation). VerifyCraft turns plausible LLM claims into **explicit, inspectable findings backed by reproducible evidence**.

---

## What VerifyCraft Does Today

In version `0.1.0`, VerifyCraft delivers the core verification engine (Stage 1 / Stage 2 vertical slice):

- **Deterministic Hypothesis Verification**: Evaluates structured regression claims against approved test targets (e.g. `pytest`).
- **Fail-Closed Verification Scope**: Resolves code context and fingerprints up to 500 relevant repository files, failing closed if limits are exceeded.
- **Root-Level Module Tracking**: Tracks root-level Python modules and configurations (`*.py`, `pyproject.toml`, `setup.cfg`) alongside `src/` and `tests/` directories.
- **Categorized Verdicts**: Returns explicit outcome semantics: `supported`, `refuted`, `inconclusive`, or `blocked`.
- **Hardened Provenance Protection**: Disables caller-provided cached execution evidence as authoritative unless verified by an independent provenance authenticator.
- **Strict Execution Policies**: Rejects untrusted code under local execution and enforces timeouts, command restrictions, and advisory limits.
- **Parity Between CLI and SDK**: Shares identical schemas, policies, and engine logic between terminal invocations and Python imports.

---

## What is Planned but Not Yet Implemented

VerifyCraft is developing in sequential outcome-based stages (see [ROADMAP.md](docs/ROADMAP.md)):

- ⏳ **LangGraph Adapter (Stage 3)**: Embedding capabilities directly as graph nodes without making LangGraph a mandatory core dependency.
- ⏳ **GitHub Action (Stage 3)**: Automated pull request review bot running bounded analysis in CI/CD.
- ⏳ **Composable Review & Test Capabilities (Stage 4)**: Independent `review` (candidate finding generation) and `test` (targeted test proposing).
- ⏳ **Containerized Isolation (Stage 4/5)**: True kernel sandbox/container execution boundary for untrusted code execution.
- ⏳ **Multi-Language Support (Stage 5)**: Expanding verification targets beyond Python/pytest.

---

## Supported Environments

- **Python Versions**: Python 3.11 and 3.12.
- **Operating Systems**: Linux (Ubuntu), macOS, and Windows.

---

## Installation

### From PyPI (Standard / Production)

Install VerifyCraft using `pip` or `uv`:

```bash
# Using pip
pip install verifycraft

# Using uv
uv add verifycraft
```

### From Source (Development)

```bash
# Clone repository
git clone https://github.com/apply-the/verifycraft.git
cd verifycraft

# Install dependencies using uv (recommended)
uv sync --extra dev

# Or using pip
pip install -e ".[dev]"
```

---

## Python SDK Quickstart

VerifyCraft provides a typed verification engine that evaluates explicit regression hypotheses against approved checks:

```python
from verifycraft import (
    CheckSpec,
    CheckType,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Hypothesis,
    VerificationEngine,
    VerificationInput,
)

# 1. Define an explicit, testable hypothesis
hypothesis = Hypothesis(
    id="hyp-auth-01",
    claim="Passing None triggers TypeError subscript error",
    failure_mechanism="TypeError: 'NoneType' object is not subscriptable",
    expected_observation=ExpectedObservation(
        expected_outcome="fail",
        expected_exit_codes=[1],
        match_patterns=["TypeError: 'NoneType' object is not subscriptable"],
    ),
)

# 2. Define an approved check specification
check_spec = CheckSpec(
    check_id="chk-auth-pytest",
    check_type=CheckType.PYTEST,
    target_path="tests/test_auth.py",
    args=["-q"],
)

# 3. Assemble verification input
verification_input = VerificationInput(
    repo_path=".",
    revision="HEAD",
    hypothesis=hypothesis,
    check_spec=check_spec,
)

# 4. Configure bounded execution policy (trusted local execution opt-in)
policy = ExecutionPolicy(
    mode=ExecutionMode.TRUSTED_LOCAL,
    timeout_seconds=15.0,
    untrusted_code=False,
)

# 5. Run verification
engine = VerificationEngine()
result = engine.verify(verification_input, policy=policy)

print("Verdict:", result.verdict.value)
if result.evidence:
    print("Evidence exit code:", result.evidence[0].exit_code)
print("JSON Output:\n", result.to_json(indent=2))
```

---

## Standalone CLI Usage

The VerifyCraft CLI mirrors the Python SDK, sharing the exact same engine and typed schemas:

```bash
# Display help and version
verifycraft --version
verifycraft --help

# Verify a test target with a hypothesis in dry-run mode (safe default)
verifycraft verify \
  --repo . \
  --target tests/test_auth.py \
  --claim "Anonymous user causes TypeError" \
  --mode dry_run

# Execute under trusted local policy and output machine-readable JSON
verifycraft verify \
  --repo . \
  --target tests/test_auth.py \
  --claim "Anonymous user causes TypeError" \
  --mode trusted_local \
  --format json
```

### CLI Exit Codes

VerifyCraft uses deterministic exit codes to communicate verification verdicts in CI/CD pipelines:

| Exit Code | Verdict / Status | Description |
| :--- | :--- | :--- |
| `0` | `supported` | Hypothesis is supported by observable evidence and diagnostic signatures. |
| `1` | Error | CLI usage error, invalid input, or unexpected operational failure. |
| `2` | `refuted` | Hypothesis is refuted (e.g., test passed when a failure was claimed). |
| `3` | `inconclusive` | Test failed without matching the expected failure signature / pattern. |
| `4` | `blocked` | Execution was blocked by policy, limits, or missing authorization. |

---

## Verification Security & Limitations

> [!IMPORTANT]
> VerifyCraft enforces strict security boundaries designed to prevent unauthorized code execution and forged verification results:

1. **Fail-Closed Verification Scope**:
   Verification scope resolution enforces a hard limit of **500 relevant repository files**. If a repository or target scope exceeds 500 files, the engine raises a `FingerprintError` and refuses to produce a partial or misleading fingerprint.
2. **Advisory Network Policy**:
   In `trusted_local` mode, setting `allow_network=False` indicates the intended constraint. Because `trusted_local` runs on the local host without kernel namespace isolation, it does not construct an OS firewall boundary. Full containerized isolation is scheduled for Stage 4/5.
3. **Untrusted Code Rejection**:
   Running with `untrusted_code=True` in `trusted_local` mode is blocked by default (`Verdict.BLOCKED` / `PolicyViolationError`). Untrusted code must never execute on a bare host.
4. **Independent Provenance Authentication for Cached Evidence**:
   Caller-supplied execution evidence is **disabled as authoritative by default**. Metadata consistency (matching exit codes, outputs, or timestamps) does not equal provenance. To reuse cached evidence, callers must provide an independent `provenance_authenticator` callback.

---

## How to Run Tests

VerifyCraft has an extensive test suite including unit, contract, integration, and security regression tests:

```bash
# Run pytest with coverage report
uv run pytest --cov=verifycraft

# Or using standard pytest
pytest --cov=verifycraft
```

---

## How to Reproduce Benchmarks

VerifyCraft includes reproducible benchmark comparisons against prompt-only LLM reviewer baselines across 5 canonical scenarios:

```bash
# Run benchmark evaluation with human-readable summary
uv run python -m benchmarks.evaluate

# Emit benchmark results as machine-readable JSON
uv run python -m benchmarks.evaluate --json
```

---

## How Public Releases are Created

Public releases are fully automated and secured via GitHub Actions:
- **Trigger**: Pushing a version tag matching `v*` (e.g. `v0.1.0`).
- **Ancestry Gate**: The tag commit must exist on the `origin/main` branch.
- **Version Gate**: Standard library `tomllib` validates that the tag matches `pyproject.toml` and `verifycraft.__version__`.
- **Verification Gates**: Runs the complete linter (`ruff`), type checker (`mypy`), test suite (`pytest`), and benchmark evaluation before building.
- **Trusted Publishing**: Releases are published to PyPI via OpenID Connect (OIDC) through the `pypi` GitHub Environment. No static API tokens are used.

For full release procedures and configuration instructions, see [RELEASING.md](docs/RELEASING.md).

---

## Documentation

- 📘 **[Architecture & Foundations (`docs/FOUNDATIONS.md`)](docs/FOUNDATIONS.md)**: Architectural principles, contracts, and safety boundaries.
- 🗺️ **[Roadmap & Delivery Sequence (`docs/ROADMAP.md`)](docs/ROADMAP.md)**: Outcome-based milestone sequence (Stages 0–5).
- 🚀 **[Release Guide (`docs/RELEASING.md`)](docs/RELEASING.md)**: Versioning, PyPI Trusted Publishing, and release checklists.
- 🤝 **[Contributing Guidelines (`CONTRIBUTING.md`)](CONTRIBUTING.md)**: Code conventions, branch patterns, and development setup.

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.