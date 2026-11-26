# Real-Transcript Decision-Point Benchmark (Milestone 1)

Status: **minimum viable baseline implemented** (Milestone 1), extended in
**Milestone 1.1** with a trustworthy business evaluation methodology, annotation
infrastructure, a channel semantics audit and a Group A business baseline report.

This is the Group A benchmark. It is a second, independent benchmark line in this
repository, built on the real de-identified outbound corpus rather than on synthetic
scenarios. It shares the package, the provider transport and the privacy rules, and
adds no simulator, no tool backend, no flowchart and no RSI.

## 0. Terminology (read this first)

The corpus contains **no `call_id`**. `transcript_id` is
`"CALL_" + hmac_sha256(key, source_file_name)[:24]`, so it identifies a *delivered
transcript unit* that came from exactly one source file.

| Term | Meaning |
|---|---|
| `transcript_unit_id` | The corpus identifier of the delivered JSONL unit |
| `conversation_id` | The reconstructed conversation built from that unit |
| `call_id` | Legacy internal field name; **always** means inferred conversation unit |

`conversation_id` and `transcript_unit_id` currently hold the same value. The
inference "one unit = one complete telephone call" is supported by the format audit
and re-verified in Milestone 1.1, but **no external call record confirms it**. New
code and reports use `conversation_id`; `call_id` is retained only for artifact
compatibility. See `docs/reports/channel_semantics_audit.md` §1 and
`decision audit-identity`.

```text
De-identified corpus (calls.jsonl)
  │  1. validate-data      count, hash, structure, quality, duplicates
  ▼
Reconstructed complete calls (data/decision/calls/calls.jsonl)
  │  2. build-calls
  ▼
Call-level split manifest (data/decision/splits/split_manifest.json)
  │  3. split              Train-F / Train-R / Dev / Test, grouped by call
  ▼
Decision-point task candidates (data/decision/tasks/)
  │  4. build-tasks        public tasks + private evaluator references
  ▼
Agent-safe loading → Group A agent → Evaluator → Trace
  │  5. check  6. run  7. evaluate  8. report
  ▼
outputs/decision/traces/<run_set>/ + outputs/decision/reports/
```

---

## 1. What this benchmark is and is not

| It is | It is not |
|---|---|
| A static benchmark over **real, de-identified** China Mobile outbound transcripts | A simulated customer interaction |
| A sequence of **decision points** cut from real conversation prefixes | A full historical conversation replayed to the agent |
| Group **A**: no flowchart, no workflow, no retrieval | Groups B/C, Flowchart Generation, RSI |
| Technical evaluation always; business evaluation only with verified labels | A fabricated business success rate |
| Provider-independent (`OpenAI`-compatible HTTP, no vendor SDK) | A single hard-coded model or vendor |

The Real-Transcript Decision Benchmark **does not invoke the Legacy Simulator**.
There is no simulated customer and no synthetic conversation anywhere in this
pipeline: the evaluated agent never receives a future turn, a historical response or
an evaluator label, and no component generates a customer reply. The legacy synthetic
benchmark in the same repository still uses its own `simulator.py`; that component is
unrelated, is not imported here, and must not be removed.

---

## 2. Data source, provenance and limits

Input is the published `cm_outbound_v2` de-identified corpus:
`calls.jsonl`, `quarantine.jsonl`, `manifest.json`. Project policy
(`docs/data_privacy.md` §2.1) forbids benchmark code from opening the raw
`data/TXT` export, and this package does not read it.

Established facts from the corpus manifest and the format audit
(`docs/TXT原始数据格式只读审计报告.md`):

| Fact | Value |
|---|---|
| Raw source files | 4,738 |
| Delivered transcript units | 4,527 |
| Quarantined units | 211 |
| Records | 137,178 (94,428 speech, 42,750 event) |
| Channel ids present | 1 (104,790 records), 2 (31,867), 3 (314), 4 (205), 5 (2) |
| Call metadata in source | **none**: no call id, no phone numbers, no customer id, no agent id, no wall-clock time |
| Speaker role in source | `UNKNOWN` for every record |

### 2.1 One unit is one call — the evidence, not the assumption

The number of transcript records is **not** the number of calls, and the source has
no `call_id`. The benchmark treats one delivered transcript unit as one complete
conversation because the format audit proves it from five independent checks
(§3.6 of the audit):

