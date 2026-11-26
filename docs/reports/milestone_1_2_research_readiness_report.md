# Milestone 1.2 — Research Readiness Report

**LLM-Gold Ground Truth Construction and Group A Business Decision Evaluation**

| Field | Value |
|---|---|
| Ground Truth version | `cm-outbound-llm-gold-v1` (frozen) |
| Ground Truth model | DeepSeek V4 Pro, API identifier `deepseek-v4-pro` |
| Ground Truth type | `llm_generated_gold` (project-scored gold, **not** verified China Mobile policy) |
| Annotation / adjudication prompt version | `v1` |
| Evaluation prompt version | `v1` |
| Evaluated condition | Group A (no flowchart, no workflow, no simulator) |
| Evaluated agent run set | `20261125T164504Z-group-a-dev-eval` (pre-existing, reused) |
| Report date | 2026-11-27 |

---

## 1. Summary

The benchmark can now measure business decision quality against a frozen reference.
DeepSeek V4 Pro produced two independent annotation passes plus adjudication for
800 real-transcript tasks (400 Train-R, 400 Dev), the labels were validated and
frozen, and the **existing 400 Group A executions were scored blind** against the
frozen Dev labels by a separate two-pass V4 Pro evaluation.

**Headline result — without any flowchart, Group A reaches a LLM-Gold Decision
Success Rate of 0.8338 (95% CI 0.7959–0.8687) on 391 scored Dev tasks.**

That number must be read together with §9: the Gold Labels are broad, union-based
acceptable-decision sets, so 0.8338 measures agreement with a permissive reference,
not conformance to a single canonical answer.

| Metric | Value | 95% CI |
|---|---:|---|
| LLM-Gold Decision Success Rate (DSR) | **0.8338** | 0.7959 – 0.8687 |
| Decision Acceptance Rate (DAR) | 0.9770 | 0.9616 – 0.9898 |
| Partial Decision Rate (PDR) | 0.1432 | 0.1100 – 0.1781 |
| Incorrect Decision Rate (IDR) | 0.0230 | 0.0077 – 0.0384 |
| Scored Task Coverage | 0.9775 | — |
| Critical error rate | 0.0230 (9 tasks) | — |
| `policy_verified_decision_success_rate` | not available | — |

Executed 400 · scored 391 · excluded 9 · outcomes: 326 fully correct, 56 partially
correct, 9 incorrect, 9 unscorable.

---

## 2. What was built

Milestone 1.1 infrastructure was extended, not rewritten. New modules:

```text
src/cm_outbound_bench/decisions/
    gold/        config, provider, prompts, annotator, adjudicator,
                 agreement, validation, storage, audit, models, cli
    scoring/     scorer, metrics, statistics, reports, schema, cli
```

- `decisions/gold/` — dedicated DeepSeek V4 Pro annotator/judge: config loading
  and model verification, stateless two-pass annotation, semantic comparison,
  adjudication, gold-reference construction, validation, immutable freeze,
  checkpoint/resume with deterministic cache keys.
- `decisions/scoring/` — blind two-pass output scoring, scorer-adjudication,
  aggregation, metrics, bootstrap statistics, report rendering.
- `scripts/audit_gold_integrity.py` — offline audit that re-hashes every saved
  annotation pass from the frozen tasks and prompt files (no API calls).
- Prompts under `prompts/` (6 versioned files), docs under `docs/annotation/`.

Two roles are kept strictly separate:

| Role | Config | Model |
|---|---|---|
| Group A Agent (frozen, untouched) | `configs/baseline_agent.toml`, `providers.toml` | `deepseek-flash`, temp 0.2 |
| Ground Truth generator / Evaluation judge | `configs/decision_annotation.toml` | `deepseek-v4-pro`, temp 0.0 |

No credential is stored in either config; the key is read from
`DEEPSEEK_API_KEY`. There is no silent fallback: if the judge model is
unavailable the run fails loudly (`JudgeConfigError` / provider error) and no
label is written.

---

## 3. Ground Truth definition and provenance

**Official Benchmark Ground Truth = the final DeepSeek V4 Pro-generated and
adjudicated reference decision frozen in the Benchmark Evaluator.**
(`docs/annotation/llm_gold_ground_truth_protocol_v1.md`)

Every one of the 800 frozen references carries:

