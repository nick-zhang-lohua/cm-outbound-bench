# Architecture

The CM Outbound Benchmark is a standalone, τ²-inspired Agent benchmark for
China Mobile outbound telecommunications business scenarios. It is independent:
it does not import `tau2`, does not depend on `tau2-bench`, and does not modify
the sibling `tau2-bench` checkout.

Two databases, two boundaries, one runtime:

```text
Raw TXT (read-only)
      │  existing de-identification pipeline (not invoked by the benchmark)
      ▼
De-identified corpus (read-only, confidential)
      │  `cm-outbound corpus import`
      ▼
Corpus database (SQLite, research only)          ← never reachable from the agent
      │  `corpus classify` → candidate domain assignments
      │  `corpus coverage-report`, local review
      ▼
Evidence references (opaque transcript ids)
      │  scenario catalogue (candidate, provenance-labelled)
      ▼
Benchmark task + explicit initial state
      │
      ▼
Benchmark runtime ──────────────── Agent (DemoAgent / BenchmarkDemoAgent / LLM)
   │   environment (isolated DB copy, policy, toolkit, trace)
   │   simulator (declarative script)
   │   runner (one loop)
   │   evaluator (shared checks + domain pack)
   ▼
trace.json (per episode) + RUN-*.json manifest → aggregate / compare
   │
   ├─ evaluate --trace   (re-run the evaluator, no agent)
   └─ replay  --trace    (re-execute the recorded actions, no provider)
```

---

## 1. Packages

```text
src/cm_outbound_bench/
├── agent.py              Agent protocol, actions, AgentContext, DemoAgent (legacy)
├── environment.py        Isolated DB copy, policy, toolkit, trace, blocked-tool record
├── runner.py             One conversation loop + EpisodeSpec + MarketingRunner adapter
├── evaluator.py          Shared evaluator, shared checks, marketing check pack
├── simulator.py          Legacy five-persona marketing simulator
├── tasks.py              Legacy marketing task model and loader
├── policy.py             Legacy marketing policy loader
├── llm_agent.py          Provider-neutral LLM agent
├── providers/            Provider protocol, TOML profiles, HTTP, OpenAI-compatible adapter
├── domain/
│   ├── data_model.py     One synthetic runtime database model (legacy + multi-domain)
│   └── tools.py          The original seven marketing tools
├── corpus/
│   ├── schema.py         Versioned SQLite DDL and taxonomy seeds
│   ├── manifest.py       Corpus manifest verification
│   ├── importer.py       Streaming, validated, atomic import
│   ├── database.py       Read-only corpus query API (text opt-in)
│   ├── taxonomy.py       Versioned domain taxonomy
│   ├── classification.py Deterministic multi-label candidate classifier
│   ├── report.py         Aggregate coverage reporting
│   ├── review.py         Local-only human review workflow
│   └── cli.py            `cm-outbound corpus ...`
└── benchmark/
    ├── tasks.py          Declarative BenchmarkTask, evidence refs, expectations
    ├── state.py          Initial states, hashing, isolation
    ├── domains.py        Domain registry (tasks, states, policy, checks)
    ├── simulator.py      Declarative scripted customer simulator
    ├── toolkits.py       Composed domain toolkit + runner-only action consent
    ├── demo_agent.py     Deterministic baseline for D01–D06
    ├── execution.py      Task → state → runner → evaluator orchestration
    ├── checks.py         Domain-specific deterministic check packs
    ├── answer_facts.py   Deterministic required/forbidden answer facts
    ├── trace.py          Versioned episode trace, atomic write, integrity
    ├── replay.py         Trace re-evaluation and deterministic replay
    ├── metrics.py        Run manifests, aggregation and comparison
    └── cli.py            `cm-outbound benchmark ...`
```

---

## 2. One runtime, no duplicated frameworks

* **One loop.** `runner.Runner.run` is the only conversation loop.
  `MarketingRunner` is a thin adapter that builds an `EpisodeSpec` from the legacy
  `MarketingTask`; domain tasks build the same `EpisodeSpec` in
  `benchmark/execution.py`.
