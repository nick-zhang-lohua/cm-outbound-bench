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

## Domains

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
| Catalogue | 6 domains, 14 tasks, all `CANDIDATE` |
| Live validation | 84/84 episodes passed in 6 clean rounds over 3 seeds |
| Offline suite | 1266 tests + 25 subtests |

These are engineering results, not a claim of business or statistical validity: all
tasks are human-unreviewed candidates, rules are synthetic assumptions, live
validation used one provider/model pair (`deepseek-flash`, `temperature=0.2`), and
repeated rounds are a stability observation, not a confidence interval. The
framework cannot place calls, send SMS, charge money or provision service, and the
corpus is confidential research material not distributed here.

## Documentation

| Document | Purpose |
|---|---|
| [Release Readiness Report](docs/release_readiness_report.md) | Release status and engineering verification |
| [Final Validation Matrix](docs/reports/validation_matrix_final.md) | Per-task live validation evidence |
| [Scenario Catalog](docs/scenario_catalog.md) | Complete task catalogue with evidence references |
| [Task Authoring Guide](docs/task_authoring.md) | Task schema, tool, simulator and evaluator reference |
| [Changelog](docs/CHANGELOG.md) | Significant changes and version history |
| [Architecture](docs/architecture.md) · [Execution Guide](docs/benchmark_execution.md) | Components, trust boundaries, CLI, providers, traces and replay |
| [Corpus Database](docs/corpus_database.md) · [Domain Catalog](docs/domain_catalog.md) · [Runtime Database](docs/runtime_database.md) | Corpus schema, taxonomy, entity model and initial states |
| [Data Privacy](docs/data_privacy.md) · [Data Pipeline](data_pipeline/README.md) | Provenance, data handling and the v2 de-identification boundary |
| [Release configuration](docs/development/release_config.json) | Frozen task, prompt, policy and evaluator hashes |