```text
ground_truth_type   = llm_generated_gold
ground_truth_source = deepseek_v4_pro
ground_truth_status = frozen
ground_truth_version= cm-outbound-llm-gold-v1
annotation_method   = llm_based
annotation_status   = accepted_as_benchmark_gold
policy_verified     = false   (on all 800)
```

`policy_verified = false` is not a defect: no authoritative China Mobile policy
document exists in the workspace, so the labels are a research-grade decision
reference, never a claim about official policy. The benchmark therefore reports
decision quality, and leaves `policy_verified_decision_success_rate` empty.

Human annotation or approval was not required at any point; V4 Pro performed
annotation, adjudication and scoring. The label is nevertheless clearly
provenanced as model-generated, so it cannot be mistaken for independent
verification.

---

## 4. Data and sample integrity

Re-derived from the actual manifests and files (`annotation_overlap_report.json`,
re-verified by `scripts/audit_gold_integrity.py`):

| Collection | Tasks | Unique task ids | Unique conversation units |
|---|---:|---:|---:|
| Train-R pilot | 100 | 100 | 100 |
| Train-R feedback | 300 | 300 | 300 |
| Dev | 400 | 400 | 400 |

- Pilot ∩ Feedback = 0 task ids, 0 conversation units; Pilot ∩ Dev = 0;
  Feedback ∩ Dev = 0. The three samples are **mutually disjoint**, so the
  100 pilot tasks and the 300 feedback tasks are counted separately (400
  Train-R references in total) and nothing is double-counted.
- No duplicate task ids or conversation units across manifests.
- Every one of the 400 Dev references maps to exactly one of the 400 Group A
  traces; the Dev reference id set is identical to the trace id set.
- Split consistency re-checked per reference: Train-R references are all
  `train_r`, Dev references are all `dev`. **No Test task appears anywhere.**
- One task per conversation unit in Dev, so a conversation-unit bootstrap and a
  task-level bootstrap coincide (recorded as `independent_sampling_unit =
  conversation_unit`).

Test remains locked: the annotation CLI exposes only `pilot`, `train-r` and `dev`
subcommands, no `--split`/`allow_test` flag exists, and Test was not annotated,
scored, or used for prompt development.

---

## 5. Annotation execution and intra-model consistency

Two stateless V4 Pro passes (separately worded prompts, same rubric, no shared
context) produced 1,600 pass records; 742 disagreements went to a third V4 Pro
adjudication call.

| Stage | Tasks | Scored / unscored | Adjudications | Tokens |
|---|---:|---:|---:|---:|
| Train-R pilot | 100 | 98 / 2 | 91 | 676,497 |
| Train-R feedback | 300 | 295 / 5 | 283 | 2,008,968 |
| Dev | 400 | 391 / 9 | 368 | 2,640,226 |
| **Total** | **800** | **784 / 16** | **742** | **5,325,691** |

### Pass comparison

| Stage | agreement | acceptable alternatives | minor | major | both unscored |
|---|---:|---:|---:|---:|---:|
| Pilot | 2 | 5 | 73 | 18 | 2 |
| Feedback | 7 | 5 | 210 | 73 | 5 |
| Dev | 15 | 8 | 272 | 96 | 9 |

Pooled exact-pass agreement is **3.0% (24/800)**. Per-dimension agreement is much
higher than the headline figure suggests:

| Dimension | Pilot | Feedback | Dev |
|---|---:|---:|---:|
| Answerability match | 0.91 | 0.88 | 0.86 |
| Primary decision category match | 0.78 | 0.74 | 0.73 |
| Scoreability match | 0.96 | 0.97 | 0.97 |
| Mean action similarity | 0.388 | 0.363 | 0.365 |

The low agreement rate is a **rubric-strictness artefact**, not evidence that the
model contradicts itself: `agreement` requires the same category *and* action
similarity ≥ 0.70 *and* required-step Jaccard ≥ 0.50. Two independently written
Chinese sentences describing the same action rarely clear a 0.50 step-set
Jaccard. The adjudicator's behaviour confirms this reading: it accepted **both**
passes in **695/742 (93.7%)** of cases.

### Adjudication outcomes (pooled)

| Outcome | Count |
|---|---:|
| Accept both alternatives | 695 |
| Accept Pass A | 38 |
| Accept Pass B | 7 |
| New consensus decision | 2 |
| Ambiguous / unscored | 0 |