1. timestamps are monotonic within every unit;
2. adjacent segments never overlap (0 overlap across 4,738 files);
3. no unit resets its clock to zero;
4. the longest unit is 191.8 s, consistent with one outbound call;
5. every file has a distinct content hash.

This milestone re-verifies (1)–(3) on the delivered data: **zero** timing
inversions, **zero** clock resets and **zero** overlaps were found. The manifest
records the inference and its evidence, and the limitation that no external call
record exists to confirm it formally.

### 2.2 Speaker mapping

The research protocol defines the original channel mapping and it is used
verbatim:

* channel **1** → `historical_agent`
* channel **2** → `customer`
* any other channel → `other` (kept on the timeline; tasks with an `other`
  speaker in the visible prefix are rejected rather than guessed)

The corpus itself stores `role = UNKNOWN`, so the mapping is a protocol decision.
Every validation report emits `speaker_roles_from_channel_mapping` as an explicit
`info` issue so this is never mistaken for a corpus-provided label.

### 2.2a Channels 3, 4 and 5 remain unknown

Milestone 1.1 investigated the remaining channels and produced a human-review
package; see **`docs/reports/channel_semantics_audit.md`**. The production mapping is
unchanged and channels 3/4/5 keep the `other` role until a researcher approves an
interpretation in writing. `decisions/channels.py` holds the registry and the
evidence-backed hypothesis catalogue, and
`assert_production_mapping_unchanged()` fails if an unknown channel is ever promoted
silently.

Measured population and benchmark cost of the current conservative behaviour:

| Fact | Value |
|---|---:|
| Records on channel 3 / 4 / 5 | 314 / 205 / 2 |
| Units containing any unknown channel | 115 |
| Units containing unknown-channel speech | 93 |
| Tasks that live in such a unit | 71 |
| Candidate cutoffs rejected as `other_speaker_in_prefix` | 173 |

### 2.3 What cannot be done with this corpus

* **No customer-level grouping.** There is no customer identifier. Calls can only
  be grouped by content similarity, and the split manifest says so.
* **No domain stratification.** There is no reliable business-domain label, so the
  split is not stratified by domain and the manifest records
  `stratification.by_business_domain = false` with the reason.
* **No verified policy ground truth.** There is no verified policy set and no
  business operation record. Milestone 1.2 adopted a frozen DeepSeek V4 Pro
  adjudicated reference (`cm-outbound-llm-gold-v1`) for research scoring, but it
  is `policy_verified = false` on every task and is never presented as an
  authoritative China Mobile policy source.

---

## 3. Pipeline

All artifacts live under `data/decision/` and `outputs/decision/`. They are
conversation-derived research artifacts and are **included in this private
owner/supervisor repository** (`.gitignore` does not exclude them). They remain
logically separated into Agent-visible, Private-Evaluator and Research-audit
boundaries — see §4 and `README.md` — and must not be mirrored outside that access
boundary. The raw TXT export and the wider de-identified corpus stay outside the
repository.

### 3.1 Validate the corpus

```bash
uv run cm-outbound decision validate-data
```

Recomputes the manifest hashes and counts (`corpus.manifest.validate_inputs`),
streams every unit, reconstructs every call and reports aggregate quality only.
Actual result on the real corpus:

```text
units read: 4527 (match=True)
records: 137178  (speech 94428, event 42750)
data status: {'valid': 4524, 'invalid': 3}
quality flags: no_agent_speech 11, no_customer_speech 127,
               no_speech 3, other_channel_present 115
calls with both speakers: 4392
duplicate groups: 12 (calls in 30)
duration ms: p50 87051, p90 145051, max 191685
```

Three units contain no speech at all and are marked `invalid`. The remaining
4,524 calls are structurally sound.

### 3.2 Reconstruct complete calls

```bash
uv run cm-outbound decision build-calls
```

Each record keeps its original `seq`, `start_ms`, `end_ms`, `channel_id`,
`payload_type`, `event`, text and flags. Records are ordered by
`(start_ms, seq)`: a timestamp tie preserves the original source order. Non-speech
events stay on the timeline. Overlapping speech is preserved and flagged
(`overlaps_previous`), never rewritten into artificial alternation.

### 3.3 Split at call level

```bash
uv run cm-outbound decision split
```

