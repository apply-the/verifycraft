# VerifyCraft

> **Evidence-aware code review engine evaluating changes against context and explicit requirements to produce actionable Conventional Comments.**

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

VerifyCraft is a Python-first, model-agnostic code review engine designed to evaluate code changes, context, and explicit requirements, producing actionable [Conventional Comments](https://conventionalcomments.org/) and versioned machine-readable reports.

The existing verification engine remains an independent, first-class capability for validating testable hypotheses against empirical test evidence. However, test execution is an evidence source rather than the definition of the entire product. VerifyCraft works **standalone** (via CLI and Python SDK) and can be **embedded** into host coding agents (e.g., Copilot, Claude Code) or CI/CD pipelines without requiring a new agent framework or forcing a secondary mandatory LLM.

---

## What VerifyCraft Does Today (`v0.1.0`)

In version `0.1.0`, VerifyCraft delivers the foundational, independent **deterministic verification engine primitive**:

- **Deterministic Hypothesis Verification**: Evaluates structured regression claims against approved test targets (e.g. `pytest`).
- **Fail-Closed Verification Scope**: Resolves code context and fingerprints up to 500 relevant repository files with SHA-256 state tracking, failing closed if limits are exceeded.
- **Root-Level Module Tracking**: Tracks root-level Python modules and configurations (`*.py`, `pyproject.toml`, `setup.cfg`) alongside `src/` and `tests/` directories.
- **Categorized Verdicts**: Returns explicit outcome semantics: `supported`, `refuted`, `inconclusive`, or `blocked`.
- **Hardened Provenance Protection**: Disables caller-provided cached execution evidence as authoritative unless verified by an independent provenance authenticator.
- **Strict Execution Policies**: Rejects untrusted code under local execution and enforces timeouts, command restrictions, and advisory limits.
- **Parity Between CLI and SDK**: Shares identical schemas, policies, and engine logic between terminal invocations (`verifycraft verify`) and Python imports (`VerificationEngine`).

> [!NOTE]
> Version `0.1.0` implements the verification engine primitive. It **does not** yet include autonomous git diff review, requirements document parsing, Conventional Comments generation, or LLM integrations. Those capabilities are the primary focus of the `v0.2.0` milestone.

---

## Planned Roadmap

VerifyCraft is evolving through focused, outcome-driven milestones (see [ROADMAP.md](docs/ROADMAP.md)):

- 🎯 **`v0.2.0`: Review-First Vertical Slice (Next Milestone)**: End-to-end review of diffs and context against optional requirements, producing actionable Conventional Comments and JSON through CLI and Python SDK; supports both configured model providers and host-agent candidate finding ingestion without a mandatory second model.
- ⏳ **`v0.3.0`: Evidence-Aware Review**: Link review findings to safe targeted verification, check selection, and containerized sandbox isolation.
- ⏳ **`v0.4.0`: Coding-Agent Distribution**: Claude Code/Copilot-friendly tool surface and candidate ingestion without requiring a second mandatory LLM.
- ⏳ **`v0.5.0`: PR Automation & Ecosystem**: GitHub Action review bot, optional LangGraph adapter, and multi-language check backends.

---

## Supported Environments

- **Python Versions**: Python 3.11 and 3.12.
- **Operating Systems**: Linux (Ubuntu), macOS, and Windows.

---

## Installation

### CLI (Recommended)

Install VerifyCraft from [PyPI](https://pypi.org/project/verifycraft/) as a standalone command-line tool. No Python project or `pyproject.toml` is required:

```bash
uv tool install verifycraft==0.1.0
verifycraft --version
```

Alternatively, run the CLI without installing it permanently:

```bash
uvx verifycraft@0.1.0 --help
```

If the installed command is not found, run `uv tool update-shell` and restart your terminal.

### Python SDK (Project Dependency)

To import VerifyCraft in an existing Python project, run the following from the project directory, where `pyproject.toml` is located:

```bash
uv add verifycraft==0.1.0
```

Or install it in an activated Python virtual environment using `pip`:

```bash
python -m pip install verifycraft==0.1.0
```

`uv add` manages project dependencies and requires a `pyproject.toml`; it is not the command for installing a standalone CLI from your home directory.

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

## Semantic Code Review (CLI & SDK)

> [!NOTE]
> The Semantic Code Review engine (`verifycraft review`, `ReviewEngine`, and provider adapters) is the core feature set of the upcoming **v0.2.0** milestone currently implemented on `main` and undergoing empirical validation. The published release on PyPI remains `v0.1.0` (verification engine primitive).

VerifyCraft v0.2.0 introduces the **first working, requirements-aware semantic code review engine**:
It maps Git diffs to relevant surrounding context, evaluates against optional requirements, runs semantic analysis via model providers (e.g. Gemini) or deterministic adapters, checks for compensating controls in surrounding files, and generates actionable [Conventional Comments](https://conventionalcomments.org/) and structured JSON.

### Review CLI

Review a Git diff against optional requirements:

```bash
# Set Gemini API key for live LLM review
export GEMINI_API_KEY="your-gemini-api-key"

# Review changes between main and current branch against requirement specs
verifycraft review . \
  --base main \
  --head HEAD \
  --requirements docs/auth.md \
  --provider gemini \
  --format markdown

# Review without requirements and emit Conventional Comments
verifycraft review . \
  --base main \
  --head feature/auth \
  --provider gemini \
  --format comments

# Emit structured machine-readable JSON report
verifycraft review . \
  --base origin/main \
  --head HEAD \
  --provider gemini \
  --format json

# Run offline deterministic test review (no API key required)
verifycraft review . \
  --base main \
  --head HEAD \
  --provider deterministic \
  --format comments
```

#### Review CLI Arguments

| Argument | Description | Default |
| :--- | :--- | :--- |
| `path` | Path to git repository (positional) | `.` |
| `--base` | Base git revision, branch, or commit | `main` |
| `--head` | Head git revision, branch, or commit | `HEAD` |
| `--requirements` | Path to requirement document or text (repeatable) | None |
| `--candidates` | Path to JSON file containing candidate findings for model-free review | None |
| `--provider` | Review provider adapter (`gemini`, `deterministic`, `none`). Explicit provider or `--candidates` required | None |
| `--model` | Model name override for LLM provider (e.g. `gemini-2.5-pro`) | `gemini-2.5-flash` |
| `--format` | Output format: `markdown`, `json`, `comments` | `markdown` |
| `--strict` | Exit with code 2 if any blocking issues are reported | Disabled |
| `--timeout` | Review timeout in seconds | `60.0` |

### Model-Free Candidate Ingestion

Host coding agents can supply structured candidate findings directly without requiring a secondary LLM:

```bash
# Ingest candidate findings from host agent and run full review pipeline
verifycraft review . \
  --base main \
  --head HEAD \
  --candidates findings.json \
  --format comments
```

### Review Python SDK Quickstart

The Python SDK exposes `ReviewEngine`, sharing the exact same pipeline and contracts:

```python
from verifycraft import (
    ReviewEngine,
    ReviewPolicy,
    ReviewRequest,
)

# 1. Configure the review request
request = ReviewRequest(
    repo_path=".",
    base_revision="main",
    head_revision="HEAD",
    requirements=["docs/auth.md"],  # Optional
    provider="gemini",  # or "deterministic", "none"
    model="gemini-2.5-pro",
    policy=ReviewPolicy(timeout_seconds=60.0),
)

# 2. Execute semantic review
engine = ReviewEngine()
report = engine.review(request)

# 3. Access structured results
print(f"Summary: {report.summary}")
print(f"Total Findings: {len(report.findings)}")
for req in report.requirement_assessments:
    print(f"[{req.requirement_id}] {req.status.value}: {req.explanation}")

# 4. Output Conventional Comments (e.g. for PR comments)
print("\n--- Conventional Comments ---")
print(report.to_conventional_comments())

# 5. Or output full Markdown / JSON
print("\n--- Markdown Report ---")
print(report.to_markdown())
```

### Conventional Comments Standard

VerifyCraft strictly follows the [Conventional Comments](https://conventionalcomments.org/) specification:

```text
<label> [(blocking|non-blocking)]: <subject>

<discussion / rationale / impact / suggested fix>
```

- **Canonical Labels**: `issue`, `suggestion`, `nitpick`, `praise`, `todo`, `question`, `thought`.
- **Explicit Blocking Decorations**: Decoupled from severity (e.g., `issue (blocking): ...` vs `suggestion (non-blocking): ...`).
- **Grounded Anchors**: Only valid changed lines within diff hunks are anchored inline. Unchanged lines or ungrounded locations are safely downgraded to file-level comments rather than fabricating line numbers.

---

## Hypothesis Verification Engine (v0.1.0 SDK)

VerifyCraft also provides a typed verification engine that evaluates explicit regression hypotheses against approved checks:

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

## Hypothesis Verification CLI (v0.1.0 CLI)

The `verifycraft verify` CLI mirrors the verification SDK, sharing the exact same engine and typed schemas:

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
   In `trusted_local` mode, setting `allow_network=False` indicates the intended constraint. Because `trusted_local` runs on the local host without kernel namespace isolation, it does not construct an OS firewall boundary. Full containerized isolation is scheduled for future container sandboxing (`v0.3.0`).
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

VerifyCraft includes reproducible benchmark suites for both the verification engine contracts and the semantic code review engine:

```bash
# 1. Run Verification Contract Invariants benchmark (5 canonical scenarios against keyword heuristic):
uv run python -m benchmarks.evaluate
uv run python -m benchmarks.evaluate --json

# 2. Run Review Quality Evaluation benchmark (3-variant evaluation across 16 cases):
# Simulated mode (deterministic contract fixture validation, no API key required):
uv run python -m benchmarks.evaluate_review --mode simulated

# Live mode (calls Gemini API; requires GEMINI_API_KEY):
export GEMINI_API_KEY="your-gemini-api-key"
uv run python -m benchmarks.evaluate_review --mode live
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
- 🗺️ **[Roadmap & Delivery Sequence (`docs/ROADMAP.md`)](docs/ROADMAP.md)**: Outcome-based milestone sequence (`v0.1.0` to `v0.5.0`).
- 🚀 **[Release Guide (`docs/RELEASING.md`)](docs/RELEASING.md)**: Versioning, PyPI Trusted Publishing, and release checklists.
- 🤝 **[Contributing Guidelines (`CONTRIBUTING.md`)](CONTRIBUTING.md)**: Code conventions, branch patterns, and development setup.

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.