### Label breadth (consequence of "accept both")

| Split | Mean acceptable alternatives | Tasks with ≥1 alternative | Mean required steps |
|---|---:|---:|---:|
| Train-R | 2.81 | 393/400 | 3.67 |
| Dev | 2.76 | 391/400 | 3.60 |

Adjudication unions both passes' acceptable sets, so the frozen reference is a
*set* of acceptable next actions, not one canonical action. This is the intended
design ("support multiple correct answers"), but it makes the reference generous
and is the single largest caveat on the DSR in §7.

---

## 6. Freeze

| Item | Value |
|---|---|
| Frozen at | 2026-11-26T15:53:15Z |
| Train-R references | 400 (393 scored / 7 unscored) |
| Dev references | 400 (391 scored / 9 unscored) |
| Train-R SHA-256 | `03d2e23bed2e8d771284d3b2a79b88d1f792e5d6fbecff642360f0112c4c4bdd` |
| Dev SHA-256 | `a7d12662af0745b4800161a1b93bbd0ada988d7a0bc671e3710c482446ba44b6` |
| Test annotated | false |

Validation applied before freezing: evidence turns inside the visible prefix,
no future-turn reference, `policy_verified = false`, scored tasks must carry a
decision or explicit clarification requirement, unscored tasks carry a reason and
no decision, duplicates rejected, split checked per reference. Both frozen hashes
were recomputed after the evaluation and are unchanged — scoring never touched
the Gold Labels.

---

## 7. Group A evaluation (blind, two V4 Pro passes)

Preconditions verified before opening the agent outputs: gold frozen, all 400
Dev ids matched the 400 traces, trace inputs re-verified
(`prompt_hash_mismatches = 0`, `private_token_leaks = 0`,
`tasks_missing = 0`), and only scored references enter the DSR denominator.

The judge received the agent-visible prefix, the frozen Gold Label and the
agent's structured output — never the agent model identity, the condition, the
historical continuation, or Test references.

### Primary and secondary results

| Metric | Value | Denominator |
|---|---:|---:|
| LLM-Gold Decision Success Rate (DSR) | 0.8338 | 391 |
| Decision Acceptance Rate (DAR) | 0.9770 | 391 |
| Partial Decision Rate (PDR) | 0.1432 | 391 |
| Incorrect Decision Rate (IDR) | 0.0230 | 391 |
| Scored Task Coverage | 0.9775 | 400 executed |
| Critical error rate | 0.0230 | 391 |
| Appropriate clarification rate | 0.1500 | 40 |
| Required-step recall (lexical proxy) | 0.0729 | 391 |
| Branch accuracy (lexical proxy) | 0.1045 | 268 |
| Required-step precision | not reported | — |
| `policy_verified_decision_success_rate` | not available | — |
| `response_quality` | not available | — |

The three lexical proxies are character-substring surrogates over free-text
Chinese: a semantically correct paraphrase that shares no literal substring
scores zero, so they are **lower bounds and must not be read as semantic
accuracy**. Required-step precision is not reported at all, because the frozen
Gold Label records steps as actions while the agent output exposes requested
information, so no valid denominator exists.

### Scorer consistency

| Item | Value |
|---|---:|
| Scorer A/B agreement | 370 / 391 = 0.9463 |
| Scorer disagreements adjudicated | 21 (5.37% of scored) |
| Evaluation API calls | 800 pass + 21 adjudication |
| Evaluation tokens | 1,266,711 |

This is **intra-model consistency** of two V4 Pro scoring passes, not human
inter-rater reliability. No kappa is reported because both "raters" are the same
model.

### Critical errors (flagged independently of the four-way outcome)

| Kind | Count |
|---|---:|
| `contradicts_explicit_customer_information` | 4 |
| `unsupported_pricing_claim` | 3 |
| `unjustified_completion_claim` | 2 |
| Total tasks with a critical error | 9 (5 incorrect, 4 partially correct) |

No `fabricated_business_policy` or `fabricated_package_eligibility` case was
flagged.

---

## 8. What the numbers say about Group A

- **The agent usually lands within the acceptable set** (DAR 0.977) but often
  only partially satisfies the specific required steps: 56 of 391 outputs are
  `partially_correct`, and most of those miss "state the purpose of the call",
  "confirm the customer's consent before registering the service", or "confirm a
  detail the customer already excluded".
