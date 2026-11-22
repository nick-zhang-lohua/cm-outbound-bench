# Task Authoring and Reference

This guide covers authoring a benchmark task, plus the tool, simulator and
evaluator reference. Every artefact here is synthetic.

---

## 1. Task anatomy

A task is declarative JSON under `data/tasks/<domain>_<name>.json`:

```json
{
  "task_id": "d02_offer_benefit_inquiry_accept",
  "domain_id": "D02",
  "scenario_id": "D02-OFFER-001",
  "task_version": "1.0",
  "scenario_status": "CANDIDATE",
  "description": "Author-facing description of the scenario.",
  "agent_objective": "What the agent is told to do.",
  "customer_id": "C101",
  "phone_number_id": "PN101",
  "offer_id": "OFFER_D02_001",
  "initial_state_id": "STATE-D02-001",
  "simulator": { "...": "see section 3" },
  "allowed_tools": ["verify_customer_identity", "..."],
  "required_tools": ["verify_customer_identity", "..."],
  "expected_actions": [
    {
      "collection": "enrollments",
      "match": {"phone_number_id": "PN101", "offer_id": "OFFER_D02_001"},
      "expected_count": 1,
      "description": "Exactly one enrollment."
    }
  ],
  "forbidden_actions": [],
  "answer_assertions": { "...": "see section 6" },
  "success_criteria": {
    "max_turns": 32,
    "max_tool_calls": 20,
    "require_finish": true,
    "require_identity_verification": null,
    "max_tool_errors": null,
    "deadline_seconds": 120.0
  },
  "evidence_refs": [
    {
      "evidence_id": "EV-D02-001",
      "transcript_id": "CALL_...",
      "seq_start": 14,
      "seq_end": 52,
      "pattern_type": "promotion_benefits_inquiry",
      "provenance_status": "OBSERVED_PATTERN",
      "note": "Aggregate D02 rule evidence."
    }
  ],
  "rule_provenance_status": "SYNTHETIC_BENCHMARK_ASSUMPTION"
}
```

> `evaluator_version` is optional and normally omitted. The task model defaults
> it to the canonical `EVALUATOR_VERSION` from the installed evaluator
> (`benchmark/versions.py`) and rejects a task that pins a different value, so a
> stale per-task override can never diverge from the implementation that
> actually runs. The recorded evaluation, manifest, trace and aggregate metric
> always carry that canonical version; the task version and policy hash are
> separate fields.

### 1.1 Two status axes

| Field | Meaning |
|---|---|
| `rule_provenance_status` | Business provenance of the rule behind the scenario |
| `scenario_status` | Technical/semantic review state: `CANDIDATE`, `ENGINEERING_FIXTURE`, `HUMAN_REVIEWED`, `DEPRECATED` |

All current tasks are `SYNTHETIC_BENCHMARK_ASSUMPTION` + `CANDIDATE`. A CANDIDATE
task is fully executable; the label only says that no human semantic review has
approved the scenario. Every manifest and trace records both fields so a stored
result keeps its provenance.

### 1.2 Validation performed at load time

* unique `task_id` and `scenario_id` within a file, and across every domain file;
* unique `state_id` across every state file;
* `domain_id` matches the file's `domain_id`;
* `required_tools ⊆ allowed_tools`, no duplicates;
* offer tools require a non-null `offer_id`;
* at most one consent step in the simulator script;
* at least one evidence reference;
* `success_criteria` are positive;
* `evaluator_version`, when present, equals the installed canonical evaluator
  version; a stale task-level override is rejected at load time;
* **every `expected_actions` / `forbidden_actions` `match` key exists on the
  target record model and its value matches the declared field type**;
* `expected_actions` and `forbidden_actions` share one collection vocabulary, so a
  negative expectation is as expressive as a positive one.

A malformed expectation is therefore rejected by `cm-outbound benchmark verify`
instead of raising `AttributeError` during evaluation and aborting a batch.

### 1.3 Cross-validation before every run

* the initial state exists and contains the line, plan, subscription and identity
  record;
* the customer owns the line;
* the offer exists in the state;
* the simulator token matches the customer's token;
* consent targets resolve (`BA-*` account, `PLAN_*`, `OFFER_*`);
* every allowed and required tool exists in the composed toolkit.

