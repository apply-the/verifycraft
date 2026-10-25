# VerifyCraft Benchmarks & Quality Evaluation

This directory contains two evaluation suites:
1. **Verification Contract Invariants Suite** (`benchmarks.evaluate`): Tests VerifyCraft's core test execution engine contracts against a deterministic keyword heuristic across 5 controlled Python/pytest scenarios.
2. **Review Quality Evaluation Suite** (`benchmarks.evaluate_review`): Evaluates semantic code review performance (Direct Gemini vs VerifyCraft) across a 16-case versioned evaluation dataset.

---

## 1. Verification Contract Invariants Suite

Evaluates engine contracts and safety boundaries across 5 controlled fixtures:

| Case ID | Scenario | Expected Behavior | Ground Truth Verdict |
| :--- | :--- | :--- | :--- |
| `case_01_real_regression` | A PR diff introduces a `TypeError` subscript error in `auth.py`. | Targeted pytest fails with the expected `TypeError: 'NoneType' object is not subscriptable` error. | **`supported`** |
| `case_02_contradicted_hypothesis` | A reviewer hypothesized that discount calculation fails on float inputs. | Targeted pytest passes cleanly (exit code 0), contradicting the claimed regression. | **`refuted`** |
| `case_03_inconclusive_signature` | A hypothesis predicts a `TimeoutError`, but check fails due to `ModuleNotFoundError`. | Check exit code indicates failure, but diagnostic signature does not match expectation. | **`inconclusive`** |
| `case_04_blocked_untrusted_code` | Analysis requested on an untrusted public PR without container isolation. | Execution safety policy blocks execution to prevent uncontained code execution. | **`blocked`** |
| `case_05_stale_evidence` | Cached evidence from an older revision/diff is presented for a new revision. | SHA-256 change fingerprint mismatch is detected; stale evidence is rejected. | **`blocked`** |

### Baseline Comparison: Keyword-Heuristic Baseline (Not a Real LLM)

> [!IMPORTANT]
> The `BaselineReviewer` in `benchmarks/baseline.py` is a **deterministic keyword-heuristic baseline**, NOT a real LLM reviewer.
> It simulates the vulnerability of prompt-only/heuristic systems that lack an execution engine:
> - It relies purely on text matching (e.g. checking if "error" or "fails" appears in the claim text).
> - It cannot execute tests, so it cannot distinguish actual regressions from refuted claims.
> - It cannot enforce sandboxing or detect stale change fingerprints.
> - It serves to test that VerifyCraft's verification engine enforces safety and execution contracts where keyword heuristics fail.

Run the verification contract benchmark:

```bash
uv run python -m benchmarks.evaluate
```

---

## 2. Review Quality Evaluation Suite (v0.2.0)

Evaluates requirements-aware code review capability across 16 realistic, human-curated Python code changes (10 development cases, 6 independent held-out cases).

### Evaluated Dimensions:
- **Direct Gemini Review**: Raw model review without VerifyCraft's grounding and validation pipeline.
- **VerifyCraft Review**: Full VerifyCraft review with surrounding context enrichment, line anchor validation, AST compensating control checks, grounding tags, and safe requirement assessment fallbacks.

### Tracked Metrics:
- **Precision**: Actionable findings matching verified defects / Total issues reported.
- **Recall (Known-Defect Yield)**: Verified defects discovered / Total ground-truth defects.
- **False Positives**: Incorrect defect assertions prevented by AST compensating controls.
- **Invalid Locations**: Fabricated line coordinates or untouched file citations eliminated.
- **Requirements Accuracy**: Exact requirement status match against specification.
- **Token Usage & API Cost**: Operational cost per review.

Run the review quality evaluation:

```bash
# Offline evaluation (hermetic, reproducible simulated reviewer):
uv run python -m benchmarks.evaluate_review

# Live Gemini review evaluation (requires GEMINI_API_KEY or GOOGLE_API_KEY):
uv run python -m benchmarks.evaluate_review --live

# Independent held-out split evaluation:
uv run python -m benchmarks.evaluate_review --split held_out

# Output JSON report:
uv run python -m benchmarks.evaluate_review --json
```