- **Outright wrong decisions are rare (2.3%)** and are dominated by two patterns:
  contradicting an explicit customer constraint (e.g. offering Saturday when the
  customer said Friday and Saturday are impossible) and claiming an operation was
  completed before consent.
- **Unsupported pricing claims** (3 cases) and **unjustified completion claims**
  (2 cases) are the only integrity-relevant failures; both are exactly the class
  of error the rubric was designed to catch.
- **Call termination**: closing decisions are common (98/400 Dev gold primaries),
  consistent with the Milestone 1.1 observation that ~25% of proposed actions
  were call-ending. Where gold expects closure the agent usually complies; the
  errors are premature closure, not failure to close.

These are descriptive statements about the scored sample, not causal claims.

---

## 9. Limitations

1. **The reference is model-generated, not policy truth.** `policy_verified =
   false` everywhere; the benchmark measures decision quality against a V4 Pro
   adjudication, and no statement here should be read as China Mobile policy.
2. **The reference is broad.** 2.76 acceptable alternatives per Dev task on
   average, produced by unioning both passes in adjudication. A stricter
   single-canonical-answer reference would very likely lower the DSR. The 0.8338
   figure is therefore an estimate of "did the agent do something professionally
   acceptable", not "did it produce the one right response".
3. **Intra-model consistency is low at the exact-decision level** (3% raw pass
   agreement; 73–78% category agreement). The frozen label is the adjudicator's
   synthesis, not a stable consensus of two independent judges. Two passes of the
   same model share its blind spots; this is *not* human reliability and cannot
   detect a systematic model error.
4. **Confidence intervals capture sampling uncertainty only** (conversation-unit
   percentile bootstrap, 2,000 replications, seed 20261126). They do not include
   annotation-design or judge error, so true uncertainty is wider than the
   reported interval.
5. **Secondary metrics are lexical.** Clarification, required-step recall and
   branch accuracy are substring surrogates; they understate semantic compliance
   and are reported only as diagnostics.
6. **One conversation unit per task in Dev** means tasks are not fully
   independent of speaker style, but the bootstrap unit matches the sampling unit.
7. **Channel semantics remain unresolved.** Channels 1/2 map to
   `historical_agent`/`customer`; channels 3–5 stay unmapped and were not
   retro-inserted. Any Channel 3/4 content is absent from the visible prefix, so
   some prefixes are thinner than the real conversation.

---

## 10. Defects found and corrected in this milestone

The evaluation artifacts were generated at 00:06:42 and the `is_scored` gold
gate was written at 00:07:00 — the published metrics predated their own guard.
Verification caught this and it was corrected deterministically, **without any
new API call**:

| Defect | Effect | Correction |
|---|---|---|
| 9 Dev tasks with an **unscored** Gold Label were still sent to the judge | 4 of them came back `fully_correct`, inflating DSR from 0.8338 to 0.8354 and understating exclusions (5 instead of 9) | Added `aggregate_saved_scores` + `decision evaluate-group-a --reaggregate`, which rebuilds finals from saved passes and saved adjudications and enforces the gold gate. Corrected: 391 scored / 9 unscorable. |
| `required_step_precision` compared requested *information* to gold *action* steps | meaningless 0.0013 reported as a real metric | Now `null` with an explicit reason; the lexical value is kept as `required_step_precision_lexical_diagnostic` |
| `scorer_disagreements` counted all saved records while the rate used finals | 24 vs 21 inconsistency | Computed from finals: 21 |
| `evaluation_manifest.frozen_manifest_path` was always empty | broken provenance link | Records the actual frozen manifest path |

Not corrected, and reported instead: 18 scoring calls (9 tasks × 2 passes) were
spent on unscored-gold tasks before the gate existed. Their verdicts are retained
in `records/` as an audit trail but excluded from every metric.

---

## 11. Tests

Full suite (`python -m pytest`, PYTHONPATH=src): **1,520 passed, 8 failed,
25 subtests passed.**

All 8 failures are in **files untouched since the v1.0.0 baseline commit** and are
environmental, not Milestone 1.2 regressions:

| Failing test | Cause |
|---|---|
| 7 × `tests/test_deidentify.py` | Windows does not honour POSIX 0600 (returns 0666) and the account cannot create symlinks (`WinError 1314`); the de-identification tests assert POSIX permissions/symlink rejection |
| 1 × `test_benchmark_lifecycle.py::test_wall_clock_deadline_is_enforced_between_actions` | a 1e-9 s deadline loses the race to `post_closure_limit`; pre-existing ordering in `benchmark/execution.py`, unmodified by this milestone |

Milestone 1.2 modules specifically: **95 tests pass** (90 existing + 5 new
regression tests pinning the unscored-gold gate: unscored gold never enters the
denominator, agreement rebuild, saved-adjudication reuse, missing-adjudication
fallback, missing-pass reporting).

Test coverage added for the required properties: V4 Pro is the configured model,
no provider fallback, agent-visible data only, Pass A/Pass B isolation,
adjudicator blindness to Group A output, multiple accepted decisions, unscored
preservation, freeze-before-scoring, scorer isolation, provider-identity hiding,
four-way outcome distinction, critical-error separation, gold immutability, Test
inaccessibility, no simulator invocation, stable hashes, resume, and
duplicate-free counting.

---

## 12. Reproducibility

| Item | Value |
|---|---|
| Annotation config hash | `a30cc8e984365fd7104cf5717f52354e3925115968733adebcd79a2729373ca2` |
| Evaluation config hash | `c1da02e897207aa1a6a90f106d6065d3712cba4c448a97a38ee559bf63197abb` |
| Annotation prompt version | `v1` (per-record `prompt_hash`) |
| Evaluation prompt version | `v1` |
| Temperature / max tokens (judge) | 0.0 / 6000 |
| Judge model | `deepseek-v4-pro` |
| Agent model (frozen) | `deepseek-flash`, temperature 0.2, `baseline-a-v1` |
| Bootstrap | 2,000 replications, seed 20261126 |

Integrity audit (`python scripts/audit_gold_integrity.py`): **1,600 / 1,600 saved
annotation passes re-hash exactly** to a prompt rebuilt from the frozen task
prefix and the frozen prompt file; both frozen reference hashes match the
manifest; the Dev reference id set equals the Group A trace id set. Every record
carries run id, model, provider, prompt version/hash, config hash, temperature,
max tokens, request timestamp, API request id, response hash, retry count, token
usage and parsing status — never a credential.

Reproduce the evaluation without spending tokens:

```bash
cm-outbound-bench decision evaluate-group-a --reaggregate
cm-outbound-bench decision report-group-a
python scripts/audit_gold_integrity.py
```

Artifacts: `outputs/decision/annotations/{train_r,dev}/`,
`outputs/decision/annotations/manifests/`, `outputs/decision/evaluation/group_a/`,
`outputs/decision/reports/`.

---

## 13. Readiness for Groups B and C

- The frozen version `cm-outbound-llm-gold-v1` is the **single reference** all
  three conditions must use; the freeze rule forbids editing it to improve a
  baseline, and any correction requires a new explicit version plus re-evaluation
  of every condition.
- Train-R Gold Labels (400, 393 scored) are available for RSI feedback through a
  controlled interface. They contain no future-turn content, so RSI can consume
  them without the leakage the protocol forbids.
- The scoring pipeline is condition-agnostic: it hides the agent model, so B and
  C outputs can be scored with the identical two-pass judge and rubric.
- Test (1,508 conversation units) remains untouched and unannotated. Before any
  Test run, Test Gold Labels must be produced and frozen under a new version,
  before any condition's Test outputs are inspected.

**Recommended next actions**

1. Treat 0.8338 / 0.9770 as the Group A business baseline, always quoted with the
   breadth caveat from §9.2.
2. Before B/C, consider a stricter secondary reference (single canonical decision)
   computed from the same passes, so DSR can be reported under both a permissive
   and a strict gold — the gap would quantify how much of the current DSR is
   label breadth.
3. Add a semantic (judge-based) required-step metric when budget allows, replacing
   the lexical proxies.
4. Resolve Channel 3/4/5 semantics before Test annotation, since thin prefixes are
   a data-quality issue rather than an annotation issue.

---

*Ground Truth provenance: every reference in this report was generated and
adjudicated by DeepSeek V4 Pro (`deepseek-v4-pro`) and frozen as
`cm-outbound-llm-gold-v1`. These labels are authoritative for this benchmark's
scoring protocol. They are not independently verified China Mobile policy, and
this report makes no claim that they are.*