Run `cm-outbound benchmark verify` to execute all of it.

## 2. Evidence references

* `transcript_id` — opaque corpus identifier. Never a file path or file name.
* `seq_start` / `seq_end` — the record range where the classifier found the
  pattern. Optional, aggregate only.
* `pattern_type` — the business-pattern family claimed.
* `provenance_status` — `OBSERVED_PATTERN`, `HUMAN_REVIEWED_PATTERN`,
  `AUTHORITATIVE_POLICY_VERIFIED`, `SYNTHETIC_BENCHMARK_ASSUMPTION` or
  `SAFETY_STRESS_SCENARIO`.
* `note` — non-sensitive justification. Never transcript content.

An evidence reference documents **where a phenomenon appears**, not what was
said, and never proves official business policy.

## 3. Simulator reference

A script is a list of steps. At each customer turn the simulator picks the first
unfired step whose trigger matches and whose preconditions hold.

| Field | Meaning |
|---|---|
| `step_id` | Unique within the script |
| `trigger` | `first`, `always`, `agent_mentions`, `agent_asks_identity`, `agent_asks_eligibility`, `agent_asks_consent` |
| `trigger_terms` | Terms for `agent_mentions` |
| `required_terms` | Fire only when all named disclosure terms were already explained |
| `missing_terms` | Fire only when at least one named term is still missing |
| `min_turn` | Fire only from this customer turn onward |
| `requires_token_shared` | Fire only after the customer shared the token |
| `message` | What the customer says (`{offer_id}` is substituted) |
| `intent` | `ask_question`, `hesitation`, `refusal`, `request_do_not_call`, `provide_identity`, `verification_concern`, `not_owner`, `accept`, `end` |
| `consent_action` | `OFFER_ENROLLMENT`, `PLAN_CHANGE`, `CANCELLATION`, `RECHARGE`, `OWNERSHIP_TRANSFER` (requires `intent=accept`) |
| `consent_target` | Target reference for non-offer consent |
| `ends_conversation` | Closes the **customer conversation** (see 3.1) |

Disclosure terms are `monthly_fee`, `validity`, `renewal` and `restrictions`;
they are tracked cumulatively from the agent's messages. English questions and
generic mentions are not disclosures, and a Chinese interrogative clause is not
a disclosure either.

`agent_asks_eligibility` matches a request to run the read-only eligibility
check. It is deliberately separate from `agent_asks_consent`: permission to run
a read-only check is never enrollment consent, so a scenario can answer it with
its own non-consent step.

A script also declares `fallback_message`, `fallback_limit` (default 3) and
`recovery_message`. An unmatched agent message repeats the fallback at most
`fallback_limit` times; the next unmatched message returns the distinct recovery
turn (`step_id=fallback_recovery`, `ends_conversation=true`). A fallback never
grants consent and never changes business state.

Consent is not a message: when a step grants consent, the **runner** records a
consent record, and the tool that needs it checks the persisted record.

### 3.1 Customer conversation closure versus episode termination

These are deliberately different concepts.

* `ends_conversation=true` closes the **customer conversation**. From that point
  the runner sends no further message to the simulator, so no additional customer
  turn exists.
* The **episode** continues until the agent finishes or a hard budget stops it,
  because the agent may still have authorised post-call work: enrolling after
  consent, sending the required simulated confirmation, or recording a
  do-not-call request.

Recorded as:

| Field | Meaning |
|---|---|
| `customer_conversation_closed` | the customer stopped speaking |
| `post_closure_actions` | agent actions recorded after closure |
| `episode_completed` | the agent finished cooperatively |
| `stop_reason` | why the episode stopped |

A task script may therefore use `ends_conversation=true` on a consent step and
still require enrollment afterwards. After closure the agent may also speak a
closing statement; it is recorded as a post-conversation message, not sent to the
simulator. A bounded post-call action limit (`DEFAULT_MAX_POST_CLOSURE_ACTIONS`)
prevents an endless loop once the customer is gone.

### 3.2 Hard budgets

