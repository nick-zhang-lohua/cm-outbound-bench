# Annotation Schema Reference

| Field | Value |
|---|---|
| Schema version | `annotation-schema-1.0` |
| Reference schema version | `2.0` (extended additively from `1.0`) |
| Guideline version | `annotation-guidelines-v1` |
| Implementation | `src/cm_outbound_bench/decisions/annotation.py` |

The annotation fields are **embedded in the existing private evaluator reference**
(`PrivateTaskReference`), not stored in a second data system. A reference written by
Milestone 1 still parses; every new field has a safe default that means "not
annotated", so an unannotated Task can never look scored.

---

## 1. Where each field lives

| Layer | Object | Purpose |
|---|---|---|
| Reviewer input | `AnnotationSubmission` | One reviewer's independent annotation of one Task |
| Final state | `PrivateTaskReference` | What the evaluator scores against |
| Adjudication | `AdjudicationRecord` | The resolved disagreement, with the adjudicator's decision |
| Evidence | `BusinessRuleCitation`, `BusinessSourceReference` | What the annotation rests on |

`AnnotationSubmission` → `AdjudicationRecord` → `PrivateTaskReference` is a one-way
flow. A reference is never edited directly by a reviewer.

---

## 2. `AnnotationSubmission`

| Field | Type | Required | Meaning |
|---|---|---|---|
| `task_id` | string | yes | The opaque Task id |
| `annotator_id` | string | yes | Stable pseudonymous reviewer id |
| `guideline_version` | string | yes | Must match the frozen version |
| `annotation_status` | enum | yes | `pending`, `in_review`, `reviewed` |
| `task_answerability` | enum | yes | See §3 |
| `primary_expected_decision` | `DecisionReference` | conditional | Required unless answerability is `ambiguous` / `unscorable` |
| `acceptable_alternative_decisions` | `DecisionReference[]` | no | Equally defensible answers |
| `branch_conditions` | `BranchCondition[]` | no | Conditional paths |
| `expected_branch` | string | no | Set only when the prefix resolves the branch |
| `required_steps` | string[] | no | Behaviours without which the decision is incomplete |
| `optional_steps` | string[] | no | Improvements, not requirements |
| `prohibited_steps` | string[] | no | Requires a real rule (§4) |
| `expected_clarification` | string[] | conditional | Required when answerability is `more_information_required` |
| `verified_business_rules` | string[] | no | Rule ids backing a prohibition |
| `rule_citations` | `BusinessRuleCitation[]` | no | Structured rule evidence |
| `evidence_refs` | string[] | **yes** | `visible_turn:<n>` / `source:<id>` / `rule:<id>` |
| `provenance` | `AnnotationProvenance` | **yes** | Who or what produced this annotation |
| `reviewer_confidence` | float 0..1 | **yes** | Honest self-assessment |
| `submitted_at` | string | no | ISO-8601 timestamp |
| `notes` | string | no | Anything needed to reproduce the reasoning |

### `DecisionReference`

| Field | Type | Meaning |
|---|---|---|
| `decision_id` | string | Stable short id within the Task |
| `label` | string | Short imperative label; **this is what agreement compares** |
| `decision_family` | string | Coarse family from the guideline §3 |
| `description` | string | One or two sentences |
| `expected_customer_response_intent` | string | Intent, not wording |
| `branch_conditions` | `BranchCondition[]` | Conditions attached to this decision |
| `required_steps` | string[] | Steps for this decision |
| `optional_steps` | string[] | Optional steps for this decision |
| `prohibited_steps` | string[] | Requires a rule |
| `rule_citations` | string[] | Rule ids |
| `evidence_refs` | string[] | Evidence for this decision |

### `BranchCondition`

| Field | Type | Meaning |
|---|---|---|
| `condition_id` | string | Stable id |
| `description` | string | The condition in words |
| `expected_branch` | string | The branch label taken when the condition holds |
| `required_information` | string[] | Facts that decide the condition |

### `AnnotationProvenance`

| Field | Type | Meaning |
|---|---|---|
| `authority` | enum | `llm_proposed`, `human_primary_annotation`, `human_secondary_review`, `human_adjudicated` |
| `method` | string | Free-form method note |
| `annotator_id` / `reviewer_id` | string | Who |
| `model_name` / `model_version` / `prompt_version` | string | **Required** when authority is `llm_proposed` |
| `guideline_version` | string | Version used |
| `created_at` | string | Timestamp |
| `source_refs` | string[] | Inventory source ids |

The model refuses an `llm_proposed` provenance without a model identity, so a model
proposal can never be anonymous.

---

## 3. Enumerations

### `task_answerability`

| Value | Scoreable | Meaning |
|---|---|---|
| `sufficient_information` | yes | Everything needed is present |
| `more_information_required` | yes | The correct move is to request a named fact |
| `ambiguous` | **no** | Equally defensible readings lead to different decisions |
| `unscorable` | **no** | Cannot be judged |
| `unknown` | **no** | Not yet classified (the default) |

### `verification_tier`

| Value | Meaning |
|---|---|
| `none` | No verified reference (default) |
| `expert_adjudicated` | Human appropriateness judgement with alternatives recorded |
| `policy_verified` | Backed by an official policy or verified business knowledge, with a citation |
| `expert_adjudicated_and_policy_verified` | Both claims hold |

### `annotation_status` / `adjudication_status`

See `business_decision_annotation_protocol_v1.md` §8.

### Evidence source

