# Annotation Guidelines v1

| Field | Value |
|---|---|
| Guideline version | `annotation-guidelines-v1` |
| Status | **draft pending first pilot** — may be revised once, then frozen |
| Governs | `AnnotationSubmission` records applied to `PrivateTaskReference` |
| Companion documents | `business_decision_annotation_protocol_v1.md`, `annotation_schema.md` |
| Applies to splits | Train-F, Train-R, Dev (never Test) |

This document tells an annotator how to turn a visible conversation prefix into a
structured expected-decision reference. It assumes the protocol's isolation rules are
already in force.

---

## 1. The judgement you are making

You are the customer-service expert. Given what the customer has said up to this
point, what should the agent do next, and say next?

Three rules come before everything else.

1. **Do not describe history.** You are not asked what the historical agent did. That
   information is withheld on purpose.
2. **Do not invent policy.** If you do not know the actual China Mobile rule, you may
   still judge what a competent agent would do, but you must not assert a specific
   prohibition, price, eligibility condition or entitlement. Mark your evidence class
   honestly (§6).
3. **Do not force one answer.** If two responses are both professionally defensible,
   record the better one as primary and the other as an acceptable alternative.

---

## 2. Step 1 — Classify answerability

Read the prefix once without deciding. Then pick one:

### `sufficient_information`

The customer has given everything needed to choose the next action. Example shape:
the customer clearly declines the offer after the terms were explained.

### `more_information_required`

A specific, nameable fact is missing, and the correct next move is to ask for it.
Example shape: the agent needs to know whether the person speaking is the registered
line owner before discussing the account.

You must then fill `expected_clarification` with the information that has to be
requested. Write one item per fact, in the annotator's own words, for example
`whether the respondent is the registered line owner`.

### `ambiguous`

Two readings are equally defensible and lead to *different* decisions, or the
transcript quality makes the customer's intent unreadable. Do not use this to avoid a
hard judgement; use it when the evidence genuinely does not decide.

### `unscorable`

The prefix is off-topic, contradictory, mostly inaudible markers, or a recording
artifact (for example two colleagues talking before the call connects). Nothing
business-relevant can be judged.

`ambiguous` and `unscorable` Tasks leave every business denominator. They remain in
the dataset with their reason.

---

## 3. Step 2 — Choose the decision family

Pick exactly one family for the primary decision. The family is the coarse label used
for agreement statistics, so use it consistently.

| Family | Use when the next move is… |
|---|---|
| `gather_information` | to obtain a fact needed before any business action; includes ownership and identity confirmation |
| `explain_terms` | to describe what the current plan, offer, charge or condition is |
| `confirm_understanding` | to check that the customer has understood before proceeding |
| `obtain_consent` | to get an explicit agreement before an enrolment or change |
| `execute_change` | to carry out a requested, already-authorised change |
| `handle_refusal` | to accept a refusal and close politely, or to offer one permitted alternative |
| `handle_objection` | to address a stated concern without inventing new information |
| `escalate` | to hand off to a specialist process the agent cannot complete |
| `close_conversation` | to end the call after the business is settled |
| `unclear` | the evidence does not support any family; this always pairs with `ambiguous` or `unscorable` |

A family is not a script. Two agents using `explain_terms` may word it completely
differently and both be correct.

---

## 4. Step 3 — Write the decision

### 4.1 Primary decision

Fill:

* `decision_id` — a stable short id you choose, for example `D-primary`;
* `label` — a short imperative label, for example `confirm line ownership before
  discussing the account`;
* `decision_family` — from §3;
* `description` — one or two sentences in your own words;
* `expected_customer_response_intent` — what the response is trying to achieve, not
  its exact wording.

### 4.2 Acceptable alternatives

Add an entry for every response that is **also** defensible in this context. Leave
the list empty when only one response is appropriate. Do not pad the list: an
alternative that a competent reviewer would reject is not acceptable merely because
it is not prohibited.

Guiding test: *would you accept this answer from a well-trained colleague without
correcting it?* If yes, it is an acceptable alternative. If you would coach them
afterwards, it is not.