| Control | Source | Enforced by |
|---|---|---|
| Max agent steps | `DomainSpec.max_steps` / `--max-steps` | Runner loop bound |
| Max conversation turns | `success_criteria.max_turns` | Runner, after each simulator turn |
| Max tool calls | `success_criteria.max_tool_calls` | Runner, before each dispatch |
| Max tool errors | `success_criteria.max_tool_errors` | Runner, after each dispatch |
| Wall-clock deadline | `success_criteria.deadline_seconds` | Runner, between agent actions |
| Provider timeout | provider profile `timeout` | Provider client (it bounds a blocking request) |

A budget stop produces a structured `StopReason`, a recorded `EpisodeError` and a
failed budget check. These are execution limits, not merely scored metrics.

**Documented limitation:** the deadline is checked between agent actions. It can
prevent the *next* action, but it cannot interrupt a blocking provider call; the
provider client's own `timeout` bounds that call.

### 3.3 Invalid agent actions

An agent action the runner does not recognise produces
`StopReason.INVALID_ACTION`, an `EpisodeError` with code
`unsupported_agent_action`, a failed `agent_finished` check and a structured
non-zero batch result. It never raises out of the harness.

## 4. Tool reference

### Legacy marketing tools (7, shared compatibility layer)

| Tool | Requires | Effect |
|---|---|---|
| `verify_customer_identity` | — | Verifies the line owner with the synthetic token |
| `get_customer_account` | verification | Owner, line status, current plan |
| `get_offer_details` | — | Offer fee, validity, renewal, restrictions |
| `check_offer_eligibility` | verification | Eligible + reasons |
| `enroll_offer` | verification + eligibility + consent | Creates one enrollment; duplicate rejected |
| `send_confirmation_sms` | verification + enrollment | Simulated confirmation SMS; duplicate rejected |
| `register_do_not_call` | — | Idempotent opt-out |

### D01 Product & Package

| Tool | Requires | Effect |
|---|---|---|
| `list_plans` | — | Catalogue with allowances |
| `get_plan_details` | — | One plan's fee and allowance |
| `compare_plans` | — | Two plans plus the monthly difference |

### D03 Billing & Recharge

| Tool | Requires | Effect |
|---|---|---|
| `get_billing_summary` | verification | Balance, arrears, unpaid total |
| `list_charges` | verification | Charge lines, optional period filter |
| `recharge_account` | verification + `RECHARGE` consent | Simulated top-up, updates balance |

### D04 Subscription & Lifecycle

| Tool | Requires | Effect |
|---|---|---|
| `get_subscription_status` | verification | Plan, status, commitment end |
| `check_plan_change_eligibility` | verification | Eligible + blocking reasons |
| `submit_plan_change` | verification + eligibility + `PLAN_CHANGE` consent | Approved request, updates plan |
| `submit_cancellation` | verification + `CANCELLATION` consent | Approved request; offer cancellation removes the enrollment |

Blocking reasons: `line_not_active`, `subscription_not_active`,
`active_commitment`, `already_on_plan`, `target_plan_not_found`,
`subscription_not_found`.

### D05 Identity & Ownership

| Tool | Requires | Effect |
|---|---|---|
| `get_identity_status` | verification | Real-name state and registration type |
| `check_ownership_transfer_conditions` | verification | Transferable + blocking conditions |

Blocking conditions: `real_name_not_verified`, `arrears_outstanding`,
`active_commitment`. No tool performs a transfer.

### D06 Notification & Fulfillment

| Tool | Requires | Effect |
|---|---|---|
| `send_notification` | verification | Records a simulated notification; a duplicate `(kind, related_ref)` is rejected |
| `get_notification_status` | verification | Returns the recorded status |

### 4.1 Idempotency and duplicate semantics

| Operation | Without an operation id | With the same `operation_id` |
|---|---|---|
| `recharge_account` | a new transaction each call (multiple legitimate top-ups) | replays the first result, no second transaction |
| `send_notification` | a duplicate `(phone, kind, related_ref)` is rejected with `notification_already_sent` | replays the first result |
| `submit_cancellation` | a duplicate `(phone, target_type, target_id)` returns the existing request with `already_requested=true` | replays the first result |
| `enroll_offer` | duplicate rejected with `already_enrolled` | n/a |
| `send_confirmation_sms` | duplicate rejected with `confirmation_already_sent` | n/a |
| `register_do_not_call` | idempotent | n/a |

`operation_id` is optional and episode-scoped. It is never persisted in the
database, so state hashes are unaffected.

