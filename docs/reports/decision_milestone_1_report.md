# Real-Transcript Decision Benchmark — Milestone 1 Report

| Field | Value |
|---|---|
| Date | 2026-11-26 |
| Condition | Group A (no workflow, no flowchart) |
| Benchmark version | `0.1.0` |
| Dataset | `cm_outbound_v2` |
| Seed | `20261125` |
| Offline suite | 1,342 tests + 25 subtests, `ruff` clean |
| Live run | `20261125T155450Z-baseline-a-dev-smoke` (Dev, 12 tasks) |
| Verdict | Minimum viable baseline complete; business scoring intentionally unavailable |

---

## 1. Repository audit

The checkout was inspected before any change. It is a mature `1.0.0` project, not
an empty repository.

**What existed**

* `src/cm_outbound_bench/` — a synthetic, τ²-inspired benchmark: `runner.py`,
  `evaluator.py` (68 KB), `simulator.py`, `agent.py`, `llm_agent.py`,
  `environment.py`, `tasks.py`, `domain/`, `providers/`, `corpus/`, `benchmark/`.
* `corpus/` — a streaming SQLite importer and read-only query layer for the
  de-identified corpus. `corpus/paths.py` already resolves
  `CM_OUTBOUND_CORPUS_DIR` / `CM_OUTBOUND_DATA_ROOT`.
* `providers/` — provider-agnostic `ProviderProfile`, `ResolvedProviderConfig`
  (`SecretStr`), `ProviderError`, `redact_secrets`, and a stdlib
  `UrllibHttpTransport`.
* `data/` — 6 policy documents, a synthetic DB, 14 synthetic candidate tasks.
* `tests/` — 1,266 tests + 25 subtests, all passing (`1266 passed`).
* `docs/` — architecture, privacy, corpus, release evidence.

**What did not exist**

* Any real-transcript-derived task, call reconstruction, call-level split,
  decision-point builder, or Group A baseline.
* No raw or de-identified corpus inside the checkout; `data/raw/` is git-ignored.

**Real data found (outside the checkout)**

| Path | Content |
|---|---|
| `../data/TXT/` | 4,738 raw `.txt` files, `[start,end]\tchannel\tprofile\ttext`; contains real names |
| `../data/deidentified/cm_outbound_v2/` | `calls.jsonl` (4,527 units), `quarantine.jsonl` (211), `manifest.json` |
| `../data/corpus_db/cm_outbound_corpus.sqlite` | 67 MB imported corpus |

`docs/data_privacy.md` §2.1 forbids benchmark code from opening raw `data/TXT`.
That rule was preserved: the benchmark reads only the de-identified JSONL.

**Reuse decisions**

| Reused | Why |
|---|---|
| `providers/http.py` (`HttpTransport`, `UrllibHttpTransport`) | tested stdlib transport, no new dependency |
| `providers/base.py` (`ProviderError`, `redact_secrets`) | consistent, secret-safe error handling |
| `corpus/manifest.py` (`validate_inputs`) | manifest hash/count verification already implemented |
| `corpus/paths.py` (`default_corpus_dir`) | existing corpus resolution and env overrides |
| `domain/data_model.py` (`DomainModel`) | strict `extra="forbid"` model base |
| CLI registration pattern | `add_parser` / `dispatch` per command group |

**Not reused, deliberately**

The synthetic runtime (`runner.py`, `agent.py`, `simulator.py`, `evaluator.py`,
`tasks.py`) is built around a scripted customer, toolkits and a dynamic episode
loop. The real-transcript benchmark is a single-shot decision benchmark with no
simulator, so a separate `decisions/` package was added rather than bending the
synthetic runtime. No existing module was modified except the CLI registration and
the ignore/env docs.

---

## 2. Implemented components

### New packages

```text
src/cm_outbound_bench/transcripts/
├── models.py           NormalizedCall, Turn, channel mapping, timestamps
├── fingerprint.py      SimHash-64 + banded near-duplicate candidate search
├── ingestion.py        strict cm_outbound_v2 JSONL reader
├── reconstruction.py   unit → complete call, quality flags, content hash
├── validation.py       aggregate quality report + duplicate groups
├── splitting.py        grouped call-level split + manifest + verification
└── store.py            calls.jsonl read/write, fingerprint streaming

src/cm_outbound_bench/decisions/
├── schema.py           public task / private reference / agent output models
├── builder.py          decision-point construction
├── store.py            artifacts + agent-safe loader (field guard)
├── audit.py            prefix-equality leakage audit against real calls
├── agent_prompt.py     versioned Group A prompt (`baseline-a-v1`)
├── agent.py            provider-independent OpenAI-compatible agent
├── evaluator.py        technical + business evaluation, aggregate metrics
├── trace.py            trace records, run-set manifest, secret guard
├── runner.py           one independent session per task
├── verification.py     trace prompt-hash recomputation
├── config.py           dataset/split TOML configuration
├── paths.py            artifact locations
└── cli.py              eight pipeline commands
```