### 4.3 Conditional decisions

When the correct decision depends on a fact not yet established, encode it:

* `branch_conditions` — one entry per condition, each with a `condition_id`, a
  `description`, the `expected_branch` label, and any `required_information`;
* `expected_branch` — set only when the visible prefix already establishes which
  branch applies.

Do not invent a condition to look thorough. A branch that the prefix cannot resolve
belongs in `expected_clarification`, not in `branch_conditions`.

---

## 5. Step 4 — Required, optional and prohibited steps

### Required steps

A required step is a behaviour without which the decision is incomplete, whatever the
wording. Write one short phrase per step.

Typical required steps in this corpus:

* confirm the respondent's relationship to the registered line owner before
  discussing the account;
* state the concrete charge or benefit before asking for agreement;
* state when a change takes effect;
* state how the customer will be told the outcome (for example a confirmation SMS);
* ask whether the customer wants to proceed before executing.

Do **not** list a required step for something every agent always says. "Greets the
customer" is not a business requirement.

### Optional steps

Behaviours that improve the response but are not necessary for correctness. Record
them so the guideline can later decide whether they should become required.

### Prohibited steps

An action the agent must not take. **A prohibited step requires a real rule.** Write
it only when you can point to a rule or a citation:

* a real China Mobile policy document, or
* a verified business instruction recorded in `rule_citations`.

