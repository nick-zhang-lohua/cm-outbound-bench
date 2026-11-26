# LLM-Gold Annotation Quality Report

| Field | Value |
|---|---|
| Ground Truth model | deepseek-v4-pro |
| Annotation prompt version | v1 |
| Adjudication prompt version | v1 |

## Annotation consistency (intra-model, two V4 Pro passes)

### train_r_pilot

| Field | Value |
|---|---|
| Tasks finalized | 100 |
| Scored / unscored | 98 / 2 |
| Pass comparison distribution | {"acceptable_alternatives": 5, "agreement": 2, "both_unscored": 2, "major_disagreement": 18, "minor_disagreement": 73} |
| Adjudication rate | 0.91 |
| Adjudication outcomes | {"accept_both": 83, "accept_pass_a": 7, "accept_pass_b": 1} |
| Answerability distribution | {"clarification_required": 15, "insufficient_context": 2, "sufficient_for_action": 83} |
| Primary category distribution | {"acknowledge": 26, "answer": 4, "clarify": 17, "close_conversation": 28, "confirm": 18, "explain": 2, "handle_objection": 2, "verify_information": 1} |
| Confidence distribution | {"high": 93, "low": 2, "medium": 5} |
| External policy dependency tasks | 1 |
| policy_verified tasks | 0 |
| Total tokens | 661877 |

### train_r_feedback

| Field | Value |
|---|---|
| Tasks finalized | 300 |
| Scored / unscored | 295 / 5 |
| Pass comparison distribution | {"acceptable_alternatives": 5, "agreement": 7, "both_unscored": 5, "major_disagreement": 73, "minor_disagreement": 210} |
| Adjudication rate | 0.9433 |
| Adjudication outcomes | {"accept_both": 265, "accept_pass_a": 15, "accept_pass_b": 2, "consensus": 1} |
| Answerability distribution | {"clarification_required": 36, "insufficient_context": 5, "sufficient_for_action": 259} |
| Primary category distribution | {"acknowledge": 78, "answer": 5, "clarify": 48, "close_conversation": 88, "confirm": 53, "explain": 6, "handle_objection": 16, "verify_information": 1} |
| Confidence distribution | {"high": 287, "low": 5, "medium": 8} |
| External policy dependency tasks | 8 |
| policy_verified tasks | 0 |
| Total tokens | 2008968 |

### dev

| Field | Value |
|---|---|
| Tasks finalized | 400 |
| Scored / unscored | 391 / 9 |
| Pass comparison distribution | {"acceptable_alternatives": 8, "agreement": 15, "both_unscored": 9, "major_disagreement": 96, "minor_disagreement": 272} |
| Adjudication rate | 0.92 |
| Adjudication outcomes | {"accept_both": 347, "accept_pass_a": 16, "accept_pass_b": 4, "consensus": 1} |
| Answerability distribution | {"ambiguous": 2, "clarification_required": 40, "insufficient_context": 6, "not_a_valid_business_decision_point": 1, "sufficient_for_action": 351} |
| Primary category distribution | {"acknowledge": 113, "answer": 9, "clarify": 62, "close_conversation": 98, "confirm": 74, "defer_pending_information": 2, "explain": 8, "handle_objection": 22, "verify_information": 3} |
| Confidence distribution | {"high": 382, "low": 8, "medium": 10} |
| External policy dependency tasks | 15 |
| policy_verified tasks | 0 |
| Total tokens | 2640226 |

## Output evaluation consistency (two V4 Pro scoring passes)

| Field | Value |
|---|---|
| Scorer A/B agreement | 0.9463 |
| Scoring adjudication rate | 0.0537 |
| Final outcome distribution | {"fully_correct": 326, "incorrect": 9, "partially_correct": 56, "unscorable": 9} |
| Critical error distribution | {"contradicts_explicit_customer_information": 4, "unjustified_completion_claim": 2, "unsupported_pricing_claim": 3} |

These are two evaluations by the same model. They are reported as LLM Pass Agreement / LLM Scorer Agreement (intra-model consistency), never as independent human annotation reliability.
