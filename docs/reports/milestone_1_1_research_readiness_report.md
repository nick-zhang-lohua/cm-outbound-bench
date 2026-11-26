# Milestone 1.1 — Research Readiness Report

| Field | Value |
|---|---|
| Date | 2026-11-26 |
| Milestone | 1.1 — business decision evaluation, annotation infrastructure, channel semantics audit |
| Benchmark version | `0.2.0` (evaluator `0.2.0`, reference schema `2.0`, prompt `baseline-a-v1`) |
| Dataset | `cm_outbound_v2` |
| Condition studied | Group A only (no flowchart, no workflow, no simulator) |
| Offline suite | **1,449 passed, 25 subtests passed**, `ruff` clean |
| Group A live run | `20261125T164504Z-group-a-dev-eval` — 400 Dev tasks, 400 successes |
| Business metric status | **not measured** — human annotation is the remaining dependency |

> **Bottom line.** The benchmark can now be scored honestly, but it is not yet
> scored. The evaluation methodology is implemented, the annotation infrastructure
> works end to end, the channel evidence is ready for human review, and Group A has
> been executed on a 400-task Dev sample with verified input isolation. What does
> not exist yet is a human-adjudicated reference set, so every business metric is
> reported as `not available` rather than as a number.

---

## 1. Repository audit

The checkout was inspected before any change, and a reproducible starting snapshot
was taken.

**Baseline verified before modification**

| Fact | Value |
|---|---|
| Git branch / HEAD | `main` / `1d2f5d4 chore: establish v1.0.0 baseline` |
| Pre-existing test suite | 1,342 passed, 25 subtests passed, `ruff` clean |
| Corpus manifest | verified (both file hashes and declared counts matched) |
| `calls.jsonl` sha256 | `d9d4dc3ae16a71f18b094df8c2891aa73d4e51cf44553d82015a8d1e1ce52617` |
| `manifest.json` sha256 | `279b367674a29ebc213c6f0ee10072b10acf9fa872b4e084656ea3841849ed57` |
| Milestone 1 report | `docs/reports/decision_milestone_1_report.md`, present and consistent |

**What was inspected**

`transcripts/` (ingestion, reconstruction, validation, splitting, store,
fingerprint), `decisions/` (schema, builder, store, audit, agent, agent_prompt,
runner, evaluator, trace, verification, config, paths, cli), `corpus/`
(manifest, paths, schema), `providers/`, `domain/data_model.py`,
`data_pipeline/deidentify.py` + `PIPELINE.md`, `docs/*.md`, `configs/*.toml`,
`data/policy/*`, and the derived artifacts under `data/decision/`.

**What was deliberately not touched**

The legacy synthetic runtime (`simulator.py`, `benchmark/simulator.py`,
`benchmark/tasks.py`, `benchmark/toolkits.py`, `llm_agent.py`, `runner.py`,
`evaluator.py`), the synthetic task catalogue, the six synthetic policy documents,
and every Milestone 1 artifact format. No legacy module was rewritten.

**Two real defects found and fixed during the audit**

1. **Credential path through the evaluation.** The runner redacted a provider error
   message when writing the trace-level `error`, but `TechnicalEvaluation.error_message`
   copied the raw message into `trace.evaluation`. A failing provider message
   containing a key would therefore persist. The runner now redacts the invocation
   itself, before evaluation, so every downstream field is clean.
2. **Status echo instead of evaluation status.** `evaluate_invocation` returned the
   reference's declared `scoring_status` when `is_scored` was false. A reference
   marked `scored` but blocked by an annotation gate would have been counted as a
   scored execution. It now reports `unscored` (or `candidate`) explicitly.

One Milestone 1 test assertion was updated: `EVALUATOR_VERSION` moved from `0.1.0`
to `0.2.0` because the business-evaluation semantics changed. The test now asserts
"the evaluator version is recorded", which is the property it was written to protect.

---

## 2. Conversation identity findings

