# Benchmark Execution, Providers and Reproducibility

---

## 1. Quick start

```bash
cd cm-outbound-bench
uv sync                                  # or reuse the existing .venv

# Legacy marketing benchmark (five manual tasks, deprecated for new work)
uv run cm-outbound run-all --agent demo

# Multi-domain benchmark (14 evidence-linked tasks)
uv run cm-outbound benchmark verify
uv run cm-outbound benchmark run --agent demo
```

Each episode writes a full trace to `outputs/runs/<run_id>/trace.json` and a
compact manifest to `outputs/runs/<run_id>.json`, then prints an aggregate
report. The default output directory is `./outputs/runs` relative to the current
working directory; set `CM_OUTBOUND_RUN_DIR` or pass `--output-dir` to change it.

## 2. Commands

### Multi-domain benchmark (current)

```bash
cm-outbound benchmark list-domains
cm-outbound benchmark list-tasks [--domain D02]
cm-outbound benchmark verify [--json]

cm-outbound benchmark run [--task ID ...] [--domain D02 ...] \
    [--agent demo|llm] [--provider LABEL] [--provider-config PATH] \
    [--output-dir PATH] [--max-steps N] [--repeat N] [--seed S] [--json]

cm-outbound benchmark evaluate --trace <trace.json> [--json]
cm-outbound benchmark replay   --trace <trace.json> [--json]

cm-outbound benchmark aggregate --dir RUN_DIR [--dir RUN_DIR ...] \
    [--json] [--output PATH]

cm-outbound benchmark compare --dir A [--dir B ...] [--split N] \
    [--json] [--output PATH]
```

### Corpus tooling

```bash
cm-outbound corpus import [--database PATH] [--corpus-dir PATH] [--rebuild] [--json]
cm-outbound corpus verify
cm-outbound corpus stats
cm-outbound corpus quarantine-summary
cm-outbound corpus list-domains
cm-outbound corpus classify [--no-replace]
cm-outbound corpus coverage-report [--out MD] [--json-out JSON]
cm-outbound corpus export-review-sample --out PATH [--per-stratum N] [--seed N] [--include-text]
cm-outbound corpus apply-review --review PATH --reviewer NAME
```

### Legacy marketing (deprecated, regression-preserved)

```bash
cm-outbound list-tasks
cm-outbound run --task marketing_direct_refusal --agent demo
cm-outbound run-all --agent demo
```

The legacy tree uses its own task model and simulator and is kept so the original
five tasks and their tests keep working. Use the `benchmark` family for all new
work.

Exit codes: `0` success, `1` evaluation or validation failure (including any
runtime failure in a batch), `2` usage or missing input.

### Batch semantics

One task's failure never aborts the rest of the batch:

| Situation | Recorded as | Effect on the batch |
|---|---|---|
| Task completed and passed | `run_status=success` | continue |
| Task completed and failed evaluation | `run_status=evaluation_failure` | continue |
| Task raised during execution or evaluation | `run_status=runtime_failure` + `<run_id>/failure.json` | continue |
| Episode stopped by a budget or an invalid action | `run_status=incomplete` | continue |

Every task is persisted immediately, so a later crash cannot destroy earlier
results. The process exits non-zero if any episode did not succeed. There is no
resume mode: re-running a batch creates new run ids and never overwrites old
runs.

## 3. Providers

The provider layer is vendor-neutral. A profile is an arbitrary local TOML label;
there is no allowlist and no runtime SDK dependency.

`providers.toml`:

```toml
[providers.my-relay]
api_protocol = "openai_compatible"
base_url = "https://relay.example.test/v1"
api_key_env = "MY_RELAY_KEY"
model = "relay-model"

[providers.my-relay.extra_params]
temperature = 0.0
seed = 7
```

Run with:

```bash
export MY_RELAY_KEY=...        # never passed on the command line
cm-outbound benchmark run --agent llm --provider my-relay \
    --task d02_offer_benefit_inquiry_accept
```

Properties:

* secrets are read from environment variables only;
* custom base URL, model, headers and extra parameters are supported by the
  profile;
* native tool calling is used when available, with a JSON structured-action
  fallback;
* errors are sanitized and known secrets are redacted (HTTP 429 is retried with
  backoff and surfaced as `provider_rate_limited` when retries run out);
