# LLM Output Scoring Rubric v1

| Field | Value |
|---|---|
| Rubric version | `llm-output-scoring-rubric-v1` |
| Judge | DeepSeek V4 Pro (`deepseek-v4-pro`), separate from the evaluated agent |
| Inputs | agent-visible prefix + frozen Gold Label + agent structured output |
| Forbidden inputs | agent model identity, condition identity, historical continuation, other tasks' outputs, Test references |

## 1. Outcomes

| Outcome | Definition |
|---|---|
| `fully_correct` | appropriate next decision; addresses apparent intent; respects information constraints; no material unsupported claims; satisfies relevant required steps. Semantic equivalents accepted. |
| `partially_correct` | understands the central intent and decides reasonably, but misses a relevant required step, omits an important clarification or makes a non-critical mistake. |
| `incorrect` | inappropriate next action; fails the central need; material unsupported assertion; contradicts explicit customer information; violates an established decision constraint. |
| `unscorable` | the gold label or the output does not support a reliable evaluation. |

## 2. Critical errors (flagged independently)

- `fabricated_business_policy`
- `fabricated_package_eligibility`
- `unsupported_pricing_claim`
- `contradicts_explicit_customer_information`
- `unjustified_completion_claim`

A critical error is only flagged when the evidence is sufficient; an
insufficient-evidence case is never promoted into one.

## 3. Metrics

- **LLM-Gold Decision Success Rate (DSR)** = `fully_correct` / scored tasks.
- **Decision Acceptance Rate (DAR)** = (`fully_correct` + `partially_correct`) /
  scored tasks.
- **Partial Decision Rate (PDR)**, **Incorrect Decision Rate (IDR)**.
- **Scored Task Coverage** = scored / executed.
- Secondary metrics (clarification rate, required-step recall, branch accuracy)
  are computed deterministically only where the Gold Label carries the necessary
  information. They are lexical substring surrogates over free-text Chinese and
  are reported as lower-bound diagnostics, not as semantic accuracy.
- **Required-step precision is not reported.** The frozen Gold Label records
  required steps as actions ("confirm a date avoiding Friday"), while the agent
  output exposes requested *information*; a precision denominator would compare
  two different kinds of object. The lexical surrogate is kept in
  `required_step_precision_lexical_diagnostic` for transparency only.
- `policy_verified_decision_success_rate` and `response_quality` stay
  unavailable: no authoritative policy source and no human quality rubric exist.

## 4. Process rules

- The Gold Label is frozen before scoring and is read-only during scoring.
- Two independent stateless scoring passes; disagreements go to a separate V4
  Pro adjudication call.
- Scoring can never change the Gold Label; the reference hash is part of every
  scoring cache key, so a changed reference forces a re-score.
- Agreement statistics are reported as LLM Scorer Agreement (intra-model
  consistency), not human annotation reliability.
