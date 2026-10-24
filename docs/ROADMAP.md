# VerifyCraft: Roadmap

> **Repository:** [`apply-the/verifycraft`](https://github.com/apply-the/verifycraft)  
> **Status:** Proposed review-first roadmap following the published `v0.1.0`  
> **Strategy:** Deliver a useful requirements-aware code review before investing in broad integrations.  
> **Release policy:** Milestones are outcome gates, not committed dates; versions after `v0.1.0` are **proposed targets**.

## 1. North star: actionable, requirements-aware code review

VerifyCraft should answer: **“What is wrong with this implementation, which requirements are not met, and what has actually been verified?”**

The product receives a Git diff or explicit revision pair, optional requirement documents and review policy, and either an explicitly configured model provider **or** candidate findings supplied by an existing coding agent. It reviews implementation and context, evaluates requirement coverage, validates and prioritizes findings, optionally gathers evidence, and returns **Conventional Comments**, an actionable summary, and **versioned JSON**.

**Core promise:** **Model-agnostic · Typed contracts · Bounded execution · Verifiable results**.

![VerifyCraft distribution surfaces](assets/architecture.svg)

The existing diagram illustrates the CLI, Python SDK, LangGraph and GitHub Action surfaces. These remain relevant, but the **product center is code review**, supported by the verification primitive. Update the graphic later to reflect that internal distinction while retaining its icon language.

## 2. Baseline: what `v0.1.0` shipped

**Released:** [`v0.1.0`](https://github.com/apply-the/verifycraft/releases/tag/v0.1.0).

Implemented: Python package, `VerificationEngine.verify`, `verifycraft verify`, explicit hypotheses/checks, a trusted-local pytest runner, bounded fingerprinting, policy checks, typed evidence and the four verification verdicts (`supported`, `refuted`, `inconclusive`, `blocked`). It is useful as a programmatically callable verification primitive.

**Not shipped:** end-to-end semantic code review, branch comparison, candidate defect discovery, requirements mapping, Conventional Comments, real-agent integrations, a GitHub PR-review Action, production isolation for untrusted code, or a benchmark proving code-review superiority.

**Benchmark clarification:** The five canonical fixtures validate verification-contract invariants. The purported LLM baseline is a keyword heuristic, **not a real model call**. Do not use that comparison as a code-review quality claim.

**Product correction:** `v0.1.0` should not be retroactively called a complete code-review milestone. Keep the release unchanged; fix forward through documentation and new functionality.

## 3. Revised delivery sequence

| Milestone | Outcome | Status | Exit gate |
| --- | --- | --- | --- |
| **`v0.1.0`: Verification primitive** | Caller-supplied hypothesis + pytest check -> typed evidence/verdict, CLI and SDK | **Released** | Published artifact and deterministic contract tests. Does not establish review value. |
| **`v0.2.0`: Review-first vertical slice** | Diff + context + optional requirements -> useful findings, Conventional Comments, JSON through CLI and SDK | **Next, proposed** | End-to-end review works on known real changes, with correctly located comments and honest evidence/uncertainty. |
| **`v0.3.0`: Evidence-aware review** | Link review findings to safe targeted verification and reliable check selection | **Proposed** | Clear, reproducible association between reviewed claims and trusted source/static/test evidence. |
| **`v0.4.0`: Coding-agent distribution** | Claude Code/Copilot-friendly tool surface, optionally MCP | **Proposed** | Existing agent can review and verify without a second mandatory LLM, through the same SDK contracts. |
| **`v0.5.0`: PR automation and ecosystem** | GitHub Action, optional LangGraph adapter, stronger empirical benchmark | **Proposed** | Safe, actionable PR review with stable contracts and measured usefulness. |

Do not advance a milestone merely because code exists. Failure to achieve useful results is a reason to narrow scope, revise the design, or stop.

## 4. `v0.2.0`: Build the first actual review product

### User story

A developer requests a review of `main...feature/auth`, supplies an auth specification (optionally), and gets a concise review containing code-specific issues, requirements coverage, questions, and actionable Conventional Comments. A second user can call the same capability programmatically, supplying either a model provider or candidates from a coding agent.

Proposed CLI, **not implemented in `v0.1.0`**:

```bash
verifycraft review . \
  --base main \
  --head feature/auth \
  --requirements docs/auth-requirements.md \
  --format markdown
```

Proposed SDK shape, **not implemented in `v0.1.0`**:

```python
review = ReviewEngine(provider=my_provider).review(
    repository=".",
    base_ref="main",
    head_ref="feature/auth",
    requirements=["docs/auth-requirements.md"],
)
print(review.to_conventional_comments())
```

### Workstream A: Change understanding and bounded context

- Resolve actual base/head Git SHAs and obtain diff plus changed-line maps. Support path filters, renamed files, deletions and a bounded context budget. A supplied patch may be supported as an alternative input with appropriately weaker provenance.
- Read surrounding code, relevant interfaces/call sites and tests; record sources, exclusions, truncated data and line locations.
- Choose a small initial target such as **Python code changes** for high-quality review; keep abstractions free of a permanent Python-only assumption. A semantic review can inspect other languages later, but must declare capability limitations.
- Preserve diff-to-file coordinates for Conventional Comments; invalid anchors should fall back to a summary-level comment, never an invented line.

**Acceptance:** A change with multiple files and at least one rename has correct base/head identities, scoped context and validated comment locations; exceeding limits is visible rather than silently ignored.

### Workstream B: Requirements-aware review

- Accept explicit sources: Markdown/text specifications, acceptance criteria, architecture decisions, issue or PR descriptions. Requirements from external systems are supplied as content/links in this milestone, not fetched via broad OAuth integrations.
- Extract traceable requirement statements and preserve IDs/locators and original text. Separate hard requirements from commentary and examples. Flag ambiguous/conflicting sources.
- Produce a requirement-by-requirement assessment with `appears_satisfied`, `partially_satisfied`, `not_satisfied`, `unclear`, and `not_assessed`, each with justification and relevant code references.
- Treat missing requirements as **not supplied**, not as a review failure or a reason to invent requirements.

**Acceptance:** On a fixture where the spec requires token expiration and single use, the review identifies a genuinely missing invariant with an exact requirement citation; in another fixture where the invariant is enforced in a different file, the reviewer finds the compensating control instead of falsely claiming it is absent.

### Workstream C: Semantic code review

- Implement a **small provider interface** for one real configured LLM provider, plus a deterministic fake for tests. The core must not import a provider SDK at package-import time; provider configuration must be explicit, including token/cost budgets and data-handling disclosure.
- Accept structured externally proposed findings from a coding agent, with the **same downstream normalization, deduplication, validation, and rendering**. Do not require a second model for this mode.
- Review logic, error paths, security-relevant behavior, API contracts, regression risk, edge cases and test coverage alongside requirements.
- Prefer changed lines and their consequences; trace surrounding or downstream code when relevant. Check counterexamples and compensating code before promoting an assertion to an `issue`.
- Separate finding discovery from claim verification. `not_verified` is not the same as `blocked` or `inconclusive`.

**Acceptance:** Known-bug fixtures yield specific actionable comments, while clean or ambiguous fixtures do not force an issue. No deterministic code test is claimed to establish semantic correctness by itself.

### Workstream D: Conventional Comments, typed report and CLI/SDK parity

- Implement the [Conventional Comments](https://conventionalcomments.org/) grammar: `<label> [decorations]: <subject>`, followed by optional discussion. Canonical standard labels are `praise`, `nitpick`, `suggestion`, `issue`, `todo`, `question`, and `thought`. Custom or team extensions (such as `chore` and `note`) may be supported as optional configurations. Decorations like `(blocking)` and `(non-blocking)` are explicit and sparse.
- Define a stable versioned `ReviewRequest`, `ReviewFinding`, `RequirementAssessment` and `ReviewReport` schema. Record path, line/range, source citations, severity, blocking recommendation, evidence type/status, suggested action and deduplication key. Keep review assessment separate from existing `VerificationResult` verdict semantics.
- Produce both Markdown/Conventional Comments and JSON. CLI and SDK must share review logic; neither independently interprets model output.
- Emit a meaningful summary for **zero findings** with assessed scope and omissions. A run without a configured provider and without agent-supplied candidates must fail clearly or run explicitly labeled deterministic-only checks; it cannot pretend to perform semantic code review.

**Acceptance:** The exact same typed review input yields semantically equivalent SDK and CLI reports. Conventional Comments parse correctly, use valid anchors, and include requirements coverage when supplied.

### Workstream E: Quality and first evidence of product value

- Curate a small **versioned evaluation set** (initially roughly 10–20 representative PR/change fixtures, including known defects, negative cases, partial requirements, ambiguous requirements and compensating logic). Freeze an independent held-out subset.
- Compare at least one **real model review** with the same model and context operating through VerifyCraft. Include a credible Git/test-assisted agent baseline when practical, rather than treating the current keyword heuristic as an LLM.
- Track precision of actionable findings, known-defect yield, coverage of explicit requirements, false positives, unnecessary comments, unverified-rate, human effort to run/review, latency, tokens and cost. Report results and limitations, including cases where there is no improvement.
- Run contract, integration, lint, type, packaging and security regression checks. Preserve compatibility with `v0.1.0` verification APIs as far as possible.

**Release gate:** A maintainer can install a candidate from a clean environment, review a representative diff end-to-end with optional requirements, obtain useful Conventional Comments and JSON, and explain each finding's source and evidence limitations. There must be positive demonstrated utility versus a bare prompt/reviewer baseline; a test suite alone is not sufficient.

### Explicitly not in `v0.2.0`

- GitHub PR comment posting or automatic review approvals; containerized execution of untrusted repositories; generic autonomous test generation; a multi-agent reviewer council; broad provider matrix; IDE extension; hosted server or billing; mandatory MCP, LangGraph or third-party agent framework.
- Do not build a provider-independent "autonomous reviewer" that secretly requires the user to discover every candidate manually. Both programmatic autonomous review **with a configured provider** and agent-supplied review candidates must be coherent use cases.

## 5. `v0.3.0`: Evidence-aware review and targeted checking

Move beyond source-only findings without turning the project into a test runner:

- Map individual review findings to explicit testable observations, static checks, or other approved evidence paths, with `not_verified` when no safe check exists.
- Support check selection, rather than forcing callers to know the exact pytest file and expected output. Never execute an arbitrary model-generated shell string.
- Resolve project execution environments explicitly (including project virtual environments). Address the isolated-interpreter mismatch from global `uv tool install` when selecting a trusted test backend.
- Provide a secure isolation implementation **before** running untrusted PR code, or return `blocked` clearly when unavailable. Trusted local mode is not isolation.
- Optionally compare behavior at base/head for the same check when environments and test identity are genuinely comparable; distinguish observed behavioral regression from inferred root cause.
- Reuse `v0.1.0` `VerificationEngine` where it genuinely fits, extending types compatibly and attaching evidence IDs to review findings.

**Exit gate:** On a held-out benchmark, executable evidence changes the status of correct and incorrect review hypotheses as expected, without fabricated confirmation or unsafe execution. Publish incremental quality/cost effects versus review alone.

## 6. `v0.4.0`: Meet coding agents where they work

Expose the same review and verification capabilities through a **thin agent-integration layer**. Prioritize a proven integration workflow with Claude Code or Copilot; choose MCP, skills, instructions, or another interface only after testing the host's actual capabilities.

- An agent should invoke review on an explicit branch pair and optionally submit its own candidate findings, without being forced to call a second LLM.
- Return structured context, requirement assessments, Conventional Comments and provenance. A tool call must not silently upload source or run code.
- Compare an agent using normal Git/tests with the same agent using VerifyCraft on a controlled dataset; measure whether the tool genuinely improves review quality, time or evidence reliability.
- Keep review core standalone. Do not duplicate reasoning, check policies or formatting inside the agent adapter.

**Exit gate:** A documented end-to-end task works in a real agent host, requires little setup and improves a measurable developer outcome. If a simple instruction to the agent works just as well, do not add a redundant adapter.

## 7. `v0.5.0`: GitHub Action, optional LangGraph and ecosystem

- **GitHub Action:** Review PR diffs with minimum permissions; publish an artifact/summary by default; post Conventional Comments inline only under explicit policy. Resolve changed-line anchors, avoid duplicate comment spam, handle forked PRs as untrusted input, and never expose write tokens to executable PR content.
- **LangGraph adapter:** Optional node(s) over the same SDK with JSON-serializable contracts; no duplicated orchestrator.
- **Reliability:** Expand benchmarks across independent repositories, provider variants, requirements quality levels and languages. Document the coverage and limits of each execution backend.
- **Compatibility:** Define schema/version migration, deprecation, supported environments, contributor docs and security reporting.

**Exit gate:** A new developer can adopt direct SDK, CLI or a relevant adapter without knowing VerifyCraft internals; reports are substantively equivalent for the same inputs and declared review mode.

## 8. Prioritization and anti-goals

| Must exist before wider adoption | Later, after value is demonstrated | Avoid |
| --- | --- | --- |
| Real diff-aware review | More languages and models | Creating a general AI-agent operating system |
| Requirements mapping with citations | Automated requirement importers | Invented acceptance criteria |
| Conventional Comments + JSON | PR comment publishing | An agent council as the default reviewer |
| Useful CLI + SDK review experience | LangGraph/MCP/GitHub Action wrappers | Empty adapters before the core works |
| Honest evidence/uncertainty | Sophisticated verification orchestration | Relabeling pytest output as bug discovery |
| Reproducible quality evaluation | More test backends | Claiming a keyword heuristic is an LLM benchmark |

## 9. Initial implementation backlog for `v0.2.0`

Work in a single release-preparation branch; each item should finish with a runnable acceptance test. Do not create empty future modules.

- [ ] Update `docs/FOUNDATIONS.md` and this roadmap; align README with what `v0.1.0` does versus proposed behavior.
- [ ] Define `ReviewRequest`, `ReviewFinding`, `RequirementAssessment` and `ReviewReport` with explicit review/evidence status separation and JSON serialization.
- [ ] Implement resolved Git revision/diff ingestion with bounded context and validated line/rename mapping.
- [ ] Implement requirement-source parsing and per-requirement traceability/coverage reporting.
- [ ] Implement one genuinely useful semantic reviewer with an explicitly configured provider; add a deterministic test adapter.
- [ ] Support externally supplied candidate findings from an existing agent without a mandatory additional model call.
- [ ] Implement claim validation, context/counterexample checks, deduplication and severity/priority policy.
- [ ] Render correct Conventional Comments, Markdown and JSON with file/line/source references.
- [ ] Expose review from the Python SDK and CLI with equivalent behavior and proper error handling.
- [ ] Add a complete examples repository/fixture and golden acceptance tests, including at least one requirement violation and one clean case.
- [ ] Benchmark against a real LLM/agent baseline with comparable inputs; publish limitations and actual metrics.
- [ ] Preserve existing `v0.1.0` verification behavior; run full CI, packaging, security and documentation checks.

## 10. Concrete end-to-end acceptance scenarios

These are proposed test cases, **not results already achieved**.

| Scenario | Required behavior |
| --- | --- |
| Changed auth handler violates a supplied expiration requirement | Issue with exact changed-line anchor, requirement citation, impact and actionable fix; do not call it runtime-verified if no check ran. |
| Token consumption occurs in an unchanged repository helper | Reviewer inspects relevant context and avoids falsely reporting token reuse. |
| A requirement is vague or two sources conflict | Emit a question/unclear assessment, not a fabricated blocking issue. |
| A code diff includes a known logic regression with no requirements supplied | Produce a code-specific review finding and state `requirements_not_assessed`. |
| A clean change or stylistic-only changes | Do not fabricate blocking bugs or fill the report with low-value nitpicks. |
| A renamed file or changed-line anchor cannot be resolved | Use correct Git line mapping or fall back to file/summary context, never invent a location. |
| No configured provider and no external candidates | Explicit error or limited deterministic mode; never present an empty semantic review as thorough analysis. |
| An external agent supplies candidates | VerifyCraft normalizes, challenges and renders without automatically invoking another LLM. |
| Untrusted PR content requests secret disclosure or command execution | Treat instructions as untrusted data; never run or exfiltrate without explicit safe authorization. |
| Budget or source-context limits are reached | Report omitted scope, partial coverage and unknowns rather than false completeness. |

## 11. Metrics and go/no-go decision

Treat quality as a multi-objective evaluation, not a single accuracy score. Publish datasets, annotations, evaluation method and failure cases.

- **Actionable finding precision:** proportion of actionable comments judged correct and useful by independent review.
- **Known-defect yield:** rate at which relevant known regressions and requirement violations are surfaced.
- **Requirements traceability:** number of supplied requirements assessed with a defensible source and code link; track false claims of compliance separately.
- **Unsupported assertion rate:** proportion of asserted facts lacking adequate cited context or appropriately qualified uncertainty.
- **Unnecessary comment volume:** low-value duplicates, speculative blocking comments or cosmetic noise per change.
- **Evidence yield:** when checks run, rate of trustworthy `supported`/`refuted` outcomes and explicit inconclusive/blocked outcomes.
- **Practical costs:** reviewer setup effort, latency, provider tokens/price, commands, CI resources and developer follow-up time.

**Go** if the first review slice adds demonstrable actionable insight or substantially reduces user effort compared with a credible baseline, while preserving transparent uncertainty and safety. **No-go / rescope** if it merely repackages what the same coding agent already does without improving outcomes. Do not accelerate integration work to compensate for poor results.

## 12. Documentation consistency and history

Keep historical release notes for `v0.1.0` intact, but avoid broad claims that the current product discovers code-review findings. Make the README explicit about what is available now and what is proposed. Replace stale statements such as "working name" or "not implemented" in the foundation and treat the old Stage 0–5 ordering as historical planning, not a binding dependency chain for a review-first product.

**Related documents:** [FOUNDATIONS.md](FOUNDATIONS.md), [Release guide](RELEASING.md), [v0.1.0 release](https://github.com/apply-the/verifycraft/releases/tag/v0.1.0), [Conventional Comments](https://conventionalcomments.org/).