| Value | Supports the policy tier |
|---|---|
| `official_policy` | yes |
| `verified_business_knowledge` | yes |
| `expert_consensus` | no |
| `historical_observation_only` | no |

---

## 4. Validation rules

`validate_annotation_consistency()` returns the problems with one submission; the
importer rejects a submission whose list is non-empty.

| Rule | Message |
|---|---|
| `ambiguous` / `unscorable` must not define a primary decision | `ambiguous_or_unscorable_task_must_not_define_a_primary_decision` |
| `sufficient_information` must not expect a clarification | `sufficient_information_task_must_not_expect_a_clarification_request` |
| A primary decision must have a label | `primary_decision_has_no_label` |
| `expected_branch` must be defined by a branch condition | `expected_branch_not_defined_by_any_branch_condition` |
| A citation must be policy-grade to be counted as policy evidence | `rule_citation_<id>_is_not_policy_grade_evidence` |
| Confidence is required | `reviewer_confidence_missing` |
| Evidence is required | `evidence_refs_missing` |
| Provenance is required | `annotation_provenance_missing` |
| Prohibited steps require a rule | raised by the model validator at construction time |

---

## 5. The scoring rule

`PrivateTaskReference.is_scored` is the single gate. It is true only when **all** of
the following hold:

1. `scoring_status == "scored"`;
2. `annotation_status` is `reviewed` or `adjudicated`;
3. the annotation is not model-only — a reference whose `annotation_provenance`
   authority is `llm_proposed` is **never** scorable;
4. `task_answerability` is scoreable (or, for a legacy reference, the answerability
   field is still `unknown` and the legacy expected decisions exist);
5. at least one accepted decision, or an explicit `expected_clarification`, exists.

### Matching

For each accepted decision (primary plus alternatives) the evaluator computes:

* `branch_matched` — the decision family / expected branch label appears in the
  normalised `next_action` or `customer_response`;
* `step_matches` — each required step (union of reference-level and decision-level
  steps) appears in the action, the spoken response or the requested information;
* `step_recall` — fraction of required steps matched;
* `step_precision` — fraction of the Agent's `required_information` claims that
  correspond to a required step, when the Agent claims anything;
* `decision_matched` — branch matched and every required step matched.

The best-scoring acceptable decision is used. When the Task expects clarification,
success is instead whether the Agent requests the expected information.

Normalisation strips punctuation and whitespace and case-folds; matching is
containment, never exact equality. The criterion is the appropriateness of the
decision, never the reproduction of the historical wording.

---

## 6. Legacy compatibility

A reference written before this schema has:

```json
{"scoring_status": "scored", "annotation_status": "reviewed",
 "expected_decisions": [{"decision_id": "D1", "branch": "…", "required_steps": []}]}
```

and none of the new fields. It still parses, and:

* `effective_verification_tier` returns `expert_adjudicated`, because the legacy
  contract described those phrases as human-verified and there is no policy citation
  to justify a policy tier;
* `is_scored` remains true under exactly the conditions it had before;
* the evaluator emits `decision_success` and
  `expert_adjudicated_decision_success`, but never
  `policy_verified_decision_success`.

This is why existing run artifacts keep their meaning.

---

## 7. Storage

| Artifact | Filename | Default directory | Git |
|---|---|---|---|
| Annotation packets | `annotation_packets.jsonl` | `data/decision/annotations/<purpose>/` | ignored |
| Packet manifest | `annotation_packets.manifest.json` | same | ignored |
| Submissions | `annotation_submissions.jsonl` | `data/decision/annotations/current/` | ignored |
| Import report | `annotation_import_report.json` | same | ignored |
| Adjudication worklist | `adjudication_worklist.jsonl` | same | ignored |
| Adjudications | `adjudications.jsonl` | same | ignored |
| Annotated references | `references.annotated.jsonl` | same | ignored |
| Apply report | `annotation_apply_report.json` | same | ignored |
| Blind output-review packets | `agent_output_review_packets.jsonl` | `…/output_review/` | ignored |
| Human output reviews | `agent_output_reviews.jsonl` | `…/output_review/` | ignored |
| Import history | `history/import-<timestamp>.json` | same | ignored |

All of these contain de-identified conversation text or reviewer identity, so they
stay outside version control. Only the schema, protocol and guideline documents are
tracked.

---

## 8. Field mapping to the milestone specification

| Specification field | Implemented as |
|---|---|
| `task_id` | `PrivateTaskReference.task_id` |
| `annotation_status` | `PrivateTaskReference.annotation_status` |
| `scoring_status` | `PrivateTaskReference.scoring_status` |
| `task_answerability` | `PrivateTaskReference.task_answerability` |
| `primary_expected_decision` | `PrivateTaskReference.primary_expected_decision` |
| `acceptable_alternative_decisions` | `PrivateTaskReference.acceptable_alternative_decisions` |
| `branch_conditions` / `expected_branch` | same names |
| `required_steps` / `optional_steps` / `prohibited_steps` | same names |
| `verified_business_rules` | same name (ids) plus `rule_citations` (structured) |
| `evidence_refs` | same name |
| `annotation_provenance` | same name |
| `reviewer_confidence` | same name |
| `annotator_ids` | same name |
| `adjudication_status` | same name |
| `annotation_guideline_version` | same name |
| *(added)* | `verification_tier`, `expected_clarification`, `conversation_id`, `transcript_unit_id` |

The additions exist because the milestone requires (a) two separable evaluation
claims, (b) an explicitly scoreable clarification answer, and (c) research
terminology that does not overclaim a verified telephone call identifier.