### 2.1 How the identifier is produced

```text
data_pipeline/deidentify.py::make_transcript_id
    "CALL_" + hmac_sha256(key, source_file_name).hexdigest()[:24]
```

The identifier is derived from a **source file name under an HMAC key**. It is not
derived from any call record, because the source contains none: no `call_id`, no
caller/called number, no customer id, no agent id, no wall-clock time.

### 2.2 What one unit represents

One delivered transcript unit is **intended** to represent one complete
conversation. The evidence, all re-verified in this milestone:

| # | Evidence | Re-measured |
|---|---|---|
| 1 | Timestamps monotonic within every unit | 0 inversions |
| 2 | Adjacent records never overlap | 0 overlaps |
| 3 | No unit resets its clock to zero | 0 resets |
| 4 | Duration distribution is single-mode | max 191.7 s, p50 87.1 s |
| 5 | Distinct content hash per unit | 12 duplicate groups only (30 units), grouped not merged |
| 6 | One unit per source file | 4,527 delivered units from 4,738 raw files (211 quarantined) |

### 2.3 Remaining uncertainty

* No external call record (CDR) or vendor specification exists in the workspace, so
  "one unit = one call" **cannot be formally confirmed**.
* A call split across two delivered files would look like two shorter conversations
  and cannot be detected from the data alone.
* Speaker roles come from the research protocol, not the corpus: `role = UNKNOWN`
  for every record.

### 2.4 Terminology standardised

| Term | Meaning | Where |
|---|---|---|
| `conversation_id` | The reconstructed conversation unit (research term) | `NormalizedCall.conversation_id`, `PrivateTaskReference.conversation_id` |
| `transcript_unit_id` | The corpus identifier of the delivered unit | same objects |
| `call_id` | Legacy internal field name; always an **inferred** conversation unit | retained for artifact compatibility |

`CONVERSATION_ID_SEMANTICS` in `transcripts/models.py` states the limitation in code,
and the identity audit repeats it in every report. No report now calls a unit a
"verified telephone call".

---

## 3. Channel semantics findings

**Production mapping unchanged: `1 → historical_agent`, `2 → customer`, 3/4/5 →
`other` (unknown).** No interpretation was approved and none was applied.

Full evidence: **`docs/reports/channel_semantics_audit.md`**. Human-review package:
`outputs/decision/channel_review/` (12 channel-3 samples, 12 channel-4 samples, all 2
channel-5 samples, a summary CSV and a JSON summary).

### 3.1 Measured channels

| Channel | Records | % | Units | Speech | Events | Shape |
|---|---:|---:|---:|---:|---:|---|
| 1 | 104,790 | 76.3898 | 4,526 | 66,387 | 38,403 | mixed |
| 2 | 31,867 | 23.2304 | 4,444 | 27,648 | 4,219 | mixed |
| **3** | **314** | **0.2289** | **84** | **225** | **89** | mixed |
| **4** | **205** | **0.1494** | **42** | **168** | **37** | mixed |
| **5** | **2** | **0.0015** | **1** | **0** | **2** | **event only** |

### 3.2 What the evidence supports

| Hypothesis | Confidence |
|---|---|
| H1 — an unknown channel is an additional distinct human speaker | strongly supported |
| H2 — the channel number is a per-unit speaker slot, not a fixed role | strongly supported |
| H3 — channel 5 is an IVR/system event stream | plausible, unverified (2 records) |
| H4 — an unknown channel is a recording/transcription artifact | unknown; zero overlaps and mostly unique content argue against |
| H5 — an unknown channel is a second agent-side leg | plausible, unverified |
| Official vendor channel specification | **absent from the workspace** |

H2 is the finding with the most consequence: the agent's opening script appears on
**channel 3** in at least three units while channel 1 carries only events, and 11
units have no channel-1 speech at all. The current task builder's refusal to build a
cutoff from a prefix containing an `other` channel is therefore not merely cautious —
it is the correct response to a mapping that is a corpus-level regularity rather than
a per-unit invariant.

