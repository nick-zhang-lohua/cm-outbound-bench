# Repository Inventory

Inventory of `cm-outbound-bench` as prepared for the private publication on
`research/milestone-1.2-clean`. Every number below was measured from the working tree,
not copied from an earlier document.

| Property | Value |
|---|---|
| Repository root | `D:/flowchart-baseline-dist/cm-outbound-bench` |
| Git remote | `origin` → `https://github.com/nick-zhang-lohua/cm-outbound-bench.git` |
| Baseline commit | `1d2f5d4789437ba38b5c36bf95feea832399e20c` — `chore: establish v1.0.0 baseline` |
| Original branch | `main` (intact, unmodified) |
| Preparation branch | `research/milestone-1.2-clean` (already existed at the baseline commit; not reset) |
| Tracked files at baseline | 1,327 |
| Files in the prepared tree (not ignored) | 5,071 |
| Python source | 97 files, 34,609 lines |
| Python tests | 74 files, 23,148 lines |
| Package version | `1.0.0` (`pyproject.toml`), benchmark version `0.2.0` for decision artifacts |

---

## 1. Top-level structure

```text
cm-outbound-bench/
├── .github/                     # NOT present — see §6
├── .gitignore                   # redesigned for processed-data publication
├── .env.example                 # credential template, placeholders only
├── README.md                    # research objective, both benchmarks, datasets, status
├── pyproject.toml               # hatchling build, pydantic-only runtime dependency
├── providers.example.toml       # sanitized provider profiles (no credentials)
├── uv.lock                      # resolved dependency lock
├── configs/                     # dataset, split, baseline-agent, judge configuration
├── data_pipeline/               # protected v2 de-identification pipeline + docs
├── docs/                        # architecture, privacy, annotation, reports
├── prompts/                     # frozen annotation and scoring prompt files
├── scripts/                     # audit_gold_integrity.py
├── src/cm_outbound_bench/       # the package (7 sub-packages)
├── tests/                       # 74 test modules
├── data/                        # legacy synthetic data + processed decision data
└── outputs/                     # legacy synthetic evidence + Group A artifacts
```

## 2. Module classification

### 2.1 Shared infrastructure (both benchmarks)

| Module | Role |
|---|---|
| `src/cm_outbound_bench/cli.py` | Single entry point: `corpus`, `benchmark`, `decision`, plus legacy `list-tasks` / `run` / `run-all` |
| `providers/` (`base`, `config`, `http`, `openai_compatible`, `registry`) | Provider-independent OpenAI-compatible transport; no vendor SDK, no hardcoded URL |
| `paths.py` | Package data-root resolution with environment overrides |

### 2.2 Legacy Synthetic Benchmark (must be preserved)

| Module | Role |
|---|---|
| `agent.py`, `llm_agent.py` | Agent interfaces and native/JSON-action LLM agent |
| `simulator.py` | **Scripted customer simulator** — used only by the legacy benchmark |
| `environment.py`, `runner.py`, `evaluator.py` | Stateful environment, episode runner, deterministic evaluator |
| `policy.py`, `tasks.py` | Policy loading and synthetic Task model |
| `benchmark/` (14 modules) | `simulator`, `state`, `toolkits`, `domains`, `metrics`, `replay`, `trace`, `checks`, `answer_facts`, `execution`, `demo_agent`, `tasks`, `versions`, `cli` |
| `domain/` (`data_model`, `tools`) | Domain models and tool definitions |
| `data/tasks/`, `data/policy/`, `data/synthetic/` | Synthetic Task catalogue (6 domains, 14 tasks), policies, runtime DB and initial states |

### 2.3 Real-Transcript Decision Benchmark (new research line)

| Module | Role |
|---|---|
| `transcripts/` (`ingestion`, `reconstruction`, `splitting`, `fingerprint`, `models`, `store`, `validation`) | Transcript ingestion, conversation reconstruction, channel preservation, grouped splitting |
| `decisions/schema.py` | `PublicTask` / `PrivateTaskReference` models and `FORBIDDEN_PUBLIC_KEYS` |
| `decisions/store.py` | `TaskStore`; `agent_visible_payload()` is the only prompt path |
| `decisions/builder.py` | Decision-point Task construction |
| `decisions/runner.py`, `decisions/agent.py`, `decisions/agent_prompt.py` | Decision Runner and provider-independent Agent interface |
| `decisions/evaluator.py` | Technical evaluator |
| `decisions/channels.py` | Channel 1/2 production mapping + hypothesis catalogue for 3/4/5 |
| `decisions/gold/` (11 modules) | Ground Truth generation, V4 Pro adjudication, storage, validation, freeze |
| `decisions/scoring/` (7 modules) | Blind two-pass output scoring, statistics, reports |
| `decisions/annotation*.py`, `agreement.py`, `sampling.py` | Annotation workflow, agreement, reproducible sampling |
| `decisions/audit.py`, `channel_audit.py`, `identity_audit.py`, `business_sources.py`, `verification.py` | Read-only governance audits |
| `decisions/trace.py`, `config.py`, `paths.py` | Trace writing, configuration, artifact paths |
| `decisions/business_report.py`, `cli.py`, `cli_extra.py` | Reports and CLI registration |