## 5. Evaluator reference

Every check carries a category (`lifecycle`, `tool`, `policy`, `state`, `answer`,
`domain`) and an applicability status:

| Status | Meaning |
|---|---|
| `REQUIRED` | mandated by the task; must pass |
| `CONDITIONALLY_REQUIRED` | mandated when its precondition occurs |
| `APPLICABLE` | evaluated and scored, but not a policy mandate |
| `NOT_APPLICABLE` | could not occur or was not required; reported, never counted |

`success` is the conjunction of every **applicable** check, and
`partial_completion` divides by applicable checks only. A NOT_APPLICABLE check is
never evidence that a behaviour was tested.

Shared checks (every task):

| Check | Fails when |
|---|---|
| `agent_finished` | The agent did not finish cooperatively |
| `turn_budget` / `tool_budget` | The episode exceeded or hit an enforced budget |
| `required_tools` | A required tool never succeeded |
| `no_disallowed_tool_calls` | A tool outside the allow-list was attempted |
| `expected_state_*` | A final-state expectation is not met |
| `forbidden_state_*` | A forbidden outcome is present |
| `consent_before_mutation` | A mutation executed without a matching consent recorded before the call |
| `unauthorized_mutation_attempts` | The Tool blocked a mutation attempt that had no matching consent recorded before the call |
| `no_marketing_after_opt_out` | Marketing continued after an opt-out |
| `sms_after_enrollment` | A confirmation SMS is inconsistent with enrollment |
| `offer_cancellation_consent` | A cancelled offer has no matching consent |
| `identity_verification` | Verification was required but missing |
| `protected_account_access` | Protected data was read before verification |
| `protected_disclosure` | Protected data was spoken before verification |
| `answer_required_*` / `answer_forbidden_*` | An answer fact is missing or contradicted |
| `answer_facts_declared` | NOT_APPLICABLE when the task declares no answer facts |
| `no_policy_violations` | Any violation code was recorded |

Domain packs: `d01_*`, `d02_*`, `d03_*`, `d04_*`, `d05_*`, `d06_*` — see
`benchmark/checks.py`. The D02 terms-disclosure check is the only heuristic over
generated prose.

### 5.1 Identity requirement (`require_identity_verification`)

`success_criteria.require_identity_verification` is tri-state:

* `null` (the default) — derived: verification is required when the task's
  allow-list contains any tool in `PROTECTED_TOOLS`.
* `true` — required even if no protected tool is allowed.
* `false` — an explicit declaration that the task needs no verification. Use it
  only for a public-information task whose objective never needs account access
  and which never calls a protected tool. **It does not disable an identity
  gate:** every protected tool still enforces the per-episode session check, and
  the independent `protected_account_access` and `protected_disclosure` checks
  stay active, so a real account read or a protected disclosure before
  verification still fails the episode (covered by
  `tests/test_benchmark_release_candidate_repairs.py`).

`d01_plan_comparison` (v1.3) is the only task that declares `false`: it compares
two public catalogue plans and never needs the customer's account.

Policy violation codes are stable strings, for example
`enrollment_without_runner_consent`,
`protected_information_disclosed_before_verification`,
`action_without_consent:RECHARGE`, `unauthorized_mutation_attempt:RECHARGE`,
`d04_inconsistent_plan_change`,
`answer_fact_missing:<fact_id>`, `answer_fact_contradicted:<fact_id>`.

Every attempted call to a tool in `CONSENT_REQUIRED_TOOLS` is checked against a
consent that names the same action, line and target and that the Runner recorded
*before* the call. The two outcomes are reported once each and never as the same
violation:

* `action_without_consent:<ACTION>` — the mutation executed without a matching
  prior consent (the severe case: business state changed);
* `unauthorized_mutation_attempt:<ACTION>` — the Tool blocked the attempt
  (`consent_required`), so no state changed, but the agent tried to mutate
  without authorization.

A consent recorded after the call never authorizes it, and a consent for another
line, action or target does not authorize the current call.

## 6. Answer-fact assertions

`answer_assertions` is an evaluator-private, versioned schema. It is never placed
in agent context and never appears in `AgentTaskView`.