### 3.3 Benchmark impact of each candidate interpretation (measured, not applied)

| Interpretation | Tasks | Units | Δ tasks | Approved |
|---|---:|---:|---:|---|
| I0 keep `other` (production) | 10,116 | 4,253 | 0 | — |
| I1 unknown → `customer` | 10,256 | 4,297 | +140 | **no** |
| I2 unknown → `historical_agent` | 10,224 | 4,296 | +108 | **no** |
| I3 channel 3 → `historical_agent` only | 10,218 | 4,295 | +102 | **no** |
| I4 exclude affected units | 10,002 | 4,188 | −114 | **no** |

115 units contain an unknown channel (93 with unknown-channel speech); 71 of the
10,116 tasks live in an affected unit; 173 candidate cutoffs were rejected with
reason `other_speaker_in_prefix`.

### 3.4 What a reviewer must decide

1. Is H1 accepted, and if so is the unknown speaker a `customer`, a separate
   `third_party`, or context-dependent?
2. Is H2 accepted? If yes, the `1 = agent` assumption must be documented as a
   regularity, not a guarantee.
3. What is channel 5?
4. Which interpretation should the benchmark use (I0–I4)?
5. Can a vendor channel specification be obtained? That single document would settle
   H1–H5.

---

## 4. Dataset coverage

`decision audit-identity` recomputes every count and compares it with the recorded
Milestone 1 baseline. Result: **exact match on all fourteen compared facts.**

| Fact | Value |
|---|---:|
| Units read | 4,527 |
| Records | 137,178 (94,428 speech, 42,750 event) |
| Valid / invalid units | 4,524 / 3 |
| Units with both confirmed speakers | 4,392 |
| Duplicate groups | 12 (30 units) |
| Timing inversions / clock resets / overlaps | 0 / 0 / 0 |

### Splits (unchanged, seed `20261125`)

| Split | Units | Actual % | Target % | Deviation (pp) | Tasks | Units with tasks |
|---|---:|---:|---:|---:|---:|---:|
| Train-F | 1,207 | 26.6799 | 26.6667 | +0.0133 | 2,726 | 1,134 |
| Train-R | 1,206 | 26.6578 | 26.6667 | −0.0088 | 2,695 | 1,135 |
| Dev | 603 | 13.3289 | 13.3333 | −0.0044 | 1,314 | 562 |
| Test | 1,508 | 33.3333 | 33.3333 | 0.0000 | 3,381 | 1,422 |
| **Total** | **4,524** | | | | **10,116** | **4,253** |

All seven split verification checks pass, the assignment is reproducible from the
seed, and no duplicate group crosses a split. The split manifest was **not**
modified.

### Task exclusions

| Reason | Count |
|---|---:|
| `prefix_too_short` (insufficient visible context) | 1,472 |
| `duplicate_visible_prefix` | 266 |
| `other_speaker_in_prefix` (unknown channel) | 173 |
| `no_customer_turn` | 124 |
| `no_eligible_cutoff` | 107 |
| `no_agent_turn` | 8 |
| `invalid_call` | 3 |

Rejections are per-cutoff, so they sum above the 271 unit count.

---

## 5. Annotation source inventory

`decision audit-business-sources` scanned the bounded, documented locations
(`data/policy/`, `docs/`, and the workspace `docs/` and `data/analysis/`). It never
opens the raw `data/TXT` export. Result: **31 documents, 0 authoritative.**

| Authority level | Count | What it is |
|---|---:|---|
| `authoritative` | **0** | No official China Mobile policy or verified operation record exists |
| `synthetic` | 8 | The legacy synthetic benchmark policy set (`data/policy/*.md`), each marked "(Synthetic)" |
| `recovered_partial` | 2 | Recovered annotation notes (`data/analysis/annotation_batch_0{1,2}.json`), both explicitly "not import ready" and one explicitly `assistant_preannotation_and_adjudication` |
| `unverified` | 21 | Project documentation and audit reports, including the read-only raw-format audit |