1. Invalid calls are excluded and counted (`excluded_call_ids`).
2. Exact duplicates are grouped by a SHA-256 of the record content.
3. Highly similar calls are grouped by a 64-bit SimHash over CJK character
   bigrams, a 4-band candidate filter and Hamming confirmation
   (`near_duplicate_max_hamming = 3`).
4. Groups — never individual calls — are allocated by a deterministic greedy
   relative-deficit pass, ordered by `sha256(seed:group_id)`.
5. Stage 1 assigns Test vs the development pool; stage 2 splits the pool into
   Train-F / Train-R / Dev.

Actual result (seed `20261125`):

| Split | Calls | % of valid | Target |
|---|---:|---:|---:|
| Train-F | 1,207 | 26.68% | 26.67% |
| Train-R | 1,206 | 26.66% | 26.67% |
| Dev | 603 | 13.33% | 13.33% |
| Test | 1,508 | 33.33% | 33.33% |
| **Valid calls** | **4,524** | | |
| Development pool | 3,016 | 66.67% | 66.67% |

Groups: 4,506 (4,494 singleton + 12 duplicate groups covering 30 calls).
Maximum deviation from target: 0.013 percentage points. All seven verification
checks pass, and a second run with the same seed reproduces `split_by_call`
exactly. No call and no duplicate group crosses a split boundary.

**Test isolation.** `run` refuses any split other than `train_f`, `train_r` or
`dev` unless `allow_test` (CLI `--allow-test`) is passed explicitly. Test tasks
are built mechanically but were not used for prompt design, thresholds or
debugging.

### 3.4 Build decision-point tasks

```bash
uv run cm-outbound decision build-tasks
```

Definition. A *decision point* is a speech-turn index `i` such that speech turn
`i` is from the customer and speech turn `i+1` is from the historical agent. In
other words: after the last customer message of a consecutive customer-message
sequence, immediately before the historical agent's next response.

When records are consecutive ASR fragments from the same channel, the run of
customer turns ends at the last fragment, so the agent receives the complete
customer information available at that point.

Controls:

| Control | Default | Effect |
|---|---|---|
| `max_tasks_per_call` | 3 | caps repeated cutoffs per call |
| `min_visible_turns` | 2 | rejects prefixes that are too short |
| `min_cutoff_gap` | 4 | minimum speech turns between two cutoffs |
| `selection` | `even_spread` | spreads cutoffs across the call |
| global prefix fingerprint | always | one identical prefix per dataset |

Rejections are counted by reason. Actual result: **10,116 tasks from 4,253
calls**; 271 calls produced no task. Reasons: `prefix_too_short` 1,472,
`duplicate_visible_prefix` 266, `other_speaker_in_prefix` 173, `no_customer_turn`
124, `no_eligible_cutoff` 107, `no_agent_turn` 8.

### 3.5 Check the artifacts

```bash
uv run cm-outbound decision check
```

Runs three independent checks:

1. **Artifact validation** — public and private counts agree, no duplicate task
   id, no forbidden field in a public task, prefix length matches the reference.
2. **Split verification** — re-verifies grouping, coverage and counts from the
   manifest alone.
3. **Prefix audit** — for every task, rebuilds the real speech prefix from the
   call and proves the visible context equals it turn for turn, stops exactly at
   the cutoff, and that the recorded historical next action is the real next
   speech turn. Also proves no call id or segment id appears in the public task.

Actual result: `public tasks: 10116`, `prefix audit failures: 0`.

---

## 4. Agent input isolation

Two physically separate artifacts are written in the same pass:

| Agent-visible (`tasks.jsonl`) | Private (`references.jsonl`) |
|---|---|
| `task_id` (opaque hash) | `call_id`, `split` |
| `task_type = decision_point` | `cutoff_record_index`, `cutoff_seq`, `cutoff_segment_id`, `cutoff_start_ms` |
| `business_domain` (`unknown`) | `cutoff_visible_turn_count`, `hidden_speech_turn_count` |
| `visible_context` (prefix only) | `historical_next_action_reference` (observed only) |
| `agent_task.instruction` | `scoring_status`, `annotation_status`, `verified_business_rules`, `expected_decisions`, `evidence_refs`, `data_quality_status`, `builder_version` |

