# Group A Business Baseline Report

| Field | Value |
|---|---|
| Run set | `20261125T164457Z-group-a-smoke` |
| Condition | A (no flowchart, no workflow) |
| Benchmark version | `0.2.0` |
| Dataset | `cm_outbound_v2` |
| Split | `dev` |
| Provider label | `configured-openai-compatible` |
| Model | `deepseek-flash` |
| Model parameters | `{"max_output_tokens": 900, "temperature": 0.2}` |
| Prompt version | `baseline-a-v1` |
| Evaluator version | `0.2.0` |
| Agent version | `0.1.0` |
| Config hash | `0baa38201da38b73e0f3aae5c960bab21e913f69ffc4aad11c55bf7589e243bc` |
| Independent sampling unit | conversation unit |

## A. Technical performance

| Metric | Value |
|---|---|
| Tasks executed | 3 |
| Request success rate | 1.0 |
| Valid output rate | 1.0 |
| Timeout rate | 0.0 |
| Latency p50 (ms) | 1471.0 |
| Latency p90 (ms) | 1997.0 |
| Tasks with token usage | 3.0 |
| Total tokens | 2095.0 |
| Failed executions | {'success': 3} |

## B. Business performance

Two evaluation claims are reported separately and are never averaged:

| Metric | Value | Denominator | Note |
|---|---:|---:|---|
| `decision_success` | not available | 0 | no_reference_provided_by_this_run |
| `expert_adjudicated_decision_success_rate` | not available | 0 | no_reference_provided_by_this_run |
| `policy_verified_decision_success_rate` | not available | 0 | no_policy_verified_reference_in_this_run |
| `branch_decision_accuracy` | not available | 0 | no_reference_provided_by_this_run |
| `required_step_recall` | not available | 0 | no_reference_provided_by_this_run |
| `required_step_precision` | not available | 0 | no_reference_provided_by_this_run |
| `policy_compliance` | not available | 0 | no_policy_verified_reference_in_this_run |
| `appropriate_clarification_rate` | not available | 0 | no_clarification_reference_in_this_run |
| `response_quality` | not available | 0 | no_reviewed_quality_rubric_applied_yet |

Structurally unavailable in this milestone:

- `tool_intent_accuracy` — unavailable_no_business_tool_definitions
- `business_task_completion_rate` — unavailable_no_business_backend

### Scoring status

| Fact | Value |
|---|---|
| Total executed | 3 |
| Scored | 0 |
| Unscored | 3 |
| Candidate (pending review) | 0 |
| Invalid reference | 0 |
| Scored fraction | 0.0 |
| Unscored reasons | `{"annotation_not_reviewed": 3}` |
| Expert-tier scored tasks | 0 |
| Policy-tier scored tasks | 0 |
| Both tiers | 0 |

## D. Statistical reporting

95% percentile intervals from a cluster bootstrap that resamples conversation units, not individual tasks.

| Metric | Point estimate | 95% CI | Units | Clusters |
|---|---:|---|---:|---:|
| `expert_adjudicated_decision_success_rate` | not available | not available | 0 | 0 |
| `policy_verified_decision_success_rate` | not available | not available | 0 | 0 |
| `branch_decision_accuracy` | not available | not available | 0 | 0 |
| `required_step_recall` | not available | not available | 0 | 0 |
| `required_step_precision` | not available | not available | 0 | 0 |
| `appropriate_clarification_rate` | not available | not available | 0 | 0 |
| `policy_compliance` | not available | not available | 0 | 0 |

## C. Error analysis

Tasks with at least one classified failure: 0

| Failure class | Count |
|---|---:|
| `wrong_business_decision` | 0 |
| `wrong_branch` | 0 |
| `missing_required_step` | 0 |
| `unnecessary_step` | 0 |
| `failure_to_request_necessary_information` | 0 |
| `unsupported_assumption` | 0 |
| `policy_violation` | 0 |
| `poor_customer_response` | 0 |
| `output_formatting_failure` | 0 |
| `unscorable_or_ambiguous_decision` | 0 |

## Pending dependencies

The business metrics above are `not available` for a concrete, unfinished reason:

- Human annotation of the evaluation sample has not been completed, so no business performance number can be reported.
- The expert-adjudicated tier needs two independent human annotations per task plus adjudication of disagreements.
- The policy-verified tier needs an authoritative China Mobile business rule set, which does not exist in this workspace.
- Response quality needs a blind human review of the agent outputs.

## Limitations

- The corpus carries no verified business-domain label, so no domain-stratified result exists. Only observable strata are reported.
- Speaker roles come from the research protocol; the corpus stores role=UNKNOWN for every record.
- No business backend or tool contract exists, so tool-intent accuracy and business task completion remain unavailable.
- Channels 3/4/5 are unresolved; units containing their speech are conservatively excluded from task cutoffs.
- Conversation identity is inferred from one delivered transcript unit, not confirmed by an external call record.

## Notes

- Technical performance below is real and measured. Business performance is explicitly NOT measured: reporting a rate from zero scored tasks would be a fabricated result.

## Integrity

This report is generated from the run-set traces, the private references and optional human output reviews. It contains aggregate measurements only: no conversation text, no prompt text and no credential.
