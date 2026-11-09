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

## What VerifyCraft Does Today (`v0.2.0`)

The current release, [`v0.2.0`](https://github.com/apply-the/verifycraft/releases/tag/v0.2.0), provides two independent capabilities that share typed contracts and are available through both the CLI and the Python SDK.

### Requirements-aware semantic code review (new in `v0.2.0`)

- **Diff-Aware Review**: Resolves real base/head Git revisions, collects changed files plus bounded surrounding context, and preserves original source line coordinates end to end.
- **Requirements Assessment**: Parses explicit requirement documents and classifies each requirement as `appears_satisfied`, `partially_satisfied`, `not_satisfied`, `unclear`, or `not_assessed`, distinguishing established violations from specification ambiguity and recognising compensating controls in related files.
- **Conventional Comments + JSON**: Renders [Conventional Comments](https://conventionalcomments.org/), Markdown, and a versioned JSON `ReviewReport`. Comment anchors are validated against the diff; ungrounded locations fall back to file-level comments instead of invented line numbers.
- **Model-Agnostic Providers**: DeepSeek, OpenAI, Anthropic, AWS Bedrock, Gemini, many OpenAI-compatible hosts, and local servers (Ollama, LM Studio, vLLM, llama.cpp). See [docs/providers.md](docs/providers.md).
- **Model-Free Candidate Ingestion**: Host coding agents can supply candidate findings directly; VerifyCraft validates, deduplicates, and renders them without calling a second model.
- **Auditable Review Benchmark**: Versioned evaluation dataset, maximum-cardinality bipartite matching of findings to expected defects, ground-truth adjudications, and offline rescoring of recorded live runs.

### Deterministic hypothesis verification (since `v0.1.0`)

- **Deterministic Hypothesis Verification**: Evaluates structured regression claims against approved test targets (e.g. `pytest`).
- **Fail-Closed Verification Scope**: Resolves code context and fingerprints up to 500 relevant repository files with SHA-256 state tracking, failing closed if limits are exceeded.
- **Categorized Verdicts**: Returns explicit outcome semantics: `supported`, `refuted`, `inconclusive`, or `blocked`.
- **Hardened Provenance Protection**: Disables caller-provided cached execution evidence as authoritative unless verified by an independent provenance authenticator.
- **Strict Execution Policies**: Rejects untrusted code under local execution and enforces timeouts, command restrictions, and advisory limits.
- **Parity Between CLI and SDK**: Shares identical schemas, policies, and engine logic between `verifycraft verify` and `VerificationEngine`.

> [!IMPORTANT]
> Requirement assessments are **review judgements, not proofs**. See [Known Limitations (`v0.2.0`)](#known-limitations-v020) before relying on them without human review.

---

## Roadmap

VerifyCraft is evolving through focused, outcome-driven milestones (see [ROADMAP.md](docs/ROADMAP.md)):

- ✅ **`v0.1.0`: Verification Primitive (Released)**: Caller-supplied hypothesis + approved pytest check → typed evidence and verdict.
- ✅ **`v0.2.0`: Review-First Vertical Slice (Released)**: End-to-end review of diffs and context against optional requirements, producing Conventional Comments and JSON through CLI and SDK, with configured model providers or host-agent candidate ingestion.
- 🎯 **`v0.3.0`: Evidence-Aware Review (Next)**: Structured evidence linking findings to requirements (replacing lexical heuristics), safe targeted verification, check selection, and containerized sandbox isolation.
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
# Core package: verification, model-free candidate review, deterministic provider
uv tool install verifycraft==0.2.0

# With all cloud provider SDKs (OpenAI-compatible, Anthropic, Bedrock, Gemini)
uv tool install "verifycraft[all-providers]==0.2.0"

verifycraft --version
```

Alternatively, run the CLI without installing it permanently:

```bash
uvx verifycraft@0.2.0 --help
```

Provider SDKs are optional extras: `openai` (DeepSeek, OpenAI, and every OpenAI-compatible or local server), `anthropic`, `bedrock`, `gemini`, or `all-providers`.

If the installed command is not found, run `uv tool update-shell` and restart your terminal.

### Python SDK (Project Dependency)

To import VerifyCraft in an existing Python project, run the following from the project directory, where `pyproject.toml` is located:

```bash
uv add verifycraft==0.2.0
# or, with provider SDKs
uv add "verifycraft[all-providers]==0.2.0"
```

Or install it in an activated Python virtual environment using `pip`:

```bash
python -m pip install "verifycraft[all-providers]==0.2.0"
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

VerifyCraft v0.2.0 introduces the **first working, requirements-aware semantic code review engine**, available on PyPI:
It maps Git diffs to relevant surrounding context, evaluates against optional requirements, runs semantic analysis via model providers (e.g. Gemini) or deterministic adapters, checks for compensating controls in surrounding files, and generates actionable [Conventional Comments](https://conventionalcomments.org/) and structured JSON.

### Review CLI & Universal Providers

VerifyCraft is genuinely model-agnostic, supporting major cloud providers (OpenAI, DeepSeek, Kimi, Grok, Anthropic, Bedrock, Gemini), local inference servers (Ollama, LM Studio, vLLM, llama.cpp), and keyless candidate ingestion. See [docs/providers.md](docs/providers.md) for the complete provider matrix and quickstarts.

```bash
# Review with DeepSeek
export DEEPSEEK_API_KEY="your-deepseek-api-key"
verifycraft review . --base main --head HEAD --provider deepseek --model deepseek-chat --format comments

# Review with local Ollama (100% offline, zero cloud API keys required)
verifycraft review . --base main --head HEAD --provider ollama --model llama3.1:latest

# Review with Anthropic Claude
export ANTHROPIC_API_KEY="your-anthropic-api-key"
verifycraft review . --base main --head HEAD --provider anthropic --model claude-3-5-sonnet-20241022

# Review with AWS Bedrock (standard AWS credentials)
verifycraft review . --base main --head HEAD --provider bedrock --model anthropic.claude-3-5-sonnet-20240620-v1:0

# Review with custom OpenAI-compatible endpoint
verifycraft review . --base main --head HEAD --provider openai-compatible --base-url http://127.0.0.1:8000/v1 --model local-model

# Review changes between main and current branch against requirement specs
verifycraft review . \
  --base main \
  --head HEAD \
  --requirements docs/auth.md \
  --provider gemini \
  --format markdown

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
| `--provider` | Review provider: `deepseek`, `openai`, `anthropic`, `bedrock`, `gemini`, `ollama`, `lmstudio`, `vllm`, `llamacpp`, `openai-compatible`, `deterministic`, `none`. | None |
| `--model` | Model name override for LLM provider (e.g. `deepseek-chat`, `gpt-4o`) | Provider default |
| `--base-url` | Custom API base URL for local inference or OpenAI-compatible server | Provider default |
| `--allow-insecure-http` | Allow plain HTTP for non-loopback endpoints (insecure) | Disabled |
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

## Hypothesis Verification Engine (SDK)

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

## Hypothesis Verification CLI

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

# 2. Review Quality Evaluation benchmark (16 cases, variants A/B/C + replay):
# Simulated mode (deterministic contract fixture validation, no API key required):
uv run python -m benchmarks.evaluate_review --mode simulated

# Offline rescoring of the recorded DeepSeek live run (no API calls, no tokens):
uv run python -m benchmarks.evaluate_review \
  --rescore benchmarks/data/review_eval/reports/live_deepseek_evaluation_raw.json

# Live mode (paid provider calls; requires the provider API key):
export DEEPSEEK_API_KEY="your-deepseek-api-key"
uv run python -m benchmarks.evaluate_review --mode live --provider deepseek --model deepseek-chat --split all --json
```

### `v0.2.0` release evidence

The `v0.2.0` release gate was closed by maintainer approval on the offline rescoring of the recorded DeepSeek (`deepseek-chat`) live run over all 16 cases (`dev` + `held_out`). Rescoring measures the **saved** provider responses, not a fresh execution of the reviewer.

| Metric | A: Direct-Diff | B: Direct-Context | C: VerifyCraft |
| :--- | ---: | ---: | ---: |
| Actionable precision | 61.5% | 76.9% | **90.9%** |
| Defect recall (10 expected defects) | 80.0% | 100.0% | **100.0%** |
| False positives | 5 | 3 | 1 |
| Invalid code locations | 0 | 0 | 0 |
| Requirements accuracy (7 requirements) | 57.1% | 71.4% | 57.1% |

Gate status: `PENDING_MAINTAINER_APPROVAL` → approved for release. The evaluation cases are curated synthetic fixtures that model real bug patterns; they are not a sample of production pull requests. See [docs/evaluation/v0.2.0-review-quality.md](docs/evaluation/v0.2.0-review-quality.md).

---

## Known Limitations (`v0.2.0`)

1. **Heuristic requirement-evidence linking.** `ReviewEngine._has_requirement_evidence()` connects a finding to a requirement through constraint-term overlap, generic-vocabulary filtering, and cosmetic/documentation exclusions. It prevents known false positives but is **not a semantic proof of non-compliance**. Requirement assessments still need human review; structured evidence handling is planned for `v0.3.0` instead of further textual exceptions.
2. **Known replay mismatch on `REQ-PERF-01`.** In `dev_05_ambiguous_requirement_timeout`, the Variant B replay returns `appears_satisfied` while the ground truth is `unclear` (the specification lacks timeout acceptance criteria). The mismatch pre-dates the `v0.2.0` classification fixes and the ground truth has intentionally not been altered.
3. **No isolation for untrusted code.** `trusted_local` is not a sandbox; untrusted code is blocked rather than executed.

---

## How Public Releases are Created

Public releases are fully automated and secured via GitHub Actions:
- **Trigger**: Pushing a version tag matching `v*` (e.g. `v0.2.0`).
- **Ref Gate**: The tag must point at the current HEAD of `origin/main`.
- **Version Gate**: Standard library `tomllib` validates that the tag matches `pyproject.toml` and `verifycraft.__version__`.
- **Verification Gates**: Runs the complete linter (`ruff`), type checker (`mypy`), test suite (`pytest`), and benchmark evaluation before building.
- **Trusted Publishing**: Releases are published to PyPI via OpenID Connect (OIDC) through the `pypi` GitHub Environment. No static API tokens are used.

For full release procedures and configuration instructions, see [RELEASING.md](docs/RELEASING.md).

---

## Documentation

- 📘 **[Architecture & Foundations (`docs/FOUNDATIONS.md`)](docs/FOUNDATIONS.md)**: Architectural principles, contracts, and safety boundaries.
- 🗺️ **[Roadmap & Delivery Sequence (`docs/ROADMAP.md`)](docs/ROADMAP.md)**: Outcome-based milestone sequence (`v0.1.0` to `v0.5.0`).
- 🔌 **[Provider Matrix (`docs/providers.md`)](docs/providers.md)**: Supported cloud, enterprise, local, and model-free review providers.
- 📊 **[Review Quality Evaluation (`docs/evaluation/v0.2.0-review-quality.md`)](docs/evaluation/v0.2.0-review-quality.md)**: Benchmark methodology, release gate, and `v0.2.0` results.
- 📝 **[Release Notes (`docs/releases/`)](docs/releases/)**: [`v0.2.0`](docs/releases/v0.2.0-release-notes.md), [`v0.1.0`](docs/releases/v0.1.0-release-notes.md).
- 🚀 **[Release Guide (`docs/RELEASING.md`)](docs/RELEASING.md)**: Versioning, PyPI Trusted Publishing, and release checklists.
- 🤝 **[Contributing Guidelines (`CONTRIBUTING.md`)](CONTRIBUTING.md)**: Code conventions, branch patterns, and development setup.

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.