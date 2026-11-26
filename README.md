# China Mobile Outbound Benchmark

The China Mobile Outbound Benchmark is an independent engineering benchmark for LLM
agents in Chinese-language China Mobile outbound customer-service scenarios. It evaluates
whether an agent can hold a multi-turn conversation, use business tools correctly, respect
identity and consent rules, and finish with factually correct statements.

Each episode runs against a simulated operating environment: a scripted customer,
a stateful synthetic business-support database, domain-scoped toolkits, written
policies, and a deterministic evaluator that scores both the final business state
and what the agent told the customer. Six domains and fourteen candidate tasks
ship with the environment, and every episode persists a full trace for audit.

The design is inspired by τ²-bench but independent: it does not import `tau2`,
depend on `tau2-bench`, or modify a τ²-bench checkout, and it is **not** an
official China Mobile system, nor is it endorsed by China Mobile.

## Key Features

- **Six domains, fourteen candidate tasks**, all `CANDIDATE` with `SYNTHETIC_BENCHMARK_ASSUMPTION` provenance.
- **Scripted customer simulator** with deterministic, bilingual trigger detection.
- **Tool-using LLM agent** with native function calling or a JSON action fallback.
- **Stateful business environment**: accounts, plans, billing, offers, subscriptions and notifications.
- **Identity and consent enforcement**: protected data needs verification; consent is recorded by the runtime, never passed as a tool argument.
- **Trace-based evaluation** over final state, tool trace, policy and Answer Facts, plus read-only re-evaluation and replay.
- **Provider-agnostic integration**: any OpenAI-compatible endpoint, with no vendor name, URL or SDK hardcoded.

## Real-Transcript Decision Benchmark (Group A)

Alongside the synthetic scenario benchmark, the package ships a second,
independent benchmark built on the **real de-identified outbound corpus**:
`cm-outbound decision ...`. There is no simulated customer and no synthetic
conversation: a task is a decision point cut from a real conversation prefix, and
the agent never sees a future turn, the historical agent's next response, or an
evaluator label.