* one normalized response yields exactly one agent action;
* the runner and tools know nothing about any vendor.

**Live model calls are never made automatically.** A run happens only when a
user invokes it explicitly with `--agent llm --provider <label>`.

### Seed and sampling

`--seed S` is recorded in every manifest and trace with an explicit
`seed_status`:

| Value | Meaning |
|---|---|
| `""` | no seed requested |
| `unused_deterministic_component` | the agent/simulator use no RNG, so the seed cannot change the episode |
| `applied` | the seed was passed to the provider request |
| `ignored_by_component` | the provider profile already pins its own seed |

The scripted simulator and the scripted agent are deterministic and use no random
number generator, so a seed does not change their behaviour. It is recorded
anyway so the experiment identity is complete.

Only an allow-list of generation parameters (`temperature`, `top_p`, `max_tokens`,
`seed`, `response_format`, …) is copied into `sampling`. Headers and API keys are
never recorded.

## 4. Artefacts: trace and manifest

Two artefacts, two jobs.

### `trace.json` — the source of truth (one per episode)

`outputs/runs/<run_id>/trace.json`, schema `trace_schema_version = "1.0"`.

| Section | Contents |
|---|---|
| Identity | `run_id`, `episode_id`, `task_id`, `domain_id`, `scenario_id`, `task_version`, `benchmark_version`, `scenario_status`, `rule_provenance_status` |
| Initial state | `initial_state_id`, `initial_state_hash`, `allowed_tools`, `required_tools` |
| Agent | `agent_type`, `provider_protocol`, `provider_name`, `model_name`, `prompt_hash`, `prompt_version`, `policy_hash`, `sampling`, `seed`, `seed_status`, `token_usage` |
| Ordered execution | `actions` (normalized agent actions in order), `conversation`, `simulator_events`, `tool_calls` (name, arguments, result, call id), `consent_events`, `action_consent_events` |
| Termination | `stop_reason`, `steps_executed`, `customer_conversation_closed`, `post_closure_actions`, `episode_completed`, structured `error_*` |
| Final state | `final_state_hash`, the complete synthetic database snapshot, `completion_status` |
| Recorded evaluation | success, summary, failed checks, per-dimension counts, answer-fact results |
| Integrity | `trace_hash`, `created_at`, `schema_version` |

Writing is atomic: serialize → re-parse and re-hash → scan for forbidden content →
write a temporary file in the destination directory → `os.replace` → only then
write the manifest that references it.

The hash is an **integrity check against accidental or uncoordinated
modification**, not a cryptographic anti-cheating signature.

### `RUN-*.json` — the compact index (one per episode)

| Field | Meaning |
|---|---|
| `run_id`, `episode_id`, `manifest_version`, `benchmark_version` | Identity |
| `domain_id`, `task_id`, `task_version`, `scenario_id` | What ran |
| `scenario_status`, `rule_provenance_status` | Provenance (all tasks stay `CANDIDATE`) |
| `initial_state_id`, `initial_state_hash`, `initial_tools_hash`, `task_hash` | Exact starting point, the visible tool surface and the task content |
| `agent_type`, `provider_protocol`, `provider_name`, `model_name` | What agent ran |
| `prompt_hash`, `prompt_version`, `policy_hash`, `evaluator_hash` | Content identity |
| `sampling`, `random_seed`, `seed_status` | Generation settings |
| `started_at`, `completed_at`, `setup_ms`, `episode_ms`, `evaluation_ms`, `total_ms`, `latency_ms` | Real wall-clock window and monotonic stage durations |
| `turn_count`, `tool_call_count` | Effort |
| `token_usage` | Provider-reported usage; empty when unavailable (never faked as zero) |
| `run_status`, `task_success`, `partial_completion`, `metric_scores` | Outcome |
| `dimensions`, `policy_violations`, `check_results`, `not_applicable_checks`, `answer_fact_results` | Compliance evidence |
| `final_state_hash`, `evaluator_version`, `completion_reason` | Reproducibility |
| `trace_path`, `trace_sha256`, `trace_schema_version` | Link to the trace |
| `repeat_index`, `repeat_count` | Position within a repeated experiment |
| `error` | Structured, sanitized failure record for a runtime failure |

Manifests never contain API keys, prompts, transcript text, the conversation or
the runtime database.

