# Baseline run report — 20261125T164504Z-group-a-dev-eval

| Field | Value |
|---|---|
| Condition | A (no workflow, no flowchart) |
| Benchmark | 0.2.0 |
| Dataset | cm_outbound_v2 |
| Split | dev |
| Provider label | configured-openai-compatible |
| Model | deepseek-flash |
| Model parameters | `{"temperature": 0.2, "max_output_tokens": 900}` |
| Prompt version | baseline-a-v1 |
| Evaluator version | 0.2.0 |
| Config hash | `0baa38201da38b73e0f3aae5c960bab21e913f69ffc4aad11c55bf7589e243bc` |

## Technical evaluation

| Metric | Value |
|---|---|
| Tasks executed | 400 |
| Request success rate | 1.0 |
| Valid output rate | 1.0 |
| Timeout rate | 0.0 |
| Latency p50 / p90 (ms) | 1558.0 / 1935.0 |
| Tasks with token usage | 400.0 |
| Total tokens | 315360.0 |

## Business evaluation

| Metric | Value | Denominator |
|---|---|---|
| decision_success | not available | 0 |
| expert_adjudicated_decision_success_rate | not available | 0 |
| policy_verified_decision_success_rate | not available | 0 |
| branch_decision_accuracy | not available | 0 |
| required_step_recall | not available | 0 |
| required_step_precision | not available | 0 |
| policy_compliance | not available | 0 |
| appropriate_clarification_rate | not available | 0 |
| response_quality | not available | 0 |
| tool_intent_accuracy | not available | 0 |
| business_task_completion_rate | not available | 0 |

Scoring status counts: `{"unscored": 400}`

Scored tasks: 0; unscored: 400; candidate: 0; invalid: 0

Unavailable metrics (no verified reference or no tool contract):

- `decision_success` — no_reference_provided_by_this_run
- `expert_adjudicated_decision_success_rate` — no_reference_provided_by_this_run
- `policy_verified_decision_success_rate` — no_policy_verified_reference_in_this_run
- `branch_decision_accuracy` — no_reference_provided_by_this_run
- `required_step_recall` — no_reference_provided_by_this_run
- `required_step_precision` — no_reference_provided_by_this_run
- `policy_compliance` — no_policy_verified_reference_in_this_run
- `appropriate_clarification_rate` — no_clarification_reference_in_this_run
- `response_quality` — no_reviewed_quality_rubric_applied_yet
- `tool_intent_accuracy` — unavailable_no_business_tool_definitions
- `business_task_completion_rate` — unavailable_no_business_backend

## Notes

- decision_success_rate is not available: zero tasks have a verified reference. Unscored tasks are excluded from every business denominator, and the historical agent response is not treated as ground truth.
- No business backend is executed in this milestone, so business task completion and real tool execution success are out of scope.

## Integrity

This report is generated from the traces in the run-set directory. It contains aggregate measurements only: no conversation text, no prompt text and no credential.