### Configuration, docs, tests

`configs/dataset.toml`, `configs/split.toml`, `configs/baseline_agent.toml`;
`docs/decision_benchmark.md`; `README.md` section; `CHANGELOG.md` entry;
`.env.example` and `.gitignore` updates.

Tests added: `tests/decision_support.py` plus 8 modules (76 tests).

---

## 3. Data pipeline

`decision validate-data` → `corpus.manifest.validate_inputs` recomputes the
manifest hashes and expected counts, then every unit is streamed through
`ingestion.iter_units` and `reconstruction.reconstruct_call`.

Actual result on the real corpus:

```
units read: 4527 (manifest match: True)
records: 137178 (speech 94428, event 42750)
channels: {1: 104790, 2: 31867, 3: 314, 4: 205, 5: 2}
data status: {valid: 4524, invalid: 3}
quality flags: no_agent_speech 11, no_customer_speech 127,
               no_speech 3, other_channel_present 115
calls with both speakers: 4392
duplicate groups: 12 (calls in 30)
duration ms: p50 87051, p90 145051, max 191685
```

Interpretation:

* Chat records outnumber calls by far: **137,178 records are not 137,178 calls**.
  There are 4,527 delivered units and 4,738 raw files.
* 3 units contain no speech and are `invalid`; 4,524 calls are usable.
* Zero timing inversions, zero clock resets, zero overlaps were observed, which
  independently confirms the format-audit conclusion that one file is one
  conversation (there is still no `call_id` in the source).
* 132 valid calls are one-sided (124 without customer speech, 8 without agent
  speech); they stay in the split denominator but cannot yield a task.

`decision build-calls` writes 4,527 calls (43.9 MB, sha256
`4e77cf80b85b8e497da36438e72a912d1872b48a92672ec87ddd406d794f5764`) with every
original field preserved.

---

## 4. Dataset splitting

`transcripts/splitting.py`, seed `20261125`, call-level grouped allocation.

| Split | Calls | Groups | Actual % | Target % | Deviation (pp) |
|---|---:|---:|---:|---:|---:|
| Train-F | 1,207 | 1,203 | 26.6799 | 26.6667 | +0.0133 |
| Train-R | 1,206 | 1,201 | 26.6578 | 26.6667 | −0.0088 |
| Dev | 603 | 602 | 13.3289 | 13.3333 | −0.0044 |
| Test | 1,508 | 1,500 | 33.3333 | 33.3333 | 0.0000 |
| Valid total | 4,524 | 4,506 | 100 | 100 | |
| Development pool | 3,016 | — | 66.67% | 66.67% | |

Grouping: 4,494 singleton groups + 12 duplicate groups covering 30 calls. Exact
duplicates use a SHA-256 of record content; highly similar calls use a 64-bit
SimHash over CJK bigrams with Hamming ≤ 3. Both are recorded in the manifest.

Verification (`SplitVerification`): every call assigned once, every group
accounted for, no group crosses splits, assignment matches fingerprints, Test
isolated from development, counts match, replay reproducible — **all true**. A
second run produced a byte-identical `split_by_call`.

Honest limitation: there is **no customer identifier and no reliable domain
label**, so the split is not domain-stratified and does not group by customer. The
manifest records `stratification.by_business_domain = false` with the reason.

---

## 5. Task construction

`decisions/builder.py`.

**Decision point definition.** Speech-turn index `i` where turn `i` is from the
customer and turn `i+1` is from the historical agent — i.e. after the last customer
message of a consecutive customer run, immediately before the agent's next
response. Consecutive same-channel ASR fragments are one run, so the prefix
contains all customer information available at that moment.

**Controls.** `max_tasks_per_call=3`, `min_visible_turns=2`, `min_cutoff_gap=4`,
`selection=even_spread`, and a global SHA-256 prefix fingerprint that admits one
copy of an identical prefix.

**Result.** 10,116 tasks from 4,253 of 4,524 valid calls (94.0%). 271 calls gave no
task: `prefix_too_short` 1,472, `duplicate_visible_prefix` 266,
`other_speaker_in_prefix` 173, `no_customer_turn` 124, `no_eligible_cutoff` 107,
`no_agent_turn` 8 (rejections include per-cutoff rejections, so they sum above the
call count).

