# Business Decision Annotation Protocol v1

| Field | Value |
|---|---|
| Protocol version | `annotation-protocol-v1` |
| Guideline version it governs | `annotation-guidelines-v1` |
| Schema version | `annotation-schema-1.0` |
| Applies to | Train-F, Train-R and Dev splits |
| Does **not** apply to | Test (separate locked process) |
| Implementation | `src/cm_outbound_bench/decisions/annotation.py`, `annotation_io.py`, `agreement.py`, `sampling.py` |

This protocol defines how a decision-point Task becomes a scored business reference.
It exists because a technically executable benchmark is not a business evaluation:
the Agent must be judged against expected decisions that an independent reviewer has
established and a second reviewer has reviewed.

---

## 1. What is being annotated

A Task is one **decision point** cut from a real de-identified outbound conversation:
the visible prefix ends at the last customer utterance, and the historical agent's
next response is withheld. The annotator decides what the agent *should* do next.

The annotator receives:

* the visible conversation prefix, role-labelled `客户` / `客服`;
* the task instruction;
* the approved business reference material, if any exists;
* a blank annotation form with enumerated fields.

The annotator never receives:

* the historical agent's next response;
* any later customer turn;
* the final outcome of the historical call;
* any Group A, B or C model output;
* any other annotator's annotation;
* an LLM-generated suggestion, during the first independent pass.

Isolation is enforced by the exporter, not by convention: the packet is built from
the agent-visible task object only, and
`annotation_io.FORBIDDEN_PACKET_KEYS` is checked against every packet before it is
written.

---

## 2. Order of work (non-negotiable)

1. The annotator reads only the packet.
2. The annotator records an independent annotation.
3. A second annotator annotates the same Task independently.
4. Disagreements go to a designated adjudicator who is neither annotator.
5. Only a human-adjudicated record becomes eligible for scoring.

`ANNOTATION_ORDER` in `decisions/annotation.py` states the same sequence in code so
the rule is not only in this document.

### Why the order matters

Historical agent behaviour is evidence of what happened, not proof of what should
have happened. If an annotator sees the historical reply, the annotation becomes a
description of history. If an annotator sees a model's answer first, the annotation
becomes a rating of that model. Both destroy the reference's independence.

---

## 3. Roles

| Role | Responsibility | Cannot do |
|---|---|---|
| Annotator A | Independent annotation of the assigned Tasks | See annotator B's work; adjudicate own Tasks |
| Annotator B | Independent annotation of the same Tasks | See annotator A's work; adjudicate own Tasks |
| Adjudicator | Resolve disagreements, record the category | Be one of the two annotators of that Task |
| Guideline owner | Maintain the guideline, freeze a version, act on poor agreement | — |
| Research engineer | Build packets, run validation, report agreement | Certify business Ground Truth |
| LLM (optional) | Produce `llm_proposed` candidate annotations | Count as a human annotation; adjudicate; become `scored` |

Each person is identified by a stable reviewer id recorded in every submission and
adjudication. No real name is required; a pseudonymous id is sufficient.

---

## 4. Two evaluation claims, kept apart

### Tier 1 — Expert-adjudicated business decision quality

The reviewer judges whether the next business decision is **reasonable and
appropriate** given the visible conversation and approved business knowledge, and
records every acceptable alternative.

Reported as `expert_adjudicated_decision_success_rate`.

This is a professional-judgement claim. It does **not** assert formal policy
compliance, and it must never be presented as if it did.

### Tier 2 — Policy-verified business correctness

The decision is supported by an official policy, a verified operation record or
authoritative business knowledge, with an explicit rule citation.

Reported as `policy_verified_decision_success_rate`.

**Current status: unavailable.** The business reference inventory
(`decision audit-business-sources`) found **no authoritative China Mobile policy or
verified operation record** in this workspace. Until one exists, no Task can enter
this tier and the metric must be reported as `not available`, never as zero.