**The Real-Transcript Decision Benchmark does not invoke the Legacy Simulator.**
The legacy synthetic benchmark and its scripted-customer simulator remain in the
repository and stay fully functional; the two architectures share only the package,
the provider transport and the privacy rules. See [Architectural Separation](#architectural-separation).

```bash
uv run cm-outbound decision validate-data     # corpus counts, quality, duplicates
uv run cm-outbound decision build-calls       # reconstruct complete calls
uv run cm-outbound decision split             # call-level Train-F/Train-R/Dev/Test
uv run cm-outbound decision build-tasks       # decision-point candidates
uv run cm-outbound decision check             # artifacts + prefix-leakage audit
uv run cm-outbound decision run --agent live --split dev --task-limit 12
uv run cm-outbound decision evaluate --run-set <dir> --tasks-dir data/decision/tasks
uv run cm-outbound decision report --run-set <dir>
```

Scope and honesty rules: Test is isolated behind an explicit opt-in; before
Milestone 1.2 there is no business ground truth, so tasks start `unscored` and the
business success rate is reported as **not available** rather than as zero. Groups
B/C, Flowchart Generation and RSI are deliberately not implemented. Full reference:
[Real-Transcript Decision Benchmark](docs/decision_benchmark.md).

### Milestone 1.1: trustworthy business evaluation

Milestone 1.1 added the evaluation methodology and the infrastructure around it:

```bash
uv run cm-outbound decision audit-identity           # conversation identity + integrity
uv run cm-outbound decision audit-channels           # channels 3/4/5 human-review package
uv run cm-outbound decision audit-business-sources   # business reference inventory
uv run cm-outbound decision sample --purpose dev_baseline_evaluation_sample --verify
uv run cm-outbound decision export-annotations --plan <plan.json>
uv run cm-outbound decision import-annotations --file annotator_a.jsonl
uv run cm-outbound decision agreement
uv run cm-outbound decision adjudication-worklist
uv run cm-outbound decision apply-annotations
uv run cm-outbound decision run --agent live --split dev --sample-plan <plan.json>
uv run cm-outbound decision business-baseline --run-set <dir>
```

Key properties:

* two evaluation claims reported separately —
  `expert_adjudicated_decision_success_rate` and
  `policy_verified_decision_success_rate` — never averaged into one number;
* multiple acceptable decisions, conditional branches and clarification answers are
  first-class, and exact-text matching is never the criterion;
* an LLM pre-annotation without the Milestone 1.2 gold gate is `candidate` only and
  can never become a scored label; the frozen V4 Pro adjudicated reference is the
  one exception, and it is recorded as model-generated rather than human-reviewed;
* channels 3, 4 and 5 stay `unknown` with an evidence-backed hypothesis catalogue
  until a researcher approves an interpretation;
* no authoritative China Mobile policy document exists in this workspace, so the
  policy-verified tier is reported as unavailable rather than invented.

Documentation: [Annotation Protocol](docs/annotation/business_decision_annotation_protocol_v1.md) ·
[Annotation Guidelines](docs/annotation/annotation_guidelines_v1.md) ·
[Annotation Schema](docs/annotation/annotation_schema.md) ·
[Channel Semantics Audit](docs/reports/channel_semantics_audit.md).

### Milestone 1.2: frozen LLM-Gold Ground Truth and the Group A business number

Milestone 1.2 adopted an explicit research methodology: **DeepSeek V4 Pro
(`deepseek-v4-pro`) is the official Ground Truth generator and adjudicator, and
its final frozen annotation is the benchmark Gold Label.** Human annotation is not
a prerequisite for scoring. Provenance is recorded on every reference, and
`policy_verified = false` everywhere because no authoritative China Mobile policy
source exists.

```bash
uv run cm-outbound decision annotation inspect     # samples, overlap, test lock
uv run cm-outbound decision annotation pilot       # 100 Train-R tasks, Pass A/B + adjudication
uv run cm-outbound decision annotation train-r     # 300 Train-R tasks
uv run cm-outbound decision annotation dev         # 400 Dev tasks
uv run cm-outbound decision annotation validate    # pre-freeze validation
uv run cm-outbound decision annotation freeze      # immutable hash-pinned freeze
uv run cm-outbound decision evaluate-group-a       # blind two-pass V4 Pro scoring
uv run cm-outbound decision report-group-a         # print saved metrics
```

Ground Truth `cm-outbound-llm-gold-v1` (frozen): two independent V4 Pro annotation
passes plus adjudication on the agent-visible prefix only, 400 Train-R and 400 Dev
references. The 400 existing Group A executions were scored blind against the
frozen Dev labels.

| Group A baseline (no flowchart, no workflow, no simulator) | Value | 95% CI |
|---|---:|---|
| Evaluated / scored / unscorable | 400 / 391 / 9 | — |
| LLM-Gold Decision Success Rate (DSR) | 0.83376 | 0.795918 – 0.868687 |
| Decision Acceptance Rate (DAR) | 0.976982 | 0.961637 – 0.989796 |
| Partial Decision Rate (PDR) | 0.143223 | — |
| Incorrect Decision Rate (IDR) | 0.023018 | 0.007692 – 0.038363 |
| Scored task coverage | 0.9775 | — |
| Critical errors | 9 | — |

Reading rules, stated because the numbers are easy to over-read:

* the reference is a **model-generated, permissive accepted-decision set** (mean
  2.76 acceptable alternatives per Dev task), so DSR measures agreement with that
  set, not conformance to one canonical answer;
* confidence intervals capture **sampling uncertainty only**, not systematic error
  of the LLM labels or the LLM judge;
* clarification / required-step / branch metrics are **lexical proxies**, reported
  as lower-bound diagnostics, not semantic accuracy;
* `policy_verified_decision_success_rate` remains unavailable.

The processed datasets, split artifacts, frozen Gold Labels and Group A evaluation
records ship with this **private** repository so that the supervisor can review and
reproduce the research. They are conversation-derived research artifacts, not public
data: the original raw transcripts and the wider de-identified corpus remain outside
the repository, and the material must stay inside the owner/supervisor access
boundary. See [Dataset Structure](#dataset-structure) and
[Data Privacy](#data-privacy).

## Architectural Separation

The repository contains two benchmark components that are deliberately modular and
independent. Preserving both is a project requirement.

| | Legacy Synthetic Benchmark | Real-Transcript Decision Benchmark |
|---|---|---|
| Origin of the conversation | Scripted, authored in `data/tasks/*.json` | Cut from a real de-identified outbound conversation |
| Counterparty | Customer **Simulator** (`simulator.py`) | No simulator; the prefix is static text |
| Business backend | Stateful synthetic database + domain toolkits | None; `tool_intent` must stay `null` |
| Evaluation | State / tool-trace / policy / Answer Facts | Decision agreement against frozen Gold Labels |
| Entry point | `cm-outbound benchmark ...` | `cm-outbound decision ...` |
| Data | `data/tasks/`, `data/policy/`, `data/synthetic/` | `data/decision/`, `outputs/decision/` |

The two components share the installed package, the OpenAI-compatible provider
transport and the privacy rules. The decision pipeline has no import path to the
simulator, the synthetic environment or the synthetic task catalogue; removing the
legacy simulator would break the synthetic benchmark and is explicitly out of scope.
The precise statement is therefore **"the Real-Transcript Decision Benchmark does not
invoke the Legacy Simulator"** — not "this repository contains no simulator".

## Dataset Structure

Processed artifacts are included in this private repository. All conversation text is
de-identified (`<PHONE>`, `<NAME>`, `<ADDRESS>`, `<ACCOUNT>`, … placeholders), and
every Task is built from a real conversation prefix.

```text
data/
├── tasks/            # legacy synthetic Task catalogue (6 domains, 14 tasks)
├── policy/           # legacy synthetic policy documents
├── synthetic/        # legacy synthetic runtime database + initial states
└── decision/
    ├── calls/        # rebuilt de-identified conversations (Agent-visible source)
    ├── tasks/        # tasks.jsonl (Agent-visible) + references.jsonl (private)
    ├── splits/       # call-level split manifest (seed, duplicate groups)
    ├── samples/      # fixed annotation / evaluation sample plans
    ├── annotations/  # annotation packets and their manifests
    └── business_sources/  # business reference inventory

outputs/decision/
├── annotations/      # frozen Gold Labels, Pass A/B, comparisons, adjudications
├── evaluation/       # blind Group A scoring records and adjudications
├── traces/           # Group A execution traces (400 Dev tasks)
└── reports/          # aggregate reports (no conversation text)
```

Split assignment (call-level, seeded, duplicate-group-safe; unchanged since
Milestone 1):

| Dataset | Target share | Calls | Decision-point Tasks |
|---|---:|---:|---:|
| Train-F | 26.67% | 1,207 | 2,726 |
| Train-R | 26.67% | 1,206 | 2,695 |
| Dev | 13.33% | 603 | 1,314 |
| Test | 33.33% | 1,508 | 3,381 |
| **Total** | 100% | **4,524** | **10,116** |

Data access boundaries are logical and technical, not merely directory names:

| Boundary | Contents | Rule |
|---|---|---|
| **Agent-visible** | `data/decision/tasks/tasks.jsonl` | Only these fields may reach an evaluated agent: `task_id`, `task_type`, `business_domain`, `visible_context`, `agent_task.instruction`, `schema_version` |
| **Private Evaluator** | `data/decision/tasks/references.jsonl`, `outputs/decision/annotations/**/final_references.jsonl` | Split membership, cutoffs, hidden future-turn counts and expected decisions; never loaded as Task input |
| **Research audit** | `outputs/decision/traces/`, `evaluation/`, `reports/`, annotation passes | Frozen run records and aggregates for review and reproduction |

Task schema versions: Tasks `1.0`, private references `1.0`, frozen Ground Truth
`cm-outbound-llm-gold-v1`. SHA-256 manifests for every published dataset file are in
[docs/reports/github_dataset_upload_manifest.md](docs/reports/github_dataset_upload_manifest.md).

## Data Privacy

The repository is private and shared only with the supervisor, but private hosting is
not by itself a data-use authorisation.

* **Included**: de-identified processed conversations, Agent-visible Tasks, split
  artifacts, frozen Gold Labels, annotation/adjudication records, Group A traces,
  scoring records and aggregate reports.
* **Excluded**: the original raw TXT transcript export, raw audio, the wider
  `cm_outbound_v2` corpus, confidential databases, credentials and local caches.
  Benchmark code never opens the raw TXT files.
* **Handling**: keep the checkout inside the authorised research environment; do not
  paste conversation text into external tools, chat interfaces or cloud services; do
  not fork the repository to a wider audience without renewed permission.

Residual re-identification risk remains after masking (names without honorifics,
addresses without markers, business context that narrows a customer down). Before any
wider distribution, re-confirm authorisation with the data owner — see
[Data Privacy and Provenance Limitations](docs/data_privacy.md) and the readiness
report's outstanding-governance section.

## Research Status

| Milestone / component | State |
|---|---|
| Milestone 1 — decision-point pipeline, split, isolation | **Completed** |
| Milestone 1.1 — evaluation methodology, annotation infrastructure, channel audit | **Completed** |
| Milestone 1.2 — frozen V4 Pro Gold Labels, blind Group A scoring | **Completed** |
| Group A Baseline v1 (no flowchart, no workflow, no simulator) | **Established** (400 Dev tasks) |
| Group B | **Not implemented** |
| Group C | **Not implemented** |
| Flowchart Generation | **Not implemented** |
| RSI feedback loop | **Not implemented** |
| Channel 3 / 4 / 5 semantics | **Unresolved** — kept as `other`, no automatic remapping |
| Test split execution and annotation | **Not executed in this milestone** — 3,381 Tasks built but untouched |

Nothing in this README advertises unimplemented functionality as available.



| Domain | Description |
|---|---|
| D01 | Product and Plan Information |
| D02 | Promotion and Offer |
| D03 | Billing and Recharge |
| D04 | Subscription and Account Lifecycle |
| D05 | Identity and Ownership |
| D06 | Notification and Fulfillment |

## Architecture

```text
Domain / Task → Initial State → Environment → Simulator ↔ Agent → Tools
              → State Transitions → Trace → Evaluator
```

`data/tasks/` declares each task's objective, scripted customer, tools, state
expectations and Answer Facts. The environment loads a synthetic initial state and
exposes it through domain toolkits; the simulator plays the customer; the agent is
pluggable; the evaluator re-checks the episode against final state, tool trace,
consent and stated facts.

## Installation

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cd cm-outbound-bench
uv sync
```

`uv sync` installs Pydantic (the only runtime dependency), the dev tools and the
`cm-outbound` console script; commands also work as `python -m cm_outbound_bench.cli ...`.

## Provider Configuration

Copy the sanitized example file and edit the copy. `providers.toml` is
git-ignored and stores **no credentials**, only the environment variable name.

```bash
cp providers.example.toml providers.toml
export DEEPSEEK_API_KEY=...       # the variable named by `api_key_env`
```

[providers.example.toml](providers.example.toml) ships DeepSeek Flash, relay and
JSON-action profiles; labels and hosts are arbitrary. Supply your own credentials.

A sanitized [`.env.example`](.env.example) lists the credential variable and the
optional `CM_OUTBOUND_*` overrides. It is a template, not an auto-loaded file:
copy it to `.env` and source it, or export the variables in your shell.

## Quick Start

```bash
# 1. Verify the catalogue (14 tasks across 6 domains)
uv run cm-outbound benchmark verify
# 2. Run every task with the deterministic demo agent (no provider call)
uv run cm-outbound benchmark run --agent demo --output-dir outputs/runs/demo-local
# 3. Run one domain against a live model (needs providers.toml and the key)
uv run cm-outbound benchmark run --domain D02 --agent llm --provider deepseek \
    --output-dir outputs/live/D02-r1 --seed 20261122
# 4. Offline checks
uv run pytest -q
uv run ruff check src tests
uv run python -m compileall -q src tests
```

Use a fresh output directory per experiment; every episode writes a new `RUN-<id>`.

## Outputs and Results

| Path | Contents |
|---|---|
| `<out>/RUN-<id>.json` | Manifest: identity, versions and hashes, status, checks, usage, trace link |
| `<out>/RUN-<id>/trace.json` | Full episode: actions, conversation, tool calls, consent events, final state, recorded evaluation |

```bash
uv run cm-outbound benchmark evaluate --trace <out>/RUN-<id>/trace.json
uv run cm-outbound benchmark replay --trace <out>/RUN-<id>/trace.json
uv run cm-outbound benchmark aggregate --dir outputs/runs/final-demo-v2.4-20261122
```

The six clean final rounds (84 episodes) are under
`outputs/live_llm_validation/deepseek/`; superseded rounds and provider-outage
attempts are retained as historical evidence. See the [Final Validation
Matrix](docs/reports/validation_matrix_final.md) and [Release Readiness
Report](docs/release_readiness_report.md).

## Release Status and Limitations

| Component | Value |
|---|---|
| Benchmark | `1.0.0` |
| Shared prompt | `system-v4` |
| Evaluator | `2.4` — `a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2` |
| Catalogue (legacy synthetic) | 6 domains, 14 tasks, all `CANDIDATE` |
| Live validation (legacy synthetic) | 84/84 episodes passed in 6 clean rounds over 3 seeds |
| Offline suite (Windows, CPython 3.12.14) | 1,528 tests + 25 subtests: **1,520 pass, 8 fail** (see below) |
| Decision-point Task catalogue | 10,116 Tasks (Train-F 2,726 / Train-R 2,695 / Dev 1,314 / Test 3,381) |

The 8 failures are environment guards, not benchmark logic, and none of them is
weakened to make the suite look green:

| # | Failing test | Cause |
|---:|---|---|
| 5 | `test_deidentify.py::KeyTests` (4 tests) and `::PathSafetyTests::test_symlink_into_input_rejected` | POSIX `0600`/`0700` assertions and symlink creation. Windows returns `0666` for `chmod` and needs elevation for `os.symlink` (`WinError 1314`). These are the de-identification pipeline's privacy guarantees; relaxing them would reduce assurance, so they stay strict and Windows-only failures are accepted. |
| 2 | `test_deidentify.py::EndToEndTests::test_run_publishes_valid_dataset` and `::test_path_rejection_happens_before_writes` | `data_pipeline/deidentify.py` requires CPython **exactly 3.12.16** and exits `8` before reading any source on another interpreter. This machine has 3.12.14, so the two end-to-end tests see exit `8` instead of `0`/`7`. The pin exists to keep de-identification output byte-reproducible and is not relaxed here. |
| 1 | `test_benchmark_lifecycle.py::test_wall_clock_deadline_is_enforced_between_actions` | The test uses a `1e-9` second budget. `time.monotonic()` on Windows has coarser resolution, so the elapsed time stays `0.0`. Production deadlines are measured in seconds and are unaffected; the fix belongs in the test's clock injection, not the runner. |

Reproduce exactly:

```bash
python -m pytest -q          # 1,520 passed, 8 failed, 25 subtests passed
```

The 1,520 passing tests include the decision-isolation, Gold-Gate, frozen-hash and
reproducibility suites, so the research invariants are covered independently of the
eight environment failures.

These are engineering results, not a claim of business or statistical validity: all
legacy synthetic tasks are human-unreviewed candidates with synthetic rules, live
validation used one provider/model pair (`deepseek-flash`, `temperature=0.2`), and
repeated rounds are a stability observation, not a confidence interval. The framework
cannot place calls, send SMS, charge money or provision service. The processed
decision datasets are included in this private repository; the original raw corpus is
not (see [Data Privacy](#data-privacy)).

## Testing and Reproduction

No network call and no API credential are required for any check below.

```bash
# 1. Import smoke test and CLI surface
python -c "import cm_outbound_bench, cm_outbound_bench.cli; print('ok')"
python -m cm_outbound_bench.cli --help

# 2. Offline test suite and lint
python -m pytest -q
python -m ruff check src tests

# 3. Package build
python -m build            # or: uv build

# 4. Frozen-artifact integrity (no API call, no regeneration)
python scripts/audit_gold_integrity.py

# 5. Dataset integrity through the shipped CLI (read-only)
python -m cm_outbound_bench.cli decision check
```

Reproducing the benchmark from the **shipped** processed data needs no corpus and no
re-annotation: `data/decision/calls/calls.jsonl`, `data/decision/splits/` and
`data/decision/tasks/` are the pipeline outputs, and
`outputs/decision/annotations/**/final_references.jsonl` are the frozen Gold Labels.
Regenerating them from the raw TXT export is **not** part of this milestone and would
require the confidential corpus plus CPython 3.12.16; it is documented in
[docs/decision_benchmark.md](docs/decision_benchmark.md) §8 for completeness only.

## Documentation

| Document | Purpose |
|---|---|
| [Release Readiness Report](docs/release_readiness_report.md) | Release status and engineering verification |
| [Final Validation Matrix](docs/reports/validation_matrix_final.md) | Per-task live validation evidence |
| [Scenario Catalog](docs/scenario_catalog.md) | Complete task catalogue with evidence references |
| [Task Authoring Guide](docs/task_authoring.md) | Task schema, tool, simulator and evaluator reference |
| [Changelog](docs/CHANGELOG.md) | Significant changes and version history |
| [Architecture](docs/architecture.md) · [Execution Guide](docs/benchmark_execution.md) | Components, trust boundaries, CLI, providers, traces and replay |
| [Decision Benchmark](docs/decision_benchmark.md) | Real-transcript Group A pipeline: data, split, tasks, isolation, evaluation |
| [Channel Semantics Audit](docs/reports/channel_semantics_audit.md) | Channels 3/4/5 evidence, hypotheses and benchmark impact |
| [Annotation Protocol](docs/annotation/business_decision_annotation_protocol_v1.md) · [Guidelines](docs/annotation/annotation_guidelines_v1.md) · [Schema](docs/annotation/annotation_schema.md) | Business decision annotation: process, substantive rules and data model |
| [Milestone 1.1 Readiness](docs/reports/milestone_1_1_research_readiness_report.md) | Research readiness, remaining limitations and the next milestone |
| [Group A Business Baseline](docs/reports/group_a_business_baseline_report.md) | Group A baseline: technical results and business-metric availability |
| [Milestone 1.2 Readiness](docs/reports/milestone_1_2_research_readiness_report.md) | LLM-Gold Ground Truth, annotation consistency, Group A LLM-Gold business evaluation |
| [LLM-Gold Protocol](docs/annotation/llm_gold_ground_truth_protocol_v1.md) · [Decision Guidelines](docs/annotation/llm_business_decision_guidelines_v1.md) · [Output Scoring Rubric](docs/annotation/llm_output_scoring_rubric_v1.md) | DeepSeek V4 Pro Gold Label construction, substantive rules and output scoring |
| [Corpus Database](docs/corpus_database.md) · [Domain Catalog](docs/domain_catalog.md) · [Runtime Database](docs/runtime_database.md) | Corpus schema, taxonomy, entity model and initial states |
| [Data Privacy](docs/data_privacy.md) · [Data Pipeline](data_pipeline/README.md) | Provenance, data handling and the v2 de-identification boundary |
| [Release configuration](docs/development/release_config.json) | Frozen task, prompt, policy and evaluator hashes |
| [Repository Inventory](docs/reports/repository_inventory.md) | Directory structure, module classification and what was cleaned or preserved |
| [GitHub Dataset Upload Manifest](docs/reports/github_dataset_upload_manifest.md) | Every dataset file: path, split, size, records, SHA-256, classification, eligibility |
| [Publishable Manifest](docs/reports/github_publishable_manifest.txt) | Flat list of files approved for manual staging |
| [GitHub Security Audit](docs/reports/github_security_audit.md) | Secret scan, raw-transcript exclusion, de-identification inspection, history findings |
| [Gitignore Audit](docs/reports/gitignore_audit.md) | Why each path is tracked or ignored, with `git check-ignore` evidence |
| [Release Readiness](docs/reports/github_release_readiness.md) | Test/lint/build results, frozen hashes, large-file issues, open governance items, manual publication steps |
