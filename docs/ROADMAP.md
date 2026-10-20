# VerifyCraft: Roadmap

> **Working name:** VerifyCraft  
> **Proposed repository:** `apply-the/verifycraft`  
> **Planning approach:** Outcome-based milestones, not fixed release dates.  
> **Goal:** Prove that an independent verification capability adds measurable value before expanding into an ecosystem.

## 1. Where we are going

Build one reusable, Python-first capability engine, exposed both directly and through integrations:

![VerifyCraft architecture with original Box, Terminal, Code 2, Workflow, and Git Pull Request icon concepts](assets/architecture.svg)

**Core promise:** **Model-agnostic · Typed contracts · Bounded execution · Verifiable results**.

The end-state includes a **CLI** for developers and CI/CD, a **Python SDK** for custom AI agents, a **LangGraph Adapter** for agent workflows, and a **GitHub Action** for automated PR review. These are interfaces to the same engine, **not independent reimplementations**.

Read [FOUNDATIONS.md](FOUNDATIONS.md) for principles, safety boundaries, and proposed data contracts.

## 2. Delivery sequence

| Stage | Outcome | Exit criteria |
| --- | --- | --- |
| **0. Frame and benchmark** | A falsifiable first use case | Golden PR dataset; simple LLM baseline; preliminary metrics and resource budget. |
| **1. Verification vertical slice** | End-to-end PR regression evidence | Python/pytest workflow verifies or rejects bounded hypotheses and returns structured, reviewable results. |
| **2. Public core: SDK + CLI** | Usable standalone component | Stable v0 contracts, installation, CLI/SDK parity, examples, CI, clear failure modes. |
| **3. Embeddable adapters** | Broad distribution | Optional LangGraph adapter and GitHub Action reuse the same package and policies. |
| **4. Specialized capabilities** | Composable review and testing modules | Independent `review` and `test` APIs, shared evidence semantics, measurable value from composition. |
| **5. Hardening and ecosystem** | Trusted OSS building block | Multi-repo benchmarks, compatibility policy, observability, docs, contributor experience. |

Stages are **sequential gates**, not promises about calendar dates. A failed gate should trigger scope reduction or redesign rather than automatic expansion.

## 3. Stage 0: Define the test of value

**Objective:** Determine whether evidence-driven verification can outperform a basic prompt-based code review on a narrow, reproducible task.

**Build:**

- Select **Python + pytest** and pull-request regressions as the initial target.
- Assemble a small, versioned set of known-good and known-buggy changes; preserve expected outcomes.
- Implement a minimal baseline: a single LLM reviewer that receives the same diff/context budget and emits candidate findings without execution-based verification.
- Define metric collection and a fixed evaluation protocol, including how ambiguous findings are adjudicated.
- Fix safety and cost ceilings for local execution and CI experiments.

**Deliverables:** `benchmarks/`, dataset documentation, baseline runner, metric definitions, first architecture decision records.

**Exit gate:** The benchmark can be rerun by another developer and supports apples-to-apples comparisons.

## 4. Stage 1: Implement the first real capability

**Objective:** Turn a proposed regression into an explicit evidence-backed outcome.

**Vertical slice:**

1. Read the repository revision and PR diff.
2. Gather a bounded amount of relevant code context.
3. Generate a small number of hypotheses or accept externally supplied hypotheses.
4. Select an **approved** pytest/static check and its expected observation.
5. Execute with fixed timeouts, attempt limits, file-system restrictions, and isolated workspace.
6. Record evidence with revision fingerprints, output, provenance, and resource usage.
7. Return `supported`, `refuted`, `inconclusive`, or `blocked` for each claim.

**Deliverables:** typed contracts; evidence capture; execution policy; Python/pytest check adapter; structured JSON result; end-to-end fixtures.

**Exit gate:** On the golden dataset, the engine handles proof, failed proof, stale evidence, timeout, denied command, and exhausted budget without misrepresenting uncertainty or silently retrying.

**Important:** If safe execution requires a container, enforce it before running untrusted pull-request code. A model-proposed shell command is **never** sufficient authorization.

## 5. Stage 2: Release the first usable product

**Objective:** Make the vertical slice directly adoptable without an agent framework.

**Ship together:**

- Python SDK for direct and agent-embedded calls.
- CLI commands for reviewing/verifying a diff and exporting JSON.
- Configuration for provider adapters and resource limits.
- JSON Schema/version compatibility rules, documented verdict semantics.
- Local installation guide, quickstart, and complete example repository.
- Unit, contract, integration, packaging, and security CI checks.

**Exit gate:** A new developer can install the package, run a documented example, inspect evidence, and integrate the SDK with no Boundline, Canon, LangGraph, or custom service required.

**Release candidate:** `0.1.0` **only when** Stage 2 gates pass. Version is a target, not a schedule.

## 6. Stage 3: Add distribution adapters

**Objective:** Make the same capability available where developers already work.

**LangGraph Adapter**

- A thin node/adapter consuming and producing typed/JSON-serializable data.
- Optional dependency and an integration test with an example graph.
- No duplicated retry, evidence, or budget logic inside the adapter.

**GitHub Action**