**Consequence.** `policy_verified_decision_success_rate` cannot be produced, and no
business rule was invented. The synthetic policy documents were **not** promoted to
business evidence: they are fiction written to exercise the legacy benchmark.

The two recovered annotation notes are not usable Ground Truth either: they are
partial summaries without label arrays, sample indexes or confidence values, and
their own metadata says a human gold label is still required.

---

## 6. Annotation protocol and infrastructure

### 6.1 Two evaluation claims, never averaged

| Claim | Metric | Available now |
|---|---|---|
| Human appropriateness judgement | `expert_adjudicated_decision_success_rate` | needs annotation |
| Official-policy support | `policy_verified_decision_success_rate` | **structurally unavailable** (no authoritative source) |

Plus `branch_decision_accuracy`, `required_step_precision`, `required_step_recall`,
`policy_compliance`, `appropriate_clarification_rate` and `response_quality`, each
with its own denominator. `tool_intent_accuracy` and
`business_task_completion_rate` remain structurally unavailable and are listed as
such in every report.

### 6.2 Answerability and multiple correct decisions

Every task is classified `sufficient_information`, `more_information_required`,
`ambiguous`, `unscorable` or `unknown`. A clarification request is a **scoreable
correct answer** when the annotation names the missing information. Ambiguous and
unscorable tasks leave every denominator.

Acceptable alternatives, conditional branches, required/optional steps and
prohibited steps are all first-class. Matching uses annotator-supplied
`match_keywords` (falling back to the label), so exact reproduction of the
historical wording is never the criterion.

### 6.3 Independence guarantees, enforced in code

* A packet is built from the agent-visible task object only; a forbidden-key scan
  rejects any packet containing a historical continuation, hidden segment id, call
  id or label.
* A model annotation is `candidate`/`in_review`, never `scored`, and
  `PrivateTaskReference.is_scored` independently refuses `llm_proposed` provenance.
* Two independent human annotations are required; a disagreement needs an
  adjudicator who is neither annotator, and the importer enforces that.
* Both annotators are blind to each other, to the historical continuation and to any
  LLM suggestion during the first pass.

### 6.4 LLM pre-annotation

The capability exists (`llm_proposed` provenance with a mandatory model identity,
`candidate` status, a separate author path) and is documented in protocol §10. It
was **deliberately not run** for this milestone: with no second human annotator
available, a generated candidate set would add review volume without adding an
evaluable reference, and it would raise a real anchoring risk for the first human
pass. No candidate annotation set was produced.

### 6.5 Reproducible samples

All three plans were generated with seed `20261126` and verified to re-derive from
the seed alone (`--verify` → `reproduced: True`).

| Plan | Split | Target | Selected | Conversations | Strata | Selection sha256 |
|---|---|---:|---:|---:|---:|---|
| Train-R annotation pilot | train_r | 100 | **100** | 100 | 12 | `43a7c204…` |
| Train-R feedback sample | train_r | 300 | **300** | 300 | 16 | `a5fe6896…` |
| Dev baseline evaluation sample | dev | 400 | **400** | 400 | 17 | `76037752…` |

The feedback sample excludes the pilot's 100 conversations, so the two Train-R
samples are independent. One task per conversation unit in every plan, so the
effective sample size equals the number of independent conversations.

Dev pool: 562 task-bearing units → 400 selected, meeting the ~400 target with 400
distinct conversations. The 162 remaining independent Dev units are held in reserve
for the expansion rule if the unscorable proportion turns out to be high.

Packets exported and ready for review:

| Packet set | Packets | Contains |
|---|---:|---|
| `data/decision/annotations/train_r_annotation_pilot/` | 100 | prefix + instruction + blank form |
| `data/decision/annotations/train_r_feedback_sample/` | 300 | same |
| `data/decision/annotations/dev_baseline_evaluation_sample/` | 400 | same |
| `data/decision/annotations/output_review/` | 400 | blind agent outputs + fixed rubric |

