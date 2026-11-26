# LLM-Gold Ground Truth Protocol v1

| Field | Value |
|---|---|
| Protocol version | `llm-gold-ground-truth-protocol-v1` |
| Ground Truth version | `cm-outbound-llm-gold-v1` |
| Generator / Adjudicator / Judge | DeepSeek V4 Pro (API identifier `deepseek-v4-pro`) |
| Status | adopted research methodology — no human approval required |

## 1. Definition

**Official Benchmark Ground Truth = the final DeepSeek V4 Pro-generated and
adjudicated reference decision, frozen in the Benchmark Evaluator.**

These references are not temporary suggestions and are not pending human review.
They are the formal Gold Labels used by this benchmark for scoring.

Provenance values recorded on every gold reference:

```text
ground_truth_type   = llm_generated_gold
ground_truth_source = deepseek_v4_pro
ground_truth_status = frozen
annotation_method   = llm_based
annotation_status   = accepted_as_benchmark_gold
```

## 2. What the Gold Label is not

- It is **not** an independently verified China Mobile official policy. No
  authoritative policy document exists in the workspace, so
  `policy_verified = false` for every task, and
  `policy_verified_decision_success_rate` remains unavailable.
- It is **not** the historical agent's response. The historical continuation is
  never shown to the annotator, the adjudicator or the scorer, and it is never
  used as a label.
- It is **not** a human expert annotation. Agreement statistics are reported as
  intra-model LLM pass consistency, never as human inter-annotator reliability.

## 3. Pipeline

1. **Pass A** and **Pass B**: two independent, stateless DeepSeek V4 Pro calls on
   the identical agent-visible prefix, using separately worded prompts
   (`decision_annotation_pass_a_v1.md`, `decision_annotation_pass_b_v1.md`) with
   the same rubric. Neither pass sees the other's output.
2. **Comparison**: structured semantic comparison classifies each task as
   `agreement`, `acceptable_alternatives`, `minor_disagreement`,
   `major_disagreement` or `both_unscored`.
3. **Adjudication**: `minor_disagreement` and `major_disagreement` go to a
   separate V4 Pro call (`decision_annotation_adjudication_v1.md`) that receives
   the prefix, both pass records and the fixed guidelines — never the historical
   continuation, Group A outputs or Test data. Outcomes: accept A, accept B,
   accept both, consensus, ambiguous, unscored.
4. **Adoption**: agreement/`both_unscored` adopt the common result directly;
   `acceptable_alternatives` adopt the union of both passes; adjudicated tasks
   adopt the adjudicator's decision. The result is the official Gold Label.
5. **Validation and freeze**: every label is validated against the visible task
   (evidence turns in range, no future turns, consistency rules), deduplicated by
   task id, and frozen with a SHA-256 manifest
   (`frozen_annotation_manifest.json`).

## 4. Independence guarantees (enforced in code)

- The annotation model receives only the same agent-visible payload the agent
  received; the renderer is the same function the runner uses.
- Pass B is constructed without pass A's output; each pass is a fresh stateless
  client.
- The adjudicator cannot access Group A outputs, hidden evaluator references,
  historical continuations or Test data.
- The scorer receives the frozen label read-only; scoring has no write path to
  gold artifacts.
- Test is structurally locked: the pipeline only loads Train-R and Dev sample
  plans, and no command exposes Test annotation or evaluation.

## 5. Resume, idempotency and cache keys

Every API record is keyed by
`sha256(task_id, dataset_version, tasks_artifact_sha256, model, prompt_hash,
config_hash, pass)`. A completed record is reused across runs; a record whose
key no longer matches the current inputs is regenerated. Frozen artifacts refuse
silent overwrite.

## 6. Unscored tasks

A task stays `scoring_status = unscored` when the final annotation cannot
provide an identifiable, sufficiently supported evaluation reference. Unscored
tasks remain in the dataset but are excluded from every business denominator.

## 7. Changing the frozen version

Any later correction must create a new explicit Ground Truth version, document
the reason, and require consistent re-evaluation of all affected experimental
conditions. The initial frozen version stays available.

## 8. Operations

```bash
cm-outbound-bench decision annotation inspect          # manifests, overlap, counts
cm-outbound-bench decision annotation pilot            # 100 Train-R pilot tasks
cm-outbound-bench decision annotation train-r          # 300 Train-R feedback tasks
cm-outbound-bench decision annotation dev              # 400 Dev tasks
cm-outbound-bench decision annotation validate         # pre-freeze validation
cm-outbound-bench decision annotation freeze           # immutable freeze (once)
cm-outbound-bench decision evaluate-group-a            # blind two-pass scoring
cm-outbound-bench decision evaluate-group-a --reaggregate
cm-outbound-bench decision report-group-a              # print saved metrics
python scripts/audit_gold_integrity.py                 # offline integrity audit
```

`--reaggregate` re-derives finals from the saved scorer passes and saved
adjudications and makes **no API call**. It exists so an aggregation rule (such
as "a task whose Gold Label is unscored can never be scored") can be re-applied
without buying new judge verdicts, and it rewrites the finals artifact so stale
results cannot survive. All run commands accept `--dry-run`, `--resume`,
`--limit`, `--prompt-version`, `--model` and `--seed`.