If you only *believe* something must not be done, do not record it as prohibited.
Record it as a required step in the positive form instead (for example "state the
charge before asking for consent" rather than "never conceal the charge").

The schema enforces this: a submission with `prohibited_steps` and no
`verified_business_rules` or `rule_citations` is rejected at validation.

---

## 6. Step 5 — Evidence and confidence

### Evidence class

Set one evidence class per rule citation:

| Class | Use when |
|---|---|
| `official_policy` | You are quoting an official policy document that exists in the approved material |
| `verified_business_knowledge` | You are applying approved operational knowledge the business has confirmed |
| `expert_consensus` | You and a second expert agree on professional grounds, with no document |
| `historical_observation_only` | You are recording what the historical agent did — **never** the sole basis for a scored decision |

In this workspace no authoritative source exists, so most submissions will be
`expert_consensus`. Say so; do not label a judgement `official_policy` to look
stronger.

### Evidence references

Fill `evidence_refs` with the concrete things you relied on:

* `visible_turn:<n>` — the n-th visible turn;
* `source:<source_id>` — an inventory source, when one exists;
* `rule:<rule_id>` — a cited rule.

An empty `evidence_refs` makes the submission invalid, because an unjustified
annotation cannot be adjudicated.

### Reviewer confidence

A number from `0.0` to `1.0`.

| Range | Meaning |
|---|---|
| 0.9–1.0 | You would defend this without hesitation |
| 0.7–0.9 | Confident, minor ambiguity |
| 0.5–0.7 | Defensible but a colleague could reasonably differ |
| below 0.5 | You are guessing; classify `ambiguous` instead |

Low confidence is useful information. Do not inflate it.

---

## 7. Step 6 — Front-load the form

Fill the blank form in this order, because each field constrains the next:

1. `task_answerability`;
2. `expected_clarification` (only for `more_information_required`);
3. `primary_expected_decision`;
4. `acceptable_alternative_decisions`;
5. `branch_conditions` and `expected_branch`;
6. `required_steps`, `optional_steps`, `prohibited_steps`;
7. `evidence_refs`, `rule_citations`, `reviewer_confidence`;
8. `notes` — anything a reviewer needs to reproduce your reasoning.

---

## 8. Consistency rules the validator enforces

| Rule | Reason |
|---|---|
| `ambiguous` / `unscorable` must not define a primary decision | A label on an undecidable Task would be noise in every denominator |
| `sufficient_information` must not expect a clarification | The two are contradictory |
| `more_information_required` must name the missing facts | Otherwise the clarification metric cannot be computed |
| Prohibited steps need a real rule | Prevents an invented prohibition |
| `expected_branch` must appear in `branch_conditions` | Prevents a dangling branch |
| Evidence references must be non-empty | Prevents an unjustified annotation |
| Provenance is mandatory | Distinguishes human review from a model proposal |
| Every citation must be policy-grade to count as policy evidence | Prevents `expert_consensus` from being reported as policy compliance |

---

## 9. Handling specific situations

### The customer refuses without a stated reason

Record `handle_refusal`. Do not record "ask why they refused" as required unless the
conversation genuinely needs the reason to proceed. Asking once is good practice;
demanding a reason after an explicit refusal is not.

### The person speaking may not be the line owner

`gather_information` with `required_steps` = confirm the relationship to the owner.
Do not assume the speaker is the owner and do not assume they are not.

### The customer asks a factual question the prefix cannot answer

`more_information_required` when the agent must look it up or ask a clarifying
question. Record the fact in `expected_clarification`. Do not guess the answer.

### The offer terms were never stated

A decision to enrol before the terms are stated is incomplete. Either record
`explain_terms` then `obtain_consent` as two required steps under a single decision,
or record `gather_information` if a missing fact blocks the explanation.

### The customer is hostile or threatens to complain

`handle_objection` or `close_conversation`. Record a required step that keeps the
response professional. Do not record "argue back" and do not invent a retention
policy.

### The prefix contains an unexplained third speaker

If the visible prefix includes speech from a channel whose meaning is unresolved, the
Task is normally not in any sample because task construction rejects such cutoffs. If
you meet one, classify it `ambiguous` and note it, rather than guessing that speaker's
role.

---

## 10. Worked examples (schematic)

These are **schematic shapes**, not transcripts, and not China Mobile policy.

### Example A — clear refusal after terms were explained

* Answerability: `sufficient_information`
* Primary: `handle_refusal`, family `handle_refusal`
* Required steps: acknowledge the refusal; close politely
* Alternatives: offer one clearly permitted alternative, if one exists in the
  approved material
* Prohibited: none recorded (no rule)
* Evidence: `visible_turn:6`, class `expert_consensus`
* Expected: the agent accepts the refusal and ends the call without repeating the
  pitch

### Example B — ownership not established

* Answerability: `more_information_required`
* Expected clarification: `whether the respondent is the registered line owner`
* Primary: `gather_information`, family `gather_information`
* Required steps: ask the ownership question before any account detail is disclosed
* Evidence: `visible_turn:3`, class `expert_consensus`
* Expected: the agent asks the ownership question and does not disclose account
  information first

### Example C — branch on an unresolved eligibility fact

* Answerability: `more_information_required`
* Expected clarification: `whether the line is currently in arrears`
* Primary: `gather_information`
* Branch conditions: `if in arrears → handle_objection with a payment path`;
  `if not in arrears → confirm_understanding`
* `expected_branch`: empty, because the prefix does not establish it

### Example D — recorded announcement, no judgement possible

* Answerability: `unscorable`
* Primary: none
* Note: `prefix is a ring-back announcement; no customer turn requiring a business
  decision`

---

## 11. Freezing and revision

1. The pilot is annotated with this draft.
2. Agreement is computed (`decision agreement`) and reviewed.
3. Any statistic below `0.60`, or any disagreement category that recurs, triggers a
   guideline revision.
4. The guideline is frozen as `annotation-guidelines-v2` (or v1 if no change is
   needed) and the pilot annotations are **re-reviewed under the frozen version**
   before they enter final statistics.
5. After freezing, changes require a new version, and old submissions are never
   silently re-labelled.

A submission is only valid for the guideline version it names. Import rejects a
mismatched version unless the operator explicitly acknowledges the mismatch, and the
mismatch is then recorded.

---

## 12. What to do when these guidelines do not cover a case

1. Classify honestly with the closest answerability value.
2. Record the case in `notes` and raise it to the guideline owner.
3. Do not invent a rule, a family or a prohibition to make the form fit.

An uncovered case is a guideline defect, not an annotator error. It is exactly the
input the first revision needs.