`evaluator_version` is the canonical version of the evaluator implementation that
actually ran, not a value copied from an authored task override. Task files
normally omit the field; a task that pins a version other than the installed
`EVALUATOR_VERSION` is rejected at load time. The aggregate report lists the
distinct evaluator versions it saw alongside the benchmark versions.

## 5. Re-evaluation and replay

```bash
cm-outbound benchmark evaluate --trace outputs/runs/<run_id>/trace.json
cm-outbound benchmark replay   --trace outputs/runs/<run_id>/trace.json
```

`evaluate` rebuilds the evaluation inputs from the trace and re-runs the
deterministic evaluator. It never calls an agent or a provider, and it reports
whether the result matches the recorded result.

A trace whose recorded `task_version` differs from the installed catalogue is
refused by default, because re-evaluating it answers a different question than "is
this result reproducible?". `evaluate --historical-diagnostic` is the explicit
opt-in for that cross-version question: it relaxes only the task-version equality
check, still enforces task, domain, scenario and final-state identity, labels the
result `historical_diagnostic: true` with both version labels, never writes to the
trace, and its result is never release evidence.

`replay` restores the original initial state, applies the recorded normalized
actions in order through the real runner, tool surface and simulator, then
compares the rebuilt episode with the recorded one across:

`action_sequence`, `conversation`, `simulator_events`, `tool_trace`,
`consent_events`, `stop_reason`, `final_state`, `evaluation`.

Any mismatch is reported as a divergence and the command exits non-zero. A trace
whose content no longer matches its hash, or whose stored final database no longer
matches its recorded final-state hash, is rejected before evaluation.

## 6. Reproducibility

Everything that determines an episode is versioned or hashed:

| Component | Version / hash |
|---|---|
| Task definition | `task_version` + `task_hash` (SHA-256 of the canonical task JSON) |
| Scenario | `scenario_id` (+ `scenario_status`) |
| Initial state | `initial_state_id` + `initial_state_hash` |
| Policy | `policy_hash` (SHA-256 of the exact policy text shown to the agent) |
| Tool surface | `allowed_tools` in the task and in the environment, plus `initial_tools_hash` (SHA-256 of the agent-visible tool definitions filtered by the allow-list) |
| Simulator | Declarative script in the task |
| Agent prompt | `prompt_hash` (SHA-256 of the real system prompt, or of the scripted-agent contract) |
| Evaluator | `evaluator_version` + `evaluator_hash` (content hash of the evaluator modules); the version is canonical, not task-authored |
| Result | `final_state_hash` |

To reproduce a run: same task version, same initial state id and hash, same agent
and model, same sampling settings and the same seed. The corpus is only needed to
*audit evidence*, not to run a task.

Aggregate reporting is descriptive and explicitly limited:

* a single run per task is not evidence of a stable success rate;
* a confidence interval is reported only for two or more runs per task, and is
  labelled as a normal approximation;
* runtime failures stay in the denominator and are also counted separately;
* the aggregate report says so in its notes.

### Agent comparison

```bash
cm-outbound benchmark compare --dir runs/agent_a --dir runs/agent_b
```

The comparison refuses to claim fairness when the benchmark version, task
version, task content hash or initial-state hash differ; it lists each
incompatibility and exits non-zero. A task present on only one side is reported
with `n/a` rates rather than a misleading zero.

## 7. Baseline results (deterministic agent)

Measured on the delivered tree:

```text
Legacy marketing tasks           5 / 5 passed
Benchmark tasks                 14 / 14 passed
Domains                          6 (D01–D06)
Average turns per benchmark task  6.3
```

These numbers validate that the task suite is solvable and self-consistent. They
are **not** a difficulty claim and say nothing about LLM agent performance.

## 8. Fresh-install verification

```bash
uv build
uv pip install dist/cm_outbound_bench-1.0.0-py3-none-any.whl
cd /tmp && cm-outbound benchmark verify
cd /tmp && cm-outbound benchmark run --agent demo
```

The installed CLI writes to `./outputs/runs` under the current working directory,
never into the Python installation. `cm_outbound_bench.paths.data_path` resolves
packaged data from `cm_outbound_bench/data` inside the wheel and falls back to the
source checkout's `data/` directory, and `tests/test_packaging.py` pins the
`force-include` layout that puts it there.