`TaskStore.agent_visible_payload()` is the only prompt path. It returns a plain
dictionary, asserts the field set is a subset of `PUBLIC_TASK_FIELDS`, and refuses
any payload containing one of twenty `FORBIDDEN_PUBLIC_KEYS` (call id, cutoff,
future-turn reference, scoring label). The runner rebuilds the task from that
guarded payload before building the prompt, so the prompt cannot be derived from a
richer object.

Two independent proofs are available:

* `check` runs the prefix audit over every task;
* `decision evaluate --tasks-dir ...` recomputes each trace's prompt hash from the
  guarded payload and compares it with the stored `input_hash`. On the live run
  set: `traces_checked 12, prompt_hash_mismatches 0, private_token_leaks 0`.

---

## 5. Group A baseline agent

`cm_outbound_bench.decisions.agent` depends only on two prompt strings. The runner
creates a fresh agent instance per task, so no state can cross a task boundary.

* Endpoint: any OpenAI-compatible `chat/completions`.
* Credentials: `CM_BASELINE_API_KEY` or the environment variable named by
  `api_key_env` in `configs/baseline_agent.toml`. The key is held in `SecretStr`,
  never written to a trace, and redacted from error text.
* Overrides: `CM_BASELINE_API_BASE`, `CM_BASELINE_MODEL`,
  `CM_BASELINE_TIMEOUT`, `CM_BASELINE_MAX_RETRIES`, `CM_BASELINE_TEMPERATURE`,
  `CM_BASELINE_MAX_OUTPUT_TOKENS`, `CM_BASELINE_HEADER_ENVS`.
* Retries on 408/409/425/429/5xx and transport failure; request timeout and
  retries are configurable.
* Prompt: `baseline-a-v1`, recorded in every trace and hashed into `input_hash`.

Output contract (`agent_output` in the trace):

```json
{
  "customer_response": "...",
  "next_action": "...",
  "required_information": [],
  "tool_intent": null,
  "uncertainty": null,
  "evidence_summary": "..."
}
```

`tool_intent` must be `null`: no business tool exists in this milestone, and a
non-null value is a technical validation failure. The output schema is not a list
of authorized actions.

Run one task or a small subset:

```bash
uv run cm-outbound decision run --agent live --split dev --task-limit 12
```

Offline smoke test with no network (clearly marked placeholder output, not a
result):

```bash
uv run cm-outbound decision run --agent scripted --split dev --task-limit 6
```

---

## 6. Evaluation

### Technical (always computed)

Request success, parse success, schema validity, missing fields, timeouts, HTTP
status, latency, attempts, token usage and execution status.

### Business (only with verified references)

Scoring status is one of `scored`, `candidate`, `unscored`, `invalid`. A task is
scored only when the reference satisfies **every** gate in
`PrivateTaskReference.is_scored`:

* `scoring_status = scored`;
* `annotation_status` is `reviewed` or `adjudicated`;
* the annotation is not model-only (`llm_proposed` is never scorable);
* `task_answerability` is scoreable;
* at least one accepted decision, or an explicit clarification expectation, exists.

No such reference exists for this corpus out of the box, so every real task starts
`unscored` and the decision success rate is `null`, not `0.0`.

### Two evaluation claims, reported separately

| Metric | Claim | Available today |
|---|---|---|
| `expert_adjudicated_decision_success_rate` | An independent business reviewer judged the decision appropriate | only after human annotation |
| `policy_verified_decision_success_rate` | An official policy or verified business rule supports the decision | **no** — no authoritative source exists in the workspace |
| `branch_decision_accuracy` | The conditional path was right | only after annotation |
| `required_step_precision` / `required_step_recall` | Step completeness and economy | only after annotation |
| `policy_compliance` | No documented prohibition was violated | only with a policy-verified reference |
| `appropriate_clarification_rate` | The agent asked for the missing information when that was the correct answer | only after annotation |
| `response_quality` | Human rating of the customer response | only after blind human review |
| `tool_intent_accuracy` | — | **structurally unavailable**: no business tool definitions |
| `business_task_completion_rate` | — | **structurally unavailable**: no business backend |

The two tier rates are never averaged. `decision_success` is retained as a legacy
alias of the expert-tier outcome so a Milestone 1 artifact keeps its meaning.

The historical agent's next response is stored as
`historical_next_action_reference` with authority `observed_reference_only` and
`historical_reference_is_ground_truth: false`. A lexical similarity value is
recorded under `diagnostics` only; it never enters a numerator or denominator.