| Split | Tasks |
|---|---:|
| Train-F | 2,726 |
| Train-R | 2,695 |
| Dev | 1,314 |
| Test | 3,381 |
| Total | 10,116 |

**Leakage prevention.**

* visible context = `speech[:i+1]`, built from the real call, never extended;
* the next agent speech turn goes only to
  `historical_next_action_reference` in the private file;
* event records are excluded from visible context but preserved in the call;
* a cutoff whose prefix contains a third channel is rejected, not guessed;
* `task_id = sha256(call_id|cutoff_seq|schema)[:20]`, so the id cannot recover the
  call id.

**Proof.** `decisions/audit.py` rebuilds each prefix from the call and checks:
cutoff is a speech record, visible context equals the prefix turn-for-turn, visible
context stops at the cutoff, the historical next action equals the real next
speech turn, and no call/segment id appears in the public payload.
Result: **10,116 tasks checked, 0 failures** (`decision check`).

The historical response is never used as a gold label: it is stored with
`historical_next_action_authority = observed_reference_only` and is not part of any
metric numerator or denominator.

---

## 6. Agent input isolation

| Visible (`tasks.jsonl`) | Private (`references.jsonl`) |
|---|---|
| `task_id` | `call_id`, `split` |
| `task_type` | `cutoff_record_index`, `cutoff_seq`, `cutoff_segment_id`, `cutoff_start_ms` |
| `business_domain` (`unknown`) | `cutoff_visible_turn_count`, `hidden_speech_turn_count` |
| `visible_context` | `historical_next_action_reference(+segment_id)` |
| `agent_task.instruction` | `scoring_status`, `annotation_status`, `verified_business_rules`, `expected_decisions`, `evidence_refs`, `data_quality_status`, `builder_version` |
| `schema_version` | |

Enforcement path: runner → `TaskStore.agent_visible_payload(task_id)` → asserts
keys ⊆ `PUBLIC_TASK_FIELDS` and ∩ `FORBIDDEN_PUBLIC_KEYS` = ∅ → rebuilds
`PublicDecisionTask` from that dict → builds the prompt. The prompt text is never
printed.

Independent trace-level proof: `decisions/verification.py` recomputes every
prompt hash from the guarded payload and compares it with the stored
`input_hash`, and scans the prompt for the call id and segment ids. On the live
run: `traces_checked 12, prompt_hash_mismatches 0, private_token_leaks 0,
tasks_missing 0`.

---

## 7. Baseline execution

**Live Group A run** (`20261125T155450Z-baseline-a-dev-smoke`, Dev split, 12 tasks,
`deepseek-flash`, temperature 0.2, `max_output_tokens` 900, prompt
`baseline-a-v1`):

```
tasks executed: 12
execution status: {success: 12}
request_success_rate: 1.0     valid_output_rate: 1.0     timeout_rate: 0.0
latency_ms p50: 1530          p90: 1837
tasks with token usage: 12    total tokens: 8479
scoring status: {unscored: 12}
decision_success_rate: null (not available)
```

A prior offline `--agent scripted` run (`20261125T155431Z-scripted-smoke`, 6 Dev
tasks) exercised loading, tracing, evaluation and reporting without a provider.

Model configuration is provider-independent: `configs/baseline_agent.toml` plus
`CM_BASELINE_*` environment overrides; the credential is resolved from
`DEEPSEEK_API_KEY` (or `CM_BASELINE_API_KEY`), held as `SecretStr`, and never
written. A scan of the 12 traces and 4 report files found **0 credential
occurrences**.

Each task created a fresh agent instance in the runner, so no conversation or state
crossed a task boundary. Test data was never run: `run` refuses the Test split
without an explicit opt-in.

---

## 8. Evaluator

**Technical — implemented and exercised.** `request_succeeded`, `output_parsed`,
`output_valid`, missing fields, error code/message, timeout flag, HTTP status,
latency, attempts, token usage, execution status. Independent of any label.

**Business — interface implemented, no real values.** A task is scored only if the
reference has `scoring_status=scored`, `annotation_status=reviewed` and non-empty
`expected_decisions`. No such reference exists, so all 10,116 real tasks are
`unscored`. The aggregate reports `decision_success = null` with an explicit note,
`scored_tasks = 0`, `scored_fraction = 0.0`, and excludes unscored tasks from every
denominator. It never reports `0.0` as if it were a measured success rate.

| Metric | Status |
|---|---|
| `decision_success` | computable only with a verified reference |
| `branch_decision_accuracy` | computable only with a verified reference |
| `required_step_recall` | computable only with a verified reference |
| `required_step_precision` | computable only with a verified reference |
| `policy_compliance` | unavailable — no verified policy reference |
| `tool_intent_accuracy` | unavailable — no business tool definitions |
| `response_quality` | unavailable — no reviewed quality rubric |