- Runs on PR diffs with minimal required permissions.
- Emits JSON artifact and a concise check summary; PR comments are optional and policy-controlled.
- Handles forked/untrusted PRs safely and never exposes credentials to the code under test.
- Reuses the SDK and the same verdict semantics.

**Exit gate:** Equivalent fixtures yield semantically identical findings through CLI, SDK, LangGraph, and GitHub Action. Any environment-specific limitation is clearly indicated.

## 7. Stage 4: Expand only through standalone capabilities

**Proposed capability contracts:**

| Capability | Primary input | Primary output | Composition |
| --- | --- | --- | --- |
| **`verify`** | A testable claim and allowed checks | Evidence and verdict | Foundational reusable primitive. |
| **`review`** | Diff, context, policy | Candidate findings/hypotheses | `review + verify`: evidence-aware code review. |
| **`test`** | Target, expected behavior, policy | Candidate tests, execution evidence | `test + verify`: generated tests with verified outcomes. |
| **`challenge`** (optional) | Finding and evidence | Counterexamples and disputed assumptions | `review + challenge + verify`: false-positive reduction. |
| **`context`** (optional) | Repository, question, budget | Traceable context selection | Improve review quality and cost. |
| **`govern`** (optional) | Policy and proposed action | Allow/deny/approval decision | Explicit gates, not a replacement agent runtime. |

**Rule:** Every new capability must work alone and justify its inclusion through a measured improvement. Do not ship the table as six empty packages.

**Exit gate:** At least two capabilities can be used independently **and** together, without hidden shared-state requirements or mandatory orchestration.

## 8. Stage 5: Measure, stabilize, and enable contributions

**Benchmark across multiple projects** (initially Python repositories):

- **Finding precision:** how many reported issues are real and relevant.
- **Verified-finding yield / recall proxy:** how many known benchmark regressions receive a correct, evidence-backed outcome.
- **False-positive rate:** erroneous actionable claims, tracked separately from inconclusive/blocked findings.
- **Verification completion rate:** share of hypotheses that reach a supported or refuted verdict under the budget.
- **Cost and latency:** time, tool calls, model tokens, and estimated spend per PR / per validated finding.
- **Staleness and reproducibility:** whether results correctly invalidate after a revision change and can be inspected later.

**Deliverables:** comparison reports, fixtures, reproducible CI benchmark jobs, API compatibility statement, contribution guide, security policy, and release automation.

**Exit gate:** The benchmark demonstrates a useful trade-off over the baseline, with failure cases and limitations published rather than hidden.

## 9. Explicit prioritization

| Do first | Do after validation | Avoid until there is evidence |
| --- | --- | --- |
| Python + pytest | TypeScript/JavaScript | Universal language support |
| One repo / one core | More execution backends | Plugin marketplace |
| Verify candidate regressions | Richer automated code review | Multi-agent council by default |
| Typed verdicts and provenance | Mutation testing | Long-running agent sessions |
| Safe bounded checks | Optional hosted provider integrations | SaaS dashboard / billing |
| SDK + CLI | LangGraph + GitHub Action | Custom workflow/orchestration engine |

The stage sequence remains authoritative: **do not build optional adapters before the core use case is reliable**, even though the interfaces are part of the overall product design.

## 10. Known risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Recreating a large agent platform | Enforce small APIs, narrow capability ownership, and no mandatory persistent runtime. |
| False confidence from LLM reasoning | Separate candidate findings from evidence verdicts; include explicit inconclusive states. |
| Security exposure while running PR code | Isolated execution, least privilege, restrictions on network/secrets and tool allowlists. |
| High per-PR cost or latency | Budget limits, contextual relevance filters, early stopping, low-cost baselines. |
| Benchmarks tailored to the product | Freeze datasets, publish selection criteria, include independent repositories. |
| Low adoption despite technical quality | Ship usable SDK and CLI early, excellent examples, minimal install friction. |
| Python performance concerns | Measure real bottlenecks; optimize or use Rust only for proven hot paths. |

## 11. First backlog, in suggested order

- [ ] Create the repository and select the final package name after availability checks.
- [ ] Publish foundations, roadmap, and the shared architecture diagram.
- [ ] Write a one-page specification for the initial Python/pytest PR-regression scenario.
- [ ] Assemble the first benchmark fixture set with ground truth.
- [ ] Create a simple reviewer baseline and metrics script.
- [ ] Define v0 typed contracts and evidence/verdict invariants.
- [ ] Implement repository/diff fingerprinting and context limits.
- [ ] Implement one approved verification check backend with safe execution.
- [ ] Produce an end-to-end JSON report and test the negative/error paths.
- [ ] Add SDK and CLI examples that produce equivalent output.
- [ ] Compare results against the baseline before adding more capabilities.

## 12. First public demo

A developer checks out a Python repository and invokes a conceptual command:

```bash
verifycraft review . --base origin/main --verify --format json
```

VerifyCraft returns candidate regression findings, checks executed under the configured policy, supporting or contradictory evidence, and clear uncertainty and budget states. **No general-purpose framework setup is required.**

The specific CLI flags, model provider, and JSON schema are design proposals until implemented.

---

**Success means:** developers can use VerifyCraft immediately, agent builders can embed it without adopting a framework, and the evidence makes results meaningfully more trustworthy than an unsupported LLM assertion. **Not** that the system produces the largest number of findings or runs the most agents.