Unscored tasks are excluded from every business denominator, and every aggregate
reports the scored denominator, the scored fraction, the reasons for being unscored
and the count of candidate and invalid tasks.

---

## 7. Traces and reports

Every execution writes `<out>/<run_set>/RUN-<id>.json` plus a
`run_set_manifest.json` index. A trace records:

`trace_schema_version`, `run_id`, `run_set_id`, `task_id`, `condition = "A"`,
`benchmark_version`, `dataset_version`, `split`, `executed_at`, `model_provider`,
`model_name`, `model_parameters`, `agent_version`, `prompt_version`,
`evaluator_version`, `input_hash`, `input_bytes`, `config_hash`,
`agent_output`, `evaluation`, `latency_ms`, `token_usage`, `attempts`,
`execution_status`, `error`.

No credential appears in any artifact. `TraceManager` refuses to write a trace
containing a configured secret. Raw prompt or customer text is never printed by the
CLI.

```bash
uv run cm-outbound decision evaluate --run-set <dir> --tasks-dir data/decision/tasks
uv run cm-outbound decision report   --run-set <dir>
```

Reports contain aggregate measurements only: no conversation text, no prompt text
and no credential.

---

## 8. Reproducing the milestone

```bash
# WSL / Linux, from the project root
uv sync
export CM_OUTBOUND_CORPUS_DIR=../data/deidentified/cm_outbound_v2

uv run cm-outbound decision validate-data
uv run cm-outbound decision build-calls
uv run cm-outbound decision split            # seed 20261125
uv run cm-outbound decision build-tasks
uv run cm-outbound decision check

# Group A smoke run on development data only
uv run cm-outbound decision run --agent scripted --split dev --task-limit 6
export DEEPSEEK_API_KEY=...
uv run cm-outbound decision run --agent live --split dev --task-limit 12
uv run cm-outbound decision evaluate --run-set outputs/decision/traces/<run_set> \
    --tasks-dir data/decision/tasks
uv run cm-outbound decision report --run-set outputs/decision/traces/<run_set>
```

`configs/dataset.toml`, `configs/split.toml` and `configs/baseline_agent.toml`
hold the artifacts paths, the seed/targets and the model parameters.

### 8.1 Milestone 1.1 commands

The governance and annotation commands are registered in the same `decision` group.

```bash
# Governance audits (read-only; none of them changes the production mapping)
uv run cm-outbound decision audit-identity          # conversation identity + dataset integrity
uv run cm-outbound decision audit-channels          # channels 1-5 + human-review package
uv run cm-outbound decision audit-business-sources  # business reference inventory

# Reproducible annotation samples (one task per conversation, fixed seed)
uv run cm-outbound decision sample --purpose train_r_annotation_pilot --verify
uv run cm-outbound decision sample --purpose train_r_feedback_sample \
    --exclude-plan data/decision/samples/train_r_annotation_pilot.json --verify
uv run cm-outbound decision sample --purpose dev_baseline_evaluation_sample --verify

# Annotation workflow
uv run cm-outbound decision export-annotations  --plan data/decision/samples/<plan>.json
uv run cm-outbound decision import-annotations  --file annotator_a.jsonl
uv run cm-outbound decision agreement
uv run cm-outbound decision adjudication-worklist
uv run cm-outbound decision import-adjudications --file adjudications.jsonl
uv run cm-outbound decision apply-annotations

# Group A on the scored evaluation sample, then the business report
uv run cm-outbound decision run --agent live --split dev \
    --sample-plan data/decision/samples/dev_baseline_evaluation_sample.json
uv run cm-outbound decision business-baseline \
    --run-set outputs/decision/traces/<run_set> \
    --references data/decision/annotations/current/references.annotated.jsonl

# Blind human review of the agent outputs
uv run cm-outbound decision export-output-review --run-set <run_set> --include-unscored
uv run cm-outbound decision import-output-review --file reviews.jsonl
```

`decision run --sample-plan` refuses a plan whose split differs from the run split, so
a Dev plan cannot be redirected at Test. `decision export-annotations` refuses any plan
whose split is `test`.

---

## 9. Known limitations

1. **Call identity is an inference.** One transcript unit is treated as one
   conversation from corpus evidence; there is no external call record to confirm it.
   Reports use `conversation_id`; `call_id` is a legacy field name for the same value.