`decisions/` has **no import path to `simulator.py`, `environment.py` or
`benchmark/`**. The Real-Transcript Decision Benchmark does not invoke the Legacy
Simulator; the legacy simulator is retained for the synthetic benchmark.

### 2.4 Data pipeline (protected)

`data_pipeline/deidentify.py`, `privacy_scan.py` — offline, standard-library-only
de-identification of the raw TXT export. Deliberately separate from benchmark runtime
code and excluded from `ruff` scope per `pyproject.toml`. **Not modified** in this
pass.

## 3. Essential files

| File | Why it matters |
|---|---|
| `pyproject.toml` | Build config, console script, pytest/ruff settings, wheel force-include of `data/{synthetic,tasks,policy}` |
| `configs/dataset.toml`, `configs/split.toml`, `configs/baseline_agent.toml`, `configs/decision_annotation.toml` | Reproduction-critical: artifact paths, split seed/targets, baseline model parameters, judge configuration |
| `prompts/*_v1.md` (6 files) | Frozen prompt wording; every Gold Label prompt hash depends on them |
| `scripts/audit_gold_integrity.py` | Offline re-verification of 1,600 annotation passes and both frozen hashes |
| `.gitignore` | Enforces the raw-vs-processed boundary reviewed in `gitignore_audit.md` |
| `.env.example` | Credential template; contains placeholders only |
| `providers.example.toml` | Sanitized provider profiles; credentials referenced by env var name |

## 4. Generated artifacts (present, inspected, classified)

| Path | Files | Bytes | Nature |
|---|---:|---:|---|
| `data/decision/` | 20 | 70,203,459 | Pipeline output: conversations, Tasks, references, split, samples, packets |
| `outputs/decision/` | 3,621 | 24,326,637 | Gold Labels, annotation passes, scoring records, traces, reports |
| `outputs/live_llm_validation/` | 1,040 | tracked | Legacy synthetic live-validation evidence |
| `outputs/live_llm_full/`, `outputs/live_llm_smoke/`, `outputs/runs/` | 140 | tracked | Legacy synthetic evidence |

Per-file detail: `github_dataset_upload_manifest.md`.

## 5. Files cleaned in this pass

Only demonstrably disposable development artifacts were removed. No source, test,
data, output, or user-owned file was deleted.

| Removed | Type |
|---|---|
| `src/cm_outbound_bench/**/__pycache__` (9 directories) | Byte-code cache |
| `tests/__pycache__` | Byte-code cache |
| `data_pipeline/__pycache__` | Byte-code cache |
| `.ruff_cache/` | Linter cache |
| `pytest-of-Nick Zhang/` | pytest `tmp_path` scratch tree |
| `outputs/tmp/` at the end of the pass | Local audit scratch created by this review |

Local virtual environments (`.venv/`, `.venv-win/`) were **kept** because they are
the working interpreter, are git-ignored, and deleting them would destroy the
researcher's environment. `providers.toml` was **kept** as local configuration; it is
git-ignored.

## 6. Deliberate omissions

| Item | Decision |
|---|---|
| `.github/` workflows | **Not added.** A CI workflow cannot be validated against the private repository or its runner images from this environment, and an unvalidated workflow that fails on the supervisor's first push would be worse than none. The offline command set in `README.md` §Testing and Reproduction covers the same checks locally. |
| Git LFS configuration | **Not added.** No file approaches the 50 MB/100 MB thresholds (largest is 41.9 MB); see `github_release_readiness.md` §15. |
| Dataset re-splitting or regeneration | **Not done**, by instruction. Split assignments, Task contents and Gold Labels are byte-identical to the frozen state. |
| New sanitized dataset versions | **Not produced.** The de-identification inspection found no residual content identifiers requiring an approved derivative. |

## 7. Files preserved deliberately

* the entire Legacy Synthetic Benchmark (agent, simulator, environment, evaluator,
  toolkits, `data/tasks/`, `data/policy/`, `data/synthetic/`);
* all 1,180 tracked legacy validation artifacts under `outputs/`;
* every Milestone 1 / 1.1 / 1.2 research artifact under `data/decision/` and
  `outputs/decision/`;
* both frozen Gold Label files, byte-identical (hashes in
  `github_dataset_upload_manifest.md` §5);
* all uncommitted work that existed before this pass: 7 modified files and 40
  untracked source/test/doc/config/prompt paths.
