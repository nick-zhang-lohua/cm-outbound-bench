# Baseline run report — 20261125T155431Z-scripted-smoke

| Field | Value |
|---|---|
| Condition | A (no workflow, no flowchart) |
| Benchmark | 0.1.0 |
| Dataset | cm_outbound_v2 |
| Split | dev |
| Provider label | scripted |
| Model | none |
| Model parameters | `{}` |
| Prompt version | baseline-a-v1 |
| Evaluator version | 0.1.0 |
| Config hash | `` |

## Technical evaluation

| Metric | Value |
|---|---|
| Tasks executed | 6 |
| Request success rate | 1.0 |
| Valid output rate | 1.0 |
| Timeout rate | 0.0 |
| Latency p50 / p90 (ms) | 0.0 / 0.0 |
| Tasks with token usage | 0.0 |
| Total tokens | 0.0 |

## Business evaluation

| Metric | Value | Denominator |
|---|---|---|
| decision_success | not available | 0 |
| branch_decision_accuracy | not available | 0 |
| required_step_recall | not available | 0 |
| required_step_precision | not available | 0 |

Scoring status counts: `{"unscored": 6}`

Scored tasks: 0; unscored: 6; candidate: 0; invalid: 0

Unavailable metrics (no verified reference or no tool contract):

- `policy_compliance` — unavailable_no_verified_policy_reference
- `tool_intent_accuracy` — unavailable_no_business_tool_definitions
- `response_quality` — unavailable_no_reviewed_quality_rubric

## Notes

- decision_success_rate is not available: zero tasks have a verified reference. Unscored tasks are excluded from every business denominator, and the historical agent response is not treated as ground truth.
- No business backend is executed in this milestone, so business task completion and real tool execution success are out of scope.

## Integrity

This report is generated from the traces in the run-set directory. It contains aggregate measurements only: no conversation text, no prompt text and no credential.
