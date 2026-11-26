# LLM Business Decision Guidelines v1

| Field | Value |
|---|---|
| Guideline version | `annotation-guidelines-v1` (adopted by the LLM-gold pipeline) |
| Governs | DeepSeek V4 Pro annotation passes for Train-R and Dev |
| Companion documents | `llm_gold_ground_truth_protocol_v1.md`, `llm_output_scoring_rubric_v1.md` |

This is the annotation rubric the frozen prompts implement. It extends the
Milestone 1.1 human guidelines with the LLM-gold answerability vocabulary and
the generic decision-category taxonomy; it invents no China Mobile policy.

## 1. The judgement

Given the visible prefix only, what should the agent do next, and what
information or steps does that decision require? The historical agent's actual
reply is withheld on purpose and is never the answer.

## 2. Answerability (LLM-gold vocabulary)

| Value | Meaning | Scoreable |
|---|---|---|
| `sufficient_for_action` | prefix supports choosing the next action | yes |
| `clarification_required` | a nameable fact is missing; the correct next move is to obtain it | yes, when `required_information` names the facts |
| `requires_external_business_policy` | the answer depends on unavailable official rules | only when the defensible next move is clarify / verify / defer |
| `ambiguous` | two readings equally defensible and divergent, or unreadable | no |
| `insufficient_context` | context cannot support a defensible decision | no |
| `not_a_valid_business_decision_point` | not a business decision point | no |
| `malformed_context` | prefix damaged or uninterpretable | no |

## 3. Decision categories

Generic conversational categories, not authorized China Mobile actions:
`acknowledge`, `clarify`, `confirm`, `answer`, `explain`, `recommend`,
`verify_information`, `handle_objection`, `escalate`, `defer_pending_information`,
`close_conversation`, `other`. A task is never forced into a category.

## 4. Decisions, steps, evidence

- **Primary decision**: the most appropriate next action with a rationale tied
  to visible turns.
- **Acceptable alternatives**: only responses a well-trained colleague's answer
  would be accepted without correction. Never padded.
- **Required steps**: behaviours without which the decision is incomplete.
- **Optional steps**: improvements that are not necessary for correctness.
- **Prohibited steps**: generic conversational constraints only. A specific
  business prohibition requires a real rule, which does not exist in this
  workspace; do not invent one.
- **Evidence refs**: at least one `visible_turn` index (1-based over the visible
  prefix). Future turns are never referenced.

## 5. Policy dependency

`external_policy_dependency = true` when a specific price, eligibility
condition, promotion rule or account state would be required and is unavailable.
`policy_verified` stays `false`. The expected decision may still be scored when
it is a clarification, verification or deferral supported by the prefix.

## 6. Confidence

`high` / `medium` / `low`, qualitative. Low confidence normally means the task
stays unscored unless a clear clarification/verification action is established.

## 7. Call termination

Call-ending decisions are judged individually: the visible prefix must support
that no further customer-facing action is necessary. "The call ended eventually"
is not evidence.

## 8. Prompt version control

Pass A and Pass B prompts (`decision_annotation_pass_{a,b}_v1.md`) and the
adjudication prompt (`decision_annotation_adjudication_v1.md`) are frozen files.
A change increments the version and requires re-running the entire pilot with
the new version; annotations produced under an obsolete version never enter
final metrics.
