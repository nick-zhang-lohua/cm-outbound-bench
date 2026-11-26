# Group A LLM-Gold Business Baseline Report

| Field | Value |
|---|---|
| Condition | A (no flowchart, no workflow, no simulator) |
| Ground Truth version | cm-outbound-llm-gold-v1 |
| Ground Truth model | deepseek-v4-pro |
| Ground Truth type | llm_generated_gold (frozen) |
| Evaluation judge model | deepseek-v4-pro |
| Scoring prompt version | v1 |
| Evaluated agent run set | 20261125T164504Z-group-a-dev-eval |
| Evaluated agent model | deepseek-flash |
| Evaluated tasks | 400 |

## Primary results

| Field | Value |
|---|---|
| LLM-Gold Decision Success Rate (DSR) | 0.83376 |
| Decision Acceptance Rate (DAR) | 0.976982 |
| Partial Decision Rate (PDR) | 0.143223 |
| Incorrect Decision Rate (IDR) | 0.023018 |
| Scored Task Coverage | 0.9775 |
| Scored tasks | 391 |
| Unscorable tasks | 9 |

## Secondary metrics

| Field | Value |
|---|---|
| Required Step Precision | not available |
| Required Step Precision reason | frozen_gold_records_steps_as_actions_not_an_information_set |
| Critical Error Rate | 0.023018 |
| Response Quality | not available |
| policy_verified_decision_success_rate | not available |

## Lexical proxy metrics (diagnostic only, lower bound)

| Field | Value |
|---|---|
| Appropriate Clarification Rate | 0.15 |
| Required Step Recall | 0.07289 |
| Required Step Precision (lexical surrogate) | 0.001279 |
| Branch Decision Accuracy | 0.104478 |

- Clarification, required-step and branch metrics are lexical substring surrogates over free-text Chinese. They understate semantic compliance (a paraphrase scores zero) and are reported as lower-bound diagnostics, not as semantic accuracy.

Outcome distribution: `{"fully_correct": 326, "incorrect": 9, "partially_correct": 56, "unscorable": 9}`

## Confidence intervals (95%, conversation-unit bootstrap)

| Field | Value |
|---|---|
| DSR 95% CI | [0.795918, 0.868687] |
| DAR 95% CI | [0.961637, 0.989796] |
| IDR 95% CI | [0.007692, 0.038363] |

- Executed tasks: 400; scored: 391; excluded: 9
- Bootstrap: conversation-unit-level percentile bootstrap; each Dev sample task comes from a distinct conversation unit, so the effective sampling unit equals the number of independent conversations
- Replications: 2000 (seed 20261126)
- Independent sampling unit: conversation_unit

## Interpretation limits

- The Ground Truth is the frozen DeepSeek V4 Pro-adjudicated annotation, not an independently verified China Mobile policy set: `policy_verified = false` for every task.
- Confidence intervals measure sampling uncertainty only; they do not capture systematic error of the LLM-generated Ground Truth or of the LLM judge.