### The two are never combined

One `decision_success_rate` covering both tiers would let a tier-1 judgement
masquerade as policy compliance. The evaluator keeps separate per-tier metric values
with separate denominators, and a report that shows only one populated tier says so
explicitly.

---

## 5. Task answerability

Every Task is classified before a decision is written.

| Value | Meaning | Scoreable |
|---|---|---|
| `sufficient_information` | The prefix contains everything needed to decide. | yes |
| `more_information_required` | The correct next move is to obtain a specific missing fact. | yes, if the missing items are recorded |
| `ambiguous` | Two or more readings of the conversation are equally defensible and lead to different decisions. | no |
| `unscorable` | The prefix is too damaged, off-topic or contradictory to judge. | no |
| `unknown` | Not yet classified. | no |

**A clarification request is a valid correct answer.** When the answerability is
`more_information_required`, the guideline requires the annotator to list the
information that must be requested in `expected_clarification`. The evaluator then
scores the Agent on whether it requests that information, and records the outcome
under `appropriate_clarification_rate`.

A Task classified `ambiguous` or `unscorable` leaves every business denominator. It
stays in the dataset with its reason so the exclusion is auditable.

---

## 6. Multiple correct decisions

Telecom customer-service conversations routinely admit more than one professional
response. The schema therefore supports:

* **one primary decision** — the annotator's preferred answer;
* **acceptable alternatives** — other answers that are equally defensible in this
  context;
* **conditional decisions** — a branch per condition, with the condition named;
* **clarification** — the answer is to ask, not to act;
* **prohibited action** — an action that a real rule forbids, with the rule cited.

Exact-text matching is never the correctness criterion. The evaluator compares the
Agent's stated next action, spoken response and requested information against the
normalised labels and required steps, and accepts any recorded acceptable decision.
See `docs/annotation/annotation_schema.md` §5 for the matching rule.

Prohibited actions require a verified business rule. The schema refuses a submission
that asserts a prohibition with no rule or citation behind it, because an invented
prohibition is a fabricated policy.

---

## 7. Evidence provenance

Every decision carries an evidence class:

| Class | Meaning | Can support tier 2 |
|---|---|---|
| `official_policy` | An official policy document states the rule. | yes |
| `verified_business_knowledge` | Approved operational knowledge from the business. | yes |
| `expert_consensus` | Two reviewers agree, with reasoning recorded. | **no** |
| `historical_observation_only` | "The historical agent did this." | **no** |

`historical_observation_only` is explicitly not evidence of correctness. It may be
recorded for diagnostics; it can never be the sole support for a scored decision.

---

## 8. Review, adjudication and status

| Stage | `annotation_status` | `adjudication_status` | Scorable |
|---|---|---|---|
| Packet exported, not started | `pending` | `pending` | no |
| One human annotation exists | `in_review` | `pending` | no |
| Two human annotations agree | `reviewed` | `not_required` | yes |
| Disagreement adjudicated | `adjudicated` | `adjudicated` | yes |
| Rejected at review | `rejected` | `rejected` | no |
| LLM proposal only | `in_review` | `pending` | **no**, `scoring_status = candidate` |

Two independent human annotations that agree structurally (same accepted decision
set, same required steps) are scored without a separate adjudication record. Any
disagreement requires the adjudicator. No code path resolves a disagreement
automatically, and no LLM is allowed to adjudicate.

---

## 9. Inter-annotator agreement

Agreement is computed **before** adjudication, from the independent submissions, and
reported separately from the final consensus.

| Statistic | When it applies | Implementation |
|---|---|---|
| Raw agreement | any number of raters ≥ 2 | `agreement.raw_agreement` |
| Cohen's kappa | exactly two raters, one categorical label | `agreement.cohen_kappa` |
| Krippendorff's alpha (nominal) | ≥ 2 raters, missing labels allowed | `agreement.krippendorff_alpha_nominal` |
| Set precision / recall / Jaccard | multi-label `required_steps` | `agreement.set_agreement` |