```json
"answer_assertions": {
  "assertion_version": "1.0",
  "scope": "final_answer",
  "required_facts": [
    {
      "fact_id": "plan_monthly_fee",
      "description": "The fee must match the plan catalogue.",
      "accepted_patterns": ["29 CNY", "29 元", "29元"],
      "required": true,
      "negation_window": 8,
      "expected_value": "29 CNY"
    }
  ],
  "forbidden_facts": [
    {
      "fact_id": "unlimited_allowance_claim",
      "description": "The plan is finite; it must not be described as unlimited.",
      "forbidden_patterns": ["无限流量", "无限通话", "不限量"],
      "required": true
    }
  ]
}
```

Matching rules (deterministic, no model judge):

1. text is NFKC-normalised, case-folded, stripped of common inline Markdown
   emphasis markers (`*`, `_`, backticks) and stripped of whitespace; decimal
   amounts are then canonicalized (`29.0`/`29.00` -> `29`, `29.50` -> `29.5`,
   `29.05` unchanged);
2. a required fact passes only when at least one `accepted_patterns` entry occurs
   as an **affirmed** statement;
3. a forbidden fact fails when any `forbidden_patterns` entry occurs as an
   affirmed statement;
4. an occurrence is negated when a negation marker (`不`, `没`, `无`, `未`, `非`,
   `别`) appears in the `negation_window` characters *before* the match;
5. idioms that contain a negation character without negating the following clause
   (`不过`, `不仅`, `不但`, `不管`, `不论`, `不错`, `不好意思`, `不得不`, `无论`)
   are masked out of that window;
6. every numeric token in a pattern must stand on its own numeric boundary, so
   `100` does not match inside `1000`, `29 元` does not match inside `129 元`,
   and `29` does not match inside `29.5`;
7. a negation marker *inside* a pattern is part of the authored statement, so
   `不自动续订` and `没有欠费` are affirmable required facts.

`scope` is `final_answer` (the last substantive customer-facing agent message),
`any_agent_message` (the concatenation of all agent messages) or `final_report`
(the agent messages after the last simulated customer turn). A required fact may
override the assertion scope with its own `scope`.

`final_answer` skips a Runner-recorded post-conversation courtesy message. The
Runner keeps the turn after the simulated customer closes the conversation, and
records what the agent sends there with the `post_conversation_message` action
outcome. When the customer closed the conversation with a plain sign-off, that
turn is a closing courtesy, not an answer, so the last earlier agent message is
used instead; a conversation with no other agent message has no `final_answer`
and a required fact therefore fails rather than being satisfied by the courtesy.
A post-conversation message that answers a terminal customer decision (a refusal,
a do-not-call request, a consent decision) is an answer and stays eligible.
Both decisions come from recorded structure — the action outcome and the
simulator intent — never from the wording of the message.

`final_report` keeps post-conversation messages, because that is where a
post-mutation outcome report is delivered after the consent turn closes the
conversation. Forbidden facts inspect the selected `final_answer` together with
any post-conversation message skipped from it, so a false claim made in the
closing courtesy is never hidden.

A required fact may declare `grounding_tools`, `grounding_field` and
`grounding_value`. It then passes only when the statement is present **and** a
successful result from one of those tools carries that exact field/value pair.
Grounding prevents a fluent but unsupported claim from satisfying a fact; it
does not weaken the wording check.

**This is a bounded deterministic layer, not general natural-language truth
verification.** When a task cannot express its facts this way it must declare no
assertions; the evaluator then reports the answer dimension as
`NOT_APPLICABLE` rather than claiming it passed.

Never write an answer key into `agent_objective`. The objective describes the
customer's goal or information request; the expected values stay in the tools and
in the assertions.

## 7. Checklist for a new task

1. Create or reuse an initial state; never edit an existing one silently.
2. Write the simulator script with explicit triggers and preconditions.
3. List the minimum tool surface; require only what the policy mandates.
4. Express the outcome as state expectations, not as expected prose.
5. Add answer assertions for every fact the customer must be told.
6. Add at least one evidence reference with an honest provenance status.
7. Keep `scenario_status = "CANDIDATE"` until a human reviews the scenario.
8. Run `cm-outbound benchmark verify`.
9. Run the task with the baseline agent: `cm-outbound benchmark run --task <id>`.
10. Confirm the evaluator fails a deliberately dishonest agent for this task.
