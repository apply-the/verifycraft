# VerifyCraft Benchmarks (Stage 0 / Stage 1)

This directory contains the reproducible benchmark suite evaluating VerifyCraft's verification capability against a prompt-based LLM reviewer baseline across canonical Python/pytest scenarios.

## Benchmark Fixtures & Scenarios

The benchmark consists of 5 controlled fixtures with explicit ground-truth verdicts:

| Case ID | Scenario | Expected Behavior | Ground Truth Verdict |
| :--- | :--- | :--- | :--- |
| `case_01_real_regression` | A PR diff introduces a `TypeError` subscript error in `auth.py`. | Targeted pytest fails with the expected `TypeError: 'NoneType' object is not subscriptable` error. | **`supported`** |
| `case_02_contradicted_hypothesis` | A reviewer hypothesized that discount calculation fails on float inputs. | Targeted pytest passes cleanly (exit code 0), contradicting the claimed regression. | **`refuted`** |
| `case_03_inconclusive_signature` | A hypothesis predicts a `TimeoutError`, but check fails due to `ModuleNotFoundError`. | Check exit code indicates failure, but diagnostic signature does not match expectation. | **`inconclusive`** |
| `case_04_blocked_untrusted_code` | Analysis requested on an untrusted public PR without container isolation. | Execution safety policy blocks execution to prevent uncontained code execution. | **`blocked`** |
| `case_05_stale_evidence` | Cached evidence from an older revision/diff is presented for a new revision. | SHA-256 change fingerprint mismatch is detected; stale evidence is rejected. | **`blocked`** |

## Baseline Comparison: Prompt-Only LLM Reviewer

The `BaselineReviewer` models the behavior of an ungrounded LLM reviewing code changes without an execution engine:
- It relies purely on text heuristics and claim phrasing.
- Because it cannot execute pytest, it suffers from confirmation bias (assuming plausible-sounding claims are true), producing false positives.
- It cannot detect when tests actually pass and refute a claim.
- It cannot enforce execution sandboxing policies or container requirements.
- It cannot detect stale evidence fingerprints.

## Running the Benchmark

Run the benchmark evaluation runner with human-readable summary output:

```bash
python -m benchmarks.evaluate
```

Or with `uv`:

```bash
uv run python -m benchmarks.evaluate
```

To emit machine-readable JSON results:

```bash
python -m benchmarks.evaluate --json
```

## Metrics and Scope

1. **Contract Invariant Adherence**: Verifies that the engine deterministically satisfies each invariant:
   - Defect confirmation requires an observable diagnostic signature (not just an exit code 1).
   - Passing tests refute claimed regression failures.
   - Unmatched diagnostic signatures yield inconclusive findings.
   - Untrusted code is intercepted and blocked without container isolation.
   - Outdated/mismatched evidence is rejected as stale.
2. **False Positive Prevention**: Demonstrates that without test execution and diagnostic matching, prompt-only reviewers hallucinate regressions on plausible claims.
3. **Execution Latency**: Wall-clock time required to enforce policies, execute checks, and evaluate evidence.

> [!NOTE]
> These 5 canonical fixtures test deterministic engine contracts and safety invariants. They are not an empirical benchmark of LLM hypothesis generation across arbitrary open-source repositories (which is planned for later roadmap stages).