A statistic that is undefined for the data (one category only, fewer than two raters)
returns `None` with a reason. Literal `0.0` is never substituted for "undefined".

Threshold: `POOR_AGREEMENT_THRESHOLD = 0.60`. If any statistic falls below it, the
guideline owner must identify the disagreeing categories or scenarios and revise the
guideline before it is frozen. Poor agreement is never hidden by reporting only the
final consensus.

---

## 10. LLM-assisted pre-annotation (optional)

An LLM may produce candidate annotations to accelerate review. The conditions:

1. `provenance.authority` is `llm_proposed`, with `model_name` and `prompt_version`
   recorded (the schema refuses a model proposal without a model identity).
2. The candidate is marked `scoring_status = candidate`, never `scored`.
3. Human reviewers annotate **before** seeing any candidate.
4. A model candidate never counts as one of the two independent human annotations.
5. `PrivateTaskReference.is_scored` independently refuses any reference whose
   provenance is `llm_proposed`, so even a mis-configured applier cannot score it.

---

## 11. Test isolation

The Test split is never annotated through this workflow.

* `export-annotations` refuses a plan whose split is `test`.
* The runner refuses a `test` run without an explicit `allow_test`.
* Test labels must not be used for prompt tuning, threshold choice, guideline
  experimentation, error-driven optimisation or RSI feedback.
* A separate locked annotation pass for Test is planned after the guideline is
  frozen. It is out of scope for Milestone 1.1.

---

## 12. Data handling

| Artifact | Location | Version control |
|---|---|---|
| Annotation packets | `data/decision/annotations/<purpose>/` | git-ignored |
| Submissions | `data/decision/annotations/current/` | git-ignored |
| Adjudications | `data/decision/annotations/current/` | git-ignored |
| Import history | `data/decision/annotations/current/history/` | git-ignored |
| Sampling plans | `data/decision/samples/` | git-ignored |
| Blind output-review packets | `data/decision/annotations/output_review/` | git-ignored |
| This protocol and the guideline | `docs/annotation/` | **tracked** |

Nothing committed to version control contains conversation text. Committed documents
contain rules, counts and process only.

---

## 13. Worked commands

```bash
export CM_OUTBOUND_CORPUS_DIR=../data/deidentified/cm_outbound_v2

# 1. Reproducible samples
uv run cm-outbound decision sample --purpose train_r_annotation_pilot --verify
uv run cm-outbound decision sample --purpose train_r_feedback_sample \
    --exclude-plan data/decision/samples/train_r_annotation_pilot.json --verify
uv run cm-outbound decision sample --purpose dev_baseline_evaluation_sample --verify

# 2. Review-ready packets
uv run cm-outbound decision export-annotations \
    --plan data/decision/samples/train_r_annotation_pilot.json

# 3. Import the two independent annotation rounds
uv run cm-outbound decision import-annotations --file annotator_a.jsonl
uv run cm-outbound decision import-annotations --file annotator_b.jsonl

# 4. Agreement (before adjudication)
uv run cm-outbound decision agreement

# 5. Adjudication worklist, then the adjudicator's decisions
uv run cm-outbound decision adjudication-worklist
uv run cm-outbound decision import-adjudications --file adjudications.jsonl

# 6. Build the annotated reference artifact
uv run cm-outbound decision apply-annotations

# 7. Group A on the scored sample
uv run cm-outbound decision run --agent live --split dev \
    --sample-plan data/decision/samples/dev_baseline_evaluation_sample.json
uv run cm-outbound decision business-baseline \
    --run-set outputs/decision/traces/<run_set> \
    --references data/decision/annotations/current/references.annotated.jsonl
```

Every step writes a JSON record with counts, and every step refuses to proceed on an
inconsistent input rather than repairing it silently.