2. **Speaker roles come from the protocol mapping.** The corpus stores
   `role = UNKNOWN`.
3. **Channels 3, 4 and 5 are unknown.** Milestone 1.1 collected evidence and
   quantified the impact of every candidate interpretation, but the production
   mapping is unchanged pending human review.
4. **No customer identifier.** Conversations from one customer cannot be grouped; only
   content duplicates are.
5. **No domain labels.** The split is not domain-stratified, and annotation samples
   are stratified on observable task characteristics only.
6. **No authoritative business reference.** The inventory found zero official China
   Mobile policy documents, so `policy_verified_decision_success_rate` cannot be
   produced and no rule was invented.
7. **Business ground truth is LLM-generated, not human-verified.** Milestone 1.2
   froze DeepSeek V4 Pro-adjudicated Gold Labels (`cm-outbound-llm-gold-v1`) as
   the official scoring reference for this project, so Group A can be scored. A
   plain `llm_proposed` pre-annotation remains non-scorable, and no label is
   presented as verified policy.
8. **No business tools or backend.** `tool_intent` must stay `null`; real tool
   execution and business task completion cannot be measured.
9. **132 one-sided valid calls** (124 without customer speech, 8 without agent
   speech) cannot yield a decision point and are skipped; 3 further units have no
   speech at all and are invalid.
10. **Coverage:** 10,116 tasks come from 4,253 of 4,524 valid conversations (94%).
11. **De-identification residual risk** remains. The datasets are included in this
    private repository for supervisor review, not published; see
    `docs/data_privacy.md` §4.1.1 and §6 for the residual-risk statement and the open
    owner-confirmation item.

---

## 11. Dataset inventory in this repository

| Artifact | Path | Records | Role |
|---|---|---:|---|
| Reconstructed conversations | `data/decision/calls/calls.jsonl` | 4,527 calls | Source for Task reconstruction and audit |
| Conversation manifest | `data/decision/calls/calls.manifest.json` | — | Counts and hashes of the reconstruction |
| Agent-visible Tasks | `data/decision/tasks/tasks.jsonl` | 10,116 | The only Task input an agent may receive |
| Private evaluator references | `data/decision/tasks/references.jsonl` | 10,116 | Split, cutoff, hidden-turn counts, expected decisions |
| Split manifest | `data/decision/splits/split_manifest.json` | 4,524 calls | Seed `20261125`, duplicate groups, per-call split |
| Annotation packets | `data/decision/annotations/**/annotation_packets.jsonl` | 400 / 100 / 300 | Fixed, reproducible review samples |
| Sample plans | `data/decision/samples/*.json` | 3 plans | Deterministic task selection |
| Frozen Train-R Gold Labels | `outputs/decision/annotations/train_r/final_references.jsonl` | 400 | `cm-outbound-llm-gold-v1` |
| Frozen Dev Gold Labels | `outputs/decision/annotations/dev/final_references.jsonl` | 400 | `cm-outbound-llm-gold-v1` |
| Annotation passes | `outputs/decision/annotations/**/records/` | 1,600 packets | Pass A, Pass B, adjudications |
| Group A blind scoring | `outputs/decision/evaluation/group_a/` | 400 tasks | Two scorer passes + adjudicated results |
| Group A traces | `outputs/decision/traces/` | 400 + 3 smoke runs | Execution records for the 400 Dev Tasks |
| Aggregate reports | `outputs/decision/reports/` | 12 files | No conversation text |

Per-file sizes and SHA-256 values are recorded in
`docs/reports/github_dataset_upload_manifest.md`; the tracked/ignored decision is
recorded in `docs/reports/gitignore_audit.md`.

---

## 10. Future extension points (not implemented)

* Groups B/C can consume `tasks.jsonl` plus a retrieved flowchart; nothing in this
  pipeline needs to change, only the agent input builder.
* Flowchart Generation and RSI can use the private `references.jsonl` and the
  split manifest; they must never read `tasks.jsonl` of the Test split.
* A verified annotation pass can set `scoring_status = scored` and populate the
  decision references; the evaluator then produces business metrics with no code
  change. The infrastructure for that pass exists as of Milestone 1.1.
* A real business API would add tool definitions and `tool_intent`
  validation/accuracy. None is invented here.
