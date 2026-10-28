# VerifyCraft Benchmarks & Quality Evaluation

This directory contains two evaluation suites:
1. **Verification Contract Invariants Suite** (`benchmarks.evaluate`): Tests VerifyCraft's core test execution engine contracts against a deterministic keyword-heuristic baseline across 5 controlled Python/pytest scenarios.
2. **Review Quality Evaluation Suite** (`benchmarks.evaluate_review`): Evaluates semantic code review performance across 3 distinct variants and candidate replay on a versioned 16-case Python evaluation dataset.

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
> The `BaselineReviewer` in `benchmarks/baseline.py` is a **deterministic keyword-heuristic baseline**, NOT an LLM reviewer.
> It simulates the vulnerability of naive prompt-only or keyword-matching systems that lack an execution engine:
> - It relies purely on substring pattern matching (e.g. checking whether "error" or "fails" appears in the hypothesis text).
> - It cannot execute tests, so it cannot distinguish actual regressions from refuted claims.
> - It cannot enforce sandboxing or detect stale change fingerprints.
> - It serves to prove that VerifyCraft's verification engine enforces safety and execution contracts where keyword heuristics fail.

Run the verification contract benchmark:

```bash
uv run python -m benchmarks.evaluate
```

---

## 2. Review Quality Evaluation Suite (v0.2.0)

Evaluates requirements-aware code review capability across a 16-case versioned evaluation dataset (`benchmarks/data/review_eval/cases.json`, v1.0.0).

### Execution Modes

The evaluation runner strictly separates two execution modes:

1. **`SIMULATED` Mode (`--mode simulated`, default)**:
   - Hermetic, deterministic, reproducible offline contract validation.
   - Uses `SimulatedGeminiReviewer` to validate pipeline contracts, line bounds, and AST compensating control checks.
   - **Crucial Disclaimer:** These results are synthetic contract tests. They are **NOT empirical Gemini API measurements** and must never be cited as real-world LLM evaluation data. Tokens, latency, and costs are synthetic estimates.
2. **`LIVE` Mode (`--mode live`)**:
   - Executes real Gemini API calls using the official `google-genai` SDK and the configured model (e.g. `gemini-2.5-flash`).
   - Requires `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the execution environment.
   - **Fails Closed:** If credentials are not present, the runner exits with code 1 immediately. It **NEVER silently falls back** to simulated results.
   - Records actual model identity, real token usage (`tokens_used`, `prompt_tokens`, `output_tokens`), and measured wall-clock latency.

### 3-Variant Comparative Methodology

To isolate and explain the specific value of VerifyCraft, the evaluation compares three variants and candidate replay:

| Variant | Input Context | Downstream Pipeline | Purpose |
| :--- | :--- | :--- | :--- |
| **Variant A** | Raw diff + explicit requirements | Raw model completion (no downstream pipeline) | Baseline: Direct diff-only review without surrounding code. |
| **Variant B** | Bounded surrounding context (±25 lines, line numbers, related helpers) + requirements | Raw model completion (no downstream pipeline) | Measures contribution of **Context Enrichment** (A vs B). |
| **Variant C** | Exact same bounded surrounding context as B | Full VerifyCraft pipeline (AST analysis, line grounding, deduplication, requirement verification) | Measures contribution of **VerifyCraft Downstream Validation** (B vs C). |
| **Variant C-Replay** | Exact captured raw candidate findings from Variant B | Full VerifyCraft downstream pipeline | Replays Variant B candidates through VerifyCraft to **isolate pipeline filtering from model nondeterminism**. |

### Dataset Composition and Provenance

The 16 evaluation cases are curated and reconstructed from real-world Python bug patterns and pull requests (10 development cases, 6 held-out cases):

- **Provenance:** Curated synthetic evaluation fixtures modeling realistic bug patterns (logic regressions, missing expiration checks, zero-division guards, pagination slices, breaking parameter additions, multi-file helper calls, and unhandled collection indexing).
- **Integrity Notice:** These cases are curated benchmark fixtures, **not an independent open-source PR dataset**.
- **Generalization Notice:** The simulated reviewer contains scenario-specific branches for all 16 cases (including held-out cases). Therefore, simulated results validate pipeline mechanics, but cannot establish independent empirical generalization. Live validation with a frozen ground truth is required.

### Tracked Metrics

- **Actionable Precision**: Grounded issues matching verified defects / Total issues reported.
- **Defect Recall (Known-Defect Yield)**: Verified defects discovered / Total ground-truth defects.
- **False Positives**: Incorrect defect assertions (reported overall and specifically on clean changes).
- **Invalid Locations**: Fabricated line coordinates or citations of non-existent files.
- **Requirements Accuracy**: Exact match of requirement assessment status against specification.
- **Comment Volume**: Total comments generated across the run.
- **Token Accounting & Cost**: Operational cost and actual (live) or estimated (simulated) token usage.

### Defensible Release Decision Policy

VerifyCraft enforces a 5-stage decision policy rather than emitting an unconditional `GO`:

1. **Credentials Availability:** Valid `GEMINI_API_KEY` or `GOOGLE_API_KEY` in environment.
2. **Provider Execution:** Live API calls complete without network, quota, or authentication failures.
3. **Live Evaluation:** Full evaluation completed in live mode (not simulated).
4. **Objective Quality Thresholds:** $\ge 85\%$ precision, $\ge 90\%$ recall, 0 invalid locations, no regressions vs direct baseline.
5. **Maintainer Manual Approval:** Required human sign-off on Conventional Comments and requirements truthfulness. Even if automated thresholds pass, the benchmark emits `PENDING_MAINTAINER_APPROVAL`, never an autonomous `GO`.

### How to Run

```bash
# 1. Hermetic contract validation (simulated mode, no API key required):
uv run python -m benchmarks.evaluate_review --mode simulated

# 2. Live Gemini evaluation (requires GEMINI_API_KEY or GOOGLE_API_KEY):
export GEMINI_API_KEY="your-gemini-api-key"
uv run python -m benchmarks.evaluate_review --mode live

# 3. Independent held-out split evaluation:
uv run python -m benchmarks.evaluate_review --mode simulated --split held_out

# 4. Machine-readable structured JSON report:
uv run python -m benchmarks.evaluate_review --mode simulated --json
```