Every packet set reports `hidden_continuation_exported: false`.

---

## 7. Inter-annotator agreement

**Status: not computable — no human annotation exists yet.**

The machinery is implemented and unit-tested with fixtures:

| Statistic | Implementation | Defined when |
|---|---|---|
| Raw agreement | `agreement.raw_agreement` | ≥ 2 raters on a task |
| Cohen's kappa | `agreement.cohen_kappa` | exactly 2 raters, ≥ 2 units, chance agreement < 1 |
| Krippendorff's alpha (nominal) | `agreement.krippendorff_alpha_nominal` | ≥ 2 units, ≥ 2 observed categories |
| Set precision / recall / Jaccard | `agreement.set_agreement` | ≥ 1 non-empty label set |

An undefined statistic returns `None` with a reason; it is never replaced by `0.0`.
`POOR_AGREEMENT_THRESHOLD = 0.60` triggers a guideline-revision recommendation, and
pre-adjudication agreement is always reported separately from the final consensus.

Running `decision agreement` on the current state reports
`tasks_considered: 0` and `guideline_revision_recommended: false` — i.e. "nothing to
measure", not "perfect agreement". No agreement number is claimed in this report.

---

## 8. Group A baseline results

### 8.1 Configuration frozen and verified

| Setting | Value |
|---|---|
| Agent system prompt | `baseline-a-v1` (`decisions/agent_prompt.py`) |
| Prompt hash | recorded per trace as `input_hash` (sha256 of system+user prompt) |
| Model provider label | `configured-openai-compatible` |
| Base URL | `https://api.deepseek.com/v1` (host recorded, credential never) |
| Actual model identifier | `deepseek-flash` |
| Temperature | 0.2 |
| Maximum output tokens | 900 |
| Timeout / retries | 60 s / 2 |
| Extra parameters | `thinking: {type: disabled}` |
| Output schema | `customer_response`, `next_action`, `required_information`, `tool_intent` (must be null), `uncertainty`, `evidence_summary` |
| Benchmark version | `0.2.0` |
| Dataset version | `cm_outbound_v2` |
| Config hash | `0baa38201da38b73e0f3aae5c960bab21e913f69ffc4aad11c55bf7589e243bc` |

**Reproducibility limitation stated honestly:** `deepseek-flash` is a provider-side
model alias. The provider exposes no pinned version string in its response, so this
run cannot be proven to have used a byte-identical model weight set as a future run.
The configuration hash pins everything the client controls; the model snapshot is
outside it.

### 8.2 Execution

| Field | Value |
|---|---|
| Run set | `20261125T164504Z-group-a-dev-eval` |
| Split | `dev` |
| Tasks executed | **400** (the full Dev evaluation sample) |
| Scoring status | `unscored: 400` |
| Workflow / flowchart access | none |
| Historical future turns given to the agent | none |
| Test data used | none |

### 8.3 Technical performance (measured)

| Metric | Value |
|---|---|
| Request success rate | **1.0000** |
| Valid output rate | **1.0000** |
| Timeout rate | 0.0000 |
| Failed executions | 0 |
| Latency p50 / p90 | **1,558 ms / 1,935 ms** |
| Attempts | 1 for all 400 executions (no retry was needed) |
| Total tokens | 315,360 (mean 788 per task; min 479, max 1,307) |

### 8.4 Business performance

**Not available.** All eight business metrics report `not available` with an explicit
reason. The single reason for every task is `annotation_not_reviewed`:

| Metric | Value | Denominator |
|---|---:|---:|
| `expert_adjudicated_decision_success_rate` | not available | 0 |
| `policy_verified_decision_success_rate` | not available | 0 |
| `branch_decision_accuracy` | not available | 0 |
| `required_step_recall` / `required_step_precision` | not available | 0 |
| `policy_compliance` | not available | 0 |
| `appropriate_clarification_rate` | not available | 0 |
| `response_quality` | not available | 0 |