* **One environment.** `environment.CmOutboundEnvironment` owns the isolated DB
  copy, policy, toolkit and trace for every domain. It optionally takes a
  toolkit factory; the legacy path keeps `CmOutboundTools`.
* **One evaluator.** `evaluator.evaluate_episode` runs the shared checks and then
  an optional domain check pack. `evaluate_task` (legacy marketing) and
  `evaluate_benchmark_task` (declarative tasks) are both thin adapters over it.
* **One database model.** `CmOutboundDB` gained multi-domain entity lists with
  empty defaults, so the legacy fixture is untouched.
* **One consent mechanism per action class.** Offer enrollment uses the original
  runner-only `ConsentRecord`; other actions use runner-only
  `ActionConsentRecord`. No tool accepts a consent argument.

---

## 3. Trust boundaries

### Agent boundary

The agent receives only:

* the agent-visible task view (`domain`, `objective`, `phone_number_id`,
  `offer_id`);
* the policy text;
* the tool definitions allowed for that task;
* the conversation and tool results (defensive copies in a frozen snapshot);
* the current step.

It never receives the task id, customer id, simulator script, verification token,
expected outcomes, evaluator criteria, evidence references, transcript ids or
any corpus content. A test asserts that no evidence identifier appears in any
captured `AgentContext`.

### Tool boundary

Every tool returns a structured `ToolResult` with a stable error code. Protected
tools require a successful identity verification for that line in that episode.
Mutation tools additionally require a runner-recorded consent. A tool outside the
task allow-list is never executed: the attempt is recorded with
`tool_not_allowed` so the evaluator can penalise it.

### Provider boundary

Provider adapters receive only the normalized messages and tool definitions for
one decision. They cannot execute tools and never receive environment, simulator
or evaluation state. Errors are sanitized; secrets come from environment
variables only.

### Corpus boundary

`corpus/` has no import path to `environment`, `runner`, `agent` or
`benchmark/`. The corpus database is opened read-only with `PRAGMA query_only`,
and transcript text is withheld unless a caller explicitly requests it.

---

## 4. Evaluation model

Success is deterministic and derived from four independent dimensions:

1. **state** — the final synthetic database (expected and forbidden expectations);
2. **tool** — required tools and blocked attempts;
3. **policy** — ordering, consent, DNC, protected access and disclosure;
4. **answer** — explicit, task-authored required/forbidden facts about what the
   agent told the customer.

Domain packs add business consistency checks (category `domain`). `lifecycle`
checks cover termination and budgets.

Every check carries an applicability status. A check whose behaviour could not
occur in a task is reported as `NOT_APPLICABLE`; it never contributes to success
and never counts as evidence that the behaviour was tested. `success` is the
conjunction of every applicable check and `partial_completion` divides by
applicable checks only.

The answer dimension is a bounded deterministic pattern layer
(`benchmark/answer_facts.py`), not a general natural-language truth judge. It
handles exact business values, documented wording alternatives and polarity
(including negation windows and documented idioms). A task that cannot express its
facts this way declares no assertions and the dimension is reported as
`NOT_APPLICABLE`. One heuristic over generated prose also exists (the D02
fee/validity disclosure check). No LLM judge is used for correctness.

Evaluation never trusts the agent's claims for state outcomes, and a stored trace
can be re-evaluated later without re-running the agent.

---

## 5. Data flow and privacy

| Artefact | Contains transcript text? | Location |
|---|---|---|
| Corpus database | Yes (`text_masked`, confidential) | `data/corpus_db/` — outside the package |
| Corpus reports | No (aggregate only) | `outputs/corpus/` (generated locally, not committed) |
| Scenario catalogue | No (opaque ids only) | `docs/scenario_catalog.md` |
| Benchmark tasks | No | `data/tasks/` |
| Initial states | No | `data/synthetic/states/` |
| Run manifests | No | `outputs/runs/RUN-*.json` |
| Episode traces | No (synthetic business state and agent messages only) | `outputs/runs/<run_id>/trace.json` |
| Agent context | No | in memory only |

The wheel never contains the corpus database, transcript text, the transcript-id
HMAC key or any entity mapping. `tests/test_packaging.py` asserts the packaged
data set.
