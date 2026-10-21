# VerifyCraft

> **Composable review, testing, and verification capabilities for AI agents.**

[![Version](https://img.shields.io/github/v/release/apply-the/verifycraft?color=blue&label=version)](https://github.com/apply-the/verifycraft/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
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

## Architecture at a Glance

![VerifyCraft Architecture](docs/assets/architecture.svg)

At the center is **one capability engine**, exposed through four thin, predictable interfaces:

| Interface | Who uses it | Responsibility |
| :--- | :--- | :--- |
| **CLI** | Developers and CI/CD | Run capabilities locally or in pipelines; emit human-readable summaries and machine-readable JSON. |
| **Python SDK** | Custom AI agents and Python apps | Typed programmatic API with dependency injection. |
| **LangGraph Adapter** | Agent workflows | Turn capabilities into graph nodes without making LangGraph a mandatory core dependency. |
| **GitHub Action** | Automated PR review | Run bounded analysis on pull requests and publish evidence-backed findings. |

---

## Core Principles

1. **Model-agnostic:** No single provider or model is the source of truth. LLMs are injectable dependencies; deterministic evidence checks are decoupled from model judgment.
2. **Typed contracts:** Public schemas for inputs, hypotheses, findings, evidence, verdicts, failures, and resource usage.
3. **Bounded execution:** Configurable caps on time, attempts, model calls, tokens/cost, test scope, and tool permissions.
4. **Verifiable results:** A model's confidence or consensus is not proof. Findings link directly to test output, static analysis, repository locations, and reproducible commands.
5. **Composition without lock-in:** Individual capabilities can be called directly, used as standard Python functions, or wired into third-party workflows without a mandatory platform runtime.
6. **Small public surface:** Focused, predictable interfaces. Integrations remain thin and optional.
7. **Safe by default:** Repository inspection is read-only by default. Running commands requires explicit execution policies and environment isolation.
8. **Measurable quality:** New reasoning steps must justify themselves against simple baselines in finding quality, reliability, cost, and latency.

---

## Documentation

Comprehensive design specifications and project planning are available in the [`docs/`](docs/) directory:

- 📘 **[Architecture & Foundations (`docs/FOUNDATIONS.md`)](docs/FOUNDATIONS.md)**
  - Mission and non-negotiable acceptance criteria
  - In-scope vs. out-of-scope boundaries
  - Core abstractions and evidence verdict semantics (`supported`, `refuted`, `inconclusive`, `blocked`)
  - Execution and security policies
  - Target repository layout

- 🗺️ **[Roadmap & Delivery Sequence (`docs/ROADMAP.md`)](docs/ROADMAP.md)**
  - Outcome-based milestone sequence (Stages 0 through 5)
  - Golden dataset and benchmark definitions
  - Risk register and mitigation strategies
  - Initial development backlog

---

---

## Quickstart

### Installation

VerifyCraft requires Python 3.11+.

```bash
# Clone the repository
git clone https://github.com/apply-the/verifycraft.git
cd verifycraft

# Install in development mode with uv or pip
uv sync --extra dev
# or
pip install -e ".[dev]"
```

### Python SDK Quickstart

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
print("Evidence exit code:", result.evidence[0].exit_code)
print("JSON Output:\n", result.to_json(indent=2))
```

### CLI Quickstart

VerifyCraft includes a thin CLI that shares the exact same verification engine and typed schemas:

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

#### CLI Exit Codes

VerifyCraft uses deterministic exit codes to communicate verification verdicts in automated pipelines:

| Exit Code | Verdict / Status | Description |
| :--- | :--- | :--- |
| `0` | `supported` | Hypothesis is supported by observable evidence and diagnostic signatures. |
| `1` | Error | CLI usage error, invalid input, or unexpected operational failure. |
| `2` | `refuted` | Hypothesis is refuted (e.g., test passed when a failure was claimed). |
| `3` | `inconclusive` | Test failed without matching the expected failure signature / pattern. |
| `4` | `blocked` | Execution was blocked by policy, limits, or missing authorization. |

> [!NOTE]
> **Advisory Network Policy**: Setting `allow_network=False` in `trusted_local` mode indicates the intended execution constraint. In trusted local mode, it does not construct an OS kernel sandbox or firewall boundary. Full containerized isolation is planned for future stages.

### Benchmark Suite

VerifyCraft includes a reproducible benchmark comparing evidence-based verification against a prompt-only LLM reviewer baseline across 5 canonical scenarios:

```bash
# Run benchmark with summary output
python -m benchmarks.evaluate

# Emit benchmark results as JSON
python -m benchmarks.evaluate --json
```

---

## Proposed Project Layout

```text
verifycraft/
├── src/verifycraft/
│   ├── contracts/               # Typed, versioned public schemas
│   ├── core/                    # Execution limits, evidence, provenance, policies
│   ├── capabilities/
│   │   ├── verify/             # Initial verification capability
│   │   ├── review/             # Candidate-finding generation
│   │   └── test/               # Targeted test proposal and execution
│   ├── providers/               # Minimal provider protocols
│   └── cli/                     # Direct CLI, same SDK behavior
├── integrations/
│   ├── langgraph/               # Optional graph adapter
│   └── github-action/           # Optional automation wrapper
├── tests/
│   ├── unit/
│   ├── contract/
│   └── integration/
├── benchmarks/                  # Reproducible comparisons against baselines
├── examples/                    # Standalone and embedded usage
├── docs/                        # Architecture foundations, roadmap, and assets
│   ├── FOUNDATIONS.md
│   ├── ROADMAP.md
│   └── assets/
└── pyproject.toml
```

---

## Current Status

VerifyCraft is currently in **Stage 0 / Stage 1** of its [roadmap](docs/ROADMAP.md): establishing golden benchmark datasets, baseline evaluation protocols, and the initial Python/pytest verification vertical slice.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.