Reporting `0.0` here would be a fabricated result; reporting any other number would
be worse. The full report is
[Group A Business Baseline Report](group_a_business_baseline_report.md).

### 8.5 What can be said descriptively (not a correctness measure)

| Observation | Value |
|---|---|
| `customer_response` length, chars (min / p50 / max) | 6 / 41 / 144 |
| `next_action` length, chars (min / p50 / max) | 4 / 20 / 68 |
| `evidence_summary` length, chars (p50) | 64 |
| Answers with non-empty `required_information` | 134 of 400 (33.5%) |
| `required_information` item counts | 0 → 266, 1 → 75, 2 → 53, 3 → 6 |
| Answers with a non-null `uncertainty` | **0 of 400** |
| Distinct `next_action` values | 302 of 400 |
| Most frequent stated action | "结束通话" (39), "礼貌结束通话" (31) |

Two observations are worth research attention, and neither is a correctness claim:

1. **The uncertainty field is never used.** 0 of 400 outputs set it. Either the
   prompt does not ask for it clearly enough, or the model does not surface
   uncertainty in this schema. A contract field that is never populated cannot
   support a calibration metric.
2. **Call-ending decisions dominate.** Roughly a quarter of the 400 stated actions are
   a variant of "end the call". Whether that is appropriate depends entirely on the
   conversation, which is exactly what the annotation pass must establish — and it is
   a strong argument for stratifying the analysis by task characteristics once labels
   exist.

### 8.6 Isolation verification

| Check | Result |
|---|---|
| Trace prompt-hash recomputation | 400 checked, **0 mismatches** |
| Private token leak scan in prompts | **0** |
| Missing tasks | 0 |
| Credential scan over artifacts (464 files) | **0** occurrences |
| Flowchart / workflow input | none |
| Simulator invoked | none (static AST scan + clean-interpreter runtime test) |
| Test split accessed | no |

---

## 9. Remaining limitations

1. **No business Ground Truth.** This is the single blocking limitation. Every
   business metric is `not available` because no human-adjudicated reference exists.
2. **No authoritative business reference.** Zero official China Mobile documents in
   the workspace, so the policy-verified tier cannot be populated and no rule was
   invented.
3. **Conversation identity is inferred.** One delivered unit is treated as one
   conversation from corpus evidence; no external call record confirms it.
4. **Speaker roles come from the protocol.** The corpus stores `role = UNKNOWN`.
   H2 suggests the 1=agent mapping is a regularity, not a per-unit invariant.
5. **Channels 3/4/5 are unresolved.** The current construction is conservative
   (173 cutoffs rejected, 71 tasks in affected units) but may discard legitimate
   cutoffs, and it may withhold business-relevant third-party information from the
   agent.
6. **No customer identifier.** Conversations cannot be grouped by customer; only
   content duplicates are, so clustered inference is limited to duplicate groups and
   one-task-per-conversation sampling.
7. **No domain labels.** Neither the split nor the samples are domain-stratified, and
   no domain-stratified result is claimed.
8. **No business backend or tool contract.** `tool_intent` must stay `null`;
   tool-intent accuracy and business task completion remain unmeasurable.
9. **Response quality is unreviewed.** 400 blind review packets exist; no human has
   rated them.
10. **Model version is an alias.** `deepseek-flash` cannot be pinned to a snapshot, so
    exact model reproducibility is not guaranteed.
11. **De-identification residual risk** remains. All conversation-bearing artifacts
    stay in git-ignored directories.

---

## 10. Recommended next milestone

**Milestone 1.2 — Human ad-judication pass and the first real Group A business
number.** In this order:

1. **Review the channel evidence.** Decide H1–H5 and record the decision. This is a
   prerequisite for anything that touches task construction, and it costs one reading
   session. If H1/H2 are accepted, plan a per-unit validation step rather than a
   global constant.
2. **Annotate the 100-task Train-R pilot with two independent reviewers.** Run
   `decision agreement`, identify the disagreeing categories, revise the guideline
   once, and freeze `annotation-guidelines-v2`. Re-review the pilot under the frozen
   version.
3. **Annotate the 400-task Dev evaluation sample** with two independent reviewers,
   adjudicate disagreements, and run `decision apply-annotations`.
4. **Re-run Group A on the same 400 tasks** with the same frozen configuration, then
   `decision business-baseline --references references.annotated.jsonl`. The run set
   from this milestone can be re-scored without a new API call, because the traces
   are complete — that is the cheapest path to a first real number.
5. **Have a human rate the 400 blind output packets** to populate `response_quality`,
   kept separate from decision correctness.
6. **Annotate the 300-task Train-R feedback sample.** Only adjudicated Train-R tasks
   can provide trustworthy RSI feedback, and it must be in place before RSI starts.

Do **not** start Flowchart Generation (Group B) or RSI (Group C) before step 4
produces a trustworthy Group A number. Optimising against an unmeasured baseline
would produce an uninterpretable improvement.

### Explicitly not done in this milestone

Group B, Group C, flowchart generation, flowchart optimisation, RSI, customer
simulator, synthetic dialogue generation, simulated backends, fabricated tools,
fine-tuning, full Test evaluation, and any automatic channel mapping.

---

## Appendix A — Commands added in this milestone

```text
decision audit-identity          conversation identity + dataset integrity
decision audit-channels          channels 1-5 + human-review package
decision audit-business-sources  business reference inventory
decision sample                  reproducible annotation sampling plan
decision export-annotations      review-ready annotation packets
decision import-annotations      validated submission import + history
decision adjudication-worklist   tasks needing a human adjudicator
decision import-adjudications    validated adjudication import
decision agreement               pre-adjudication agreement statistics
decision apply-annotations       annotated reference artifact
decision export-output-review    blind agent-output review packets
decision import-output-review    human response-quality reviews
decision business-baseline       the Group A business baseline report
decision run --sample-plan       run exactly a saved sample
```

## Appendix B — Evidence index

| Claim | Evidence |
|---|---|
| Baseline snapshot reproduced | `decision audit-identity` → `baseline_matches: true`; report at `outputs/decision/reports/conversation_identity_audit.md` |
| Channel statistics | `docs/reports/channel_semantics_audit.md`; `outputs/decision/channel_review/channel_review_summary.csv` |
| Channel samples | `outputs/decision/channel_review/channel_{3,4,5}_samples.jsonl` |
| Mapping unchanged | `decisions/channels.py::assert_production_mapping_unchanged`; `tests/test_decisions_channels.py` |
| No authoritative business source | `decision audit-business-sources`; `data/decision/business_sources/inventory.json` |
| Annotation schema | `docs/annotation/annotation_schema.md`; `decisions/annotation.py` |
| Protocol | `docs/annotation/business_decision_annotation_protocol_v1.md` |
| Guideline | `docs/annotation/annotation_guidelines_v1.md` |
| Sampling plans | `data/decision/samples/*.json` + `.md` (seed, selection hash, strata) |
| Packet isolation | `data/decision/annotations/*/annotation_packets.manifest.json` (`hidden_continuation_exported: false`) |
| Agreement machinery | `decisions/agreement.py`; `tests/test_decisions_annotation.py` |
| Group A results | `outputs/decision/traces/20261125T164504Z-group-a-dev-eval/`; `docs/reports/group_a_business_baseline_report.md` |
| Prompt integrity | `decision evaluate --tasks-dir` → 400 checked, 0 mismatches, 0 leaks |
| No credential in artifacts | credential scan over 464 files → 0 occurrences |
| Simulator independence | `tests/test_decision_isolation.py` (AST scan + clean-interpreter run) |
| Test suite | `pytest -q` → 1,449 passed, 25 subtests; `ruff check` → clean |