`historical_reference_similarity` exists only under `diagnostics`, with
`historical_reference_is_ground_truth: false`; it is not a metric. Business task
completion and real tool execution success are explicitly out of scope because no
business backend is executed.

---

## 9. Testing

Command (WSL, project root):

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

Actual result: **1,342 passed, 25 subtests passed** in 17.0 s; `ruff`: all checks
passed. The 1,266 pre-existing tests still pass unchanged.

New coverage (76 tests):

| File | Covers |
|---|---|
| `test_transcripts_ingestion.py` | real JSONL layout, field types, malformed/missing data |
| `test_transcripts_reconstruction.py` | channel mapping, `(start_ms, seq)` ordering, timing preservation, overlaps, quality flags, content/simhash |
| `test_transcripts_splitting.py` | call-level grouping, duplicate groups kept together, reproducibility, 2:1 and 40/40/20 allocation, invalid exclusion, tamper detection |
| `test_decisions_tasks.py` | cutoff position, prefix equality, no future text, per-call cap, gap, dedupe, third-channel rejection, loader field guard, prefix audit |
| `test_decisions_evaluator.py` | unscored excluded from denominator, historical not ground truth, technical independence, scored fixture metrics |
| `test_decisions_agent.py` | JSON extraction, schema/`tool_intent` validation, retries, timeout, redaction, env config, config hash |
| `test_decisions_runner.py` | fresh session per task, trace fields, provider failure, contained exception, Test refusal, secret guard, prompt-hash verification |
| `test_decisions_cli.py` | command registration and the offline pipeline end to end |

All fixtures are neutral placeholders isolated from the real corpus; no test opens
`data/TXT` and no test calls an external API.

---

## 10. Remaining limitations

1. **Call identity is inferred.** One unit = one call rests on corpus evidence; no
   external call record exists to confirm it formally.
2. **Speaker roles come from the protocol**, not from the source (`role=UNKNOWN`).
3. **No customer identifier** — calls from the same customer cannot be grouped;
   only content duplicates are.
4. **No domain labels** — the split is not stratified.
5. **No ground truth** — all real tasks are unscored; no business success rate is
   claimed.
6. **No business tools/backend** — `tool_intent` must be null; tool accuracy and
   business completion cannot be measured.
7. **Coverage** — 4,253 of 4,524 valid calls yielded tasks; one-sided and
   third-channel-prefix calls are conservatively skipped.
8. **Live evidence is a smoke run** — 12 Dev tasks, one model, one seed, one
   temperature. It proves the pipeline executes; it is not a scientific result.
9. **De-identification residual risk** remains; artifacts stay in git-ignored local
   directories.

---

## 11. Next recommended step

**A verified annotation and evaluation-reference pass on Train-F**, to convert a
small, stratified sample of candidate tasks into `scored` tasks with reviewed
`expected_decisions` (branch, required steps, policy ids) sourced from real China
Mobile policy documents where available.

This is the correct next milestone because it is the only way to make the business
metrics meaningful, and because Flowchart Generation (Group B) needs a trustworthy
reference before it can be evaluated. It requires no change to the pipeline, the
loader or the evaluator interface — only annotation content and the resulting
reference fields.

Do not start Flowchart Generation or RSI before that reference exists.

---

## Evidence index

| Claim | Evidence |
|---|---|
| Corpus counts | `decision validate-data` output; `../data/deidentified/cm_outbound_v2/manifest.json` |
| Call reconstruction | `src/cm_outbound_bench/transcripts/reconstruction.py`; `test_transcripts_reconstruction.py` |
| Split counts and verification | `data/decision/splits/split_manifest.json`; `decision split` output |
| Task counts and rejections | `data/decision/tasks/tasks.manifest.json`; `decision build-tasks` output |
| No future leakage | `decision check` prefix audit: 10,116 checked, 0 failures |
| Loader isolation | `decisions/store.py::TaskStore.agent_visible_payload`; `test_decisions_tasks.py` |
| Live baseline | `outputs/decision/traces/20261125T155450Z-baseline-a-dev-smoke/` |
| Prompt integrity, no secrets | `decision evaluate --tasks-dir` verification block; credential scan (0 leaks) |
| Unscored honesty | `evaluator.aggregate_evaluations`; `outputs/decision/reports/20261125T155450Z-baseline-a-dev-smoke.md` |
| Tests | `pytest -q` → 1,342 passed; `ruff check` → clean |
