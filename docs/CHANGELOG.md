# Changelog

## Final documentation consistency and evidence audit (2026-11-22)

Documentation-only close-out of the Engineering MVP delivery. No benchmark
behaviour, source, agent, simulator, task, policy, prompt, evaluator, schema,
manifest, trace or release evidence changed. All 453 manifests and 411 traces
remain byte-identical.

### Corrected — data pipeline documentation (v2 no-mapping model)

* `data_pipeline/README.md` rewritten as the entry point for the **current v2**
  pipeline (2.0.0): one-way HMAC transcript ids, **no mapping table**, no
  cross-transcript pseudonyms. The obsolete v1-era "protected mapping" design is
  explicitly marked historical; `cm_outbound_v1` is historical evidence only.
* `data_pipeline/PIPELINE.md` now records the F1–F11 repair evidence and the
  verified final-v2 run counts, preserving the engineering decisions from the
  removed `docs/deidentification_v2_report.md`.
* `docs/data_privacy.md`, `docs/corpus_database.md` and `docs/architecture.md`
  no longer describe per-source-file provenance through a source mapping: they
  state the one-way HMAC model, the absence of a mapping table and the resulting
  `source_file_outcomes_unavailable` gap.

### Corrected — dates, seeds and round identity

The `20261127` / `20261128` tokens in four stability directory names are random
seeds, not execution dates. All six clean rounds executed on 2026-11-22
(10:54–11:29 UTC); every manifest records `random_seed` and
`seed_status=applied`. `docs/release_readiness_report.md` §C.0 and the
validation-matrix provenance paragraph now list output-directory identifier,
seed and manifest execution window separately. No manifest has `started_at`
after 2026-11-22, so no recorded timestamp needed correction.

### Corrected — evidence accounting

Recomputed from `outputs/` (read-only):

| Population | Manifests | Traces | success | evaluation_failure | runtime_failure | no status |
|---|---:|---:|---:|---:|---:|---:|
| Historical | 369 | 327 | 253 | 49 | 25 | 42 |
| Final six clean rounds | 84 | 84 | 84 | 0 | 0 | 0 |
| **Combined** | **453** | **411** | **337** | **49** | **25** | **42** |

The earlier report presented `success 337` next to the historical manifest
total; 337 is the combined figure (253 historical + 84 final). The two
populations are now reported separately. 411/411 trace hashes match, with no
missing or unreferenced trace.

### Corrected — cleanup accounting

* The **five** orphaned `scripts/__pycache__/_probe_*.pyc` files are already
  included in the **283** cache files, not additional to them. The 283 comprise
  256 `.pyc` files (including the five) plus 27 `.pytest_cache`/`.ruff_cache`
  files.
* Totals: **30** substantive files (13 documents + 17 scripts) + **286**
  generated files (283 cache + 3 `dist/`) = **316** deleted files; **12**
  directories removed (10 cache directories + `dist/` + `docs/audit/`).
* Before/after (at cleanup): 1,643 → 1,327 files outside `.venv` (2,528 → 2,212
  including `.venv` and its three interpreter symlinks). No additional cleanup
  was run. This audit adds exactly one file (`.env.example`), so the current
  tree holds 1,328 files outside `.venv`.

### Corrected — README, `.env.example` and `uv` commands

* Root `README.md` is **154 lines / 120 non-blank** after this audit (it was 150
  lines / 117 non-blank at cleanup). The earlier delivery report's "1,200
  non-blank" was a typo; all README numeric claims were re-checked.
* Added `cm-outbound-bench/.env.example`: sanitized placeholders only, matching
  the `api_key_env` contract in `providers.example.toml` and the optional
  `CM_OUTBOUND_*` overrides. It is a template and is not auto-loaded.
* The documented `uv` wrappers were executed in an isolated copy with `uv 0.13.8`
  and CPython 3.12.16: `uv sync`, `uv run cm-outbound benchmark verify`,
  `uv run pytest -q`, `uv run ruff check src tests`,
  `uv run python -m compileall -q src tests`, the documented
  `benchmark run --agent demo` shape (14 manifests written to a temporary
  directory), and `benchmark evaluate` / `replay` / `aggregate` all exited 0.
  The committed `uv.lock` records project version `0.1.0` while
  `pyproject.toml` declares `1.0.0`, so `uv lock --check` reports it as out of
  date and `uv sync` refreshes it; the release tree's lock was deliberately not
  modified.

### Changed — report generator (documentation only)

* `scripts/validation_matrix.py` now derives a provenance paragraph from the
  manifests (output-directory identifier, random seeds and
  `started_at`/`completed_at`) before the table. Regenerating
  `validation_matrix_final.json` from the same runs is byte-identical, and the
  table rows are unchanged. The script is reporting-only; no benchmark code
  imports it.

### Verification

`1266 passed, 25 subtests`; `ruff check src tests` clean; `compileall -q src
tests` clean; `benchmark verify` = `Catalog OK: 14 tasks across 6 domains.`;
`scripts/release_config.py` output byte-identical to
`docs/development/release_config.json`; 411/411 trace file hashes verified and
411/411 traces re-load through `load_trace` (content hash + final-state hash)
with zero errors; the 84 final manifests match `release_config.json` on all 11
pinned fields (0 mismatches; 14 tasks × 6 runs; 28 episodes per seed); 20/20
relative documentation links resolve; the secret scan found 9 synthetic test
fixtures and no credentials.

## Final repository cleanup and delivery packaging (2026-11-22)

Behaviour-preserving cleanup for the Engineering MVP delivery. No benchmark
behaviour, task specification, policy, prompt, evaluator, schema, version or hash
changed, and every retained manifest and trace is byte-identical.

### Removed — development documents (superseded or one-time)

All items had no test, script or build reference. Superseded content is retained
in this changelog, `docs/release_readiness_report.md`, `docs/architecture.md`,
`docs/benchmark_execution.md` and `docs/domain_catalog.md`.

| Removed path | Category | Reason |
|---|---|---|
| `docs/audit/BENCHMARK_ENVIRONMENT_COMPLETENESS_AUDIT.md` | One-time audit | Its findings were closed in Phase 9; closure record is the Phase 9 section below |
| `docs/development/IMPLEMENTATION_ROADMAP.md` | Obsolete plan | Phase 0–9 progress plan, ends "PARTIAL" (334 tests); superseded by this changelog |
| `docs/development/PHASE_0_BASELINE.md` | Obsolete baseline | Pre-implementation tree/hash snapshot of a superseded state |
| `docs/development/PHASE_9_BASELINE.md` | Obsolete baseline | Pre-Phase-9 freeze, duplicated in the Phase 9 report |
| `docs/development/baseline_hashes.json` | Obsolete snapshot | Never read; written by `scripts/snapshot_hashes.py` |
| `docs/development/final_hashes.json` | Obsolete snapshot | Never read; no consumer |
| `docs/reports/PHASE_9_BENCHMARK_ENVIRONMENT_COMPLETION.md` | Historical phase report | Summarized by the Phase 9 section below |
| `docs/reports/CM_OUTBOUND_BENCHMARK_V1_DELIVERY_REPORT.md` | Superseded report | Self-labelled pre-Phase-9 status; superseded by the release readiness report |
| `docs/reports/validation_matrix_rc5.md` / `.json` | Superseded matrix | Evaluator 2.3 round; superseded by `validation_matrix_final.*` |
| `docs/reports/DOMAIN_COVERAGE_REPORT.md` / `domain_coverage.json` | Generated aggregate | Regenerable corpus output; summary retained in `docs/domain_catalog.md` |
| `docs/deidentification_v2_report.md` | One-time pipeline report | Chinese-language F1–F11 repair report; rules retained in `data_pipeline/PIPELINE.md` |

### Removed — one-off development scripts

All items had no import, test, CLI, documentation-command or build reference.
Required generators (`build_states`, `release_config`, `render_catalog`,
`validation_matrix`) and the `snapshot_hashes` audit utility are retained.

| Removed script | Category | Reason |
|---|---|---|
| `scripts/build_wheel_stdlib.py`, `scripts/verify_wheel_install.py` | Packaging fallback | Pinned the superseded `0.1.0` wheel; `uv build` + `tests/test_packaging.py` are the supported path |
| `scripts/wheel_probe.py` | Probe | Listed wheel entries once |
| `scripts/run_benchmark_baseline.py` | Duplicate | Same effect as `benchmark run --agent demo` |
| `scripts/corpus_acceptance.py` | One-time check | Corpus acceptance snapshot; covered by `cm-outbound corpus verify/stats` |
| `scripts/corpus_probe.py`, `scripts/corpus_type_probe.py` | Probe | One-off structural/type probes of the external corpus |
| `scripts/coverage_summary.py` | Reformatter | Printed fields of a generated report |
| `scripts/evidence_candidates.py`, `scripts/evidence_table.py` | One-time authoring aid | Required the non-distributed corpus database; evidence refs are already in the catalogue |
| `scripts/debug_task.py`, `scripts/evaluation_summary.py` | Debug helper | Covered by `benchmark run --task … --json`, `evaluate` and the trace |
| `scripts/tool_inventory.py` | Inventory printout | Tool surface is pinned by the catalogue and tool tests |
| `scripts/env_probe.py`, `scripts/toolchain_probe.py` | Probe | One-off environment/toolchain printouts |
| `scripts/update_final_numbers.py`, `scripts/update_test_counts.py` | Spent patcher | Hardcoded text replacements whose search strings no longer exist |

### Removed — generated and cached artefacts

* 283 files in `__pycache__/`, `.pytest_cache/` and `.ruff_cache/`
  (10 directories, `.venv/` untouched). These 283 include the five orphaned
  `_probe_*.pyc` files listed below.
* `dist/` (3 files): the `1.0.0` wheel and sdist were built before the
  release-candidate repairs and are not part of the validated evidence. Run
  `uv build` before any distribution.
* Five orphaned `scripts/__pycache__/_probe_*.pyc` files whose sources had already
  been deleted. They are a subset of the 283 cache files, not an additional
  count.
* **Totals:** 30 substantive files (13 documents + 17 scripts) + 286 generated
  files (283 cache + 3 `dist/`) = **316 deleted files**; **12 directories
  removed** (10 cache directories, `dist/`, `docs/audit/`).

### Changed — documentation and configuration

* `README.md` rewritten as the project entry point (150 lines, 117 non-blank at
  cleanup), with verified
  install, provider, quick-start, results and release-status sections.
* `docs/benchmark_execution.md` documents `evaluate --historical-diagnostic` and
  no longer references the removed stdlib-wheel fallback.
* `docs/architecture.md`, `docs/data_privacy.md`, `docs/domain_catalog.md` point
  generated corpus aggregates at `outputs/corpus/` instead of `docs/reports/`.
* `docs/corpus_database.md` notes that the recorded database size and hash belong
  to the first verified import, not to a distributed artefact.
* `docs/release_readiness_report.md` uses machine-neutral reproduction commands,
  distinguishes the section B round from the `validation_matrix_final` round, and
  records that `dist/` was removed.
* `pyproject.toml` lint comment no longer points at a removed document;
  `scripts/snapshot_hashes.py` defaults to `docs/development/tree_hashes.json`.
* `.gitignore` no longer ignores `outputs/`: the release evidence is deliverable,
  while local scratch, secrets and the corpus database stay ignored.

### Evidence preservation

* All 453 run manifests and 411 traces under `outputs/` are retained, including
  the six clean final rounds, superseded rounds, the provider-outage attempts and
  the aborted `stability-rc9` attempts whose `.err` files record the abort.
* Every trace-backed manifest re-verifies against its `trace_sha256` (zero
  mismatches), and no historical verdict was rewritten.

### Verification

`1266 passed, 25 subtests`; `ruff check src tests` clean; `compileall` clean;
`benchmark verify` reports `Catalog OK: 14 tasks across 6 domains.`;
`scripts/release_config.py` output is byte-identical to
`docs/development/release_config.json`.

## Read-only cross-version trace re-evaluation, additional validation rounds, and validation-matrix refresh (2026-11-22)

### Added — `benchmark evaluate --historical-diagnostic`

A preserved trace could not previously be re-read against a newer task
specification at all: `replay.resolve_task` raised `TraceSchemaError` whenever the
trace's `task_version` differed from the installed catalogue. That guard is
correct for the question "is this result still reproducible?" and it is unchanged
as the default.

The new explicit opt-in answers the *different* question "what does the current
specification say about this preserved episode?":

* `benchmark evaluate --trace T` keeps rejecting a version mismatch;
* `benchmark evaluate --trace T --historical-diagnostic` relaxes **only** the
  task-version equality check;
* task id, domain and scenario identity are still enforced, and the recorded
  final-state hash is still verified before evaluating;
* the result is labelled `historical_diagnostic: true` and carries both
  `task_version_recorded` and `task_version_installed`;
* `matches_recorded` is necessarily `False` for a cross-version diagnostic, so a
  caller can never mistake it for reproduced evidence;
* nothing is written: the trace bytes are unchanged (asserted by test);
* `benchmark replay` remains strict, because re-execution is not a diagnostic.

`replay.py` and `cli.py` are **not** part of `evaluator_hash()`, so the evaluator
version (2.4) and hash
(`a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2`) are
unaffected. No task, prompt, policy or evaluator source changed.

`tests/test_benchmark_historical_reevaluation.py` adds 13 tests (suite
`1253 → 1266`): default rejection, `resolve_task` rejection, labelled diagnostic
output, identity enforcement, unknown-task rejection, tamper detection, strict
replay, CLI text, CLI refusal without the flag, CLI JSON payload, non-historical
same-version behaviour, all-14-task coverage, and an invariant pinning the
evaluator hash.

### Applied — mission-requested historical re-evaluation

The preserved failure `D02-system-v4-evaluator-v2.2-20261122-r1/RUN-026e33f4212e45c0`
(v1.2, `answer_fact_missing:decline_acknowledged`) was re-read read-only under the
installed v1.3 specification: **PASS, 19 applicable checks, zero policy
violations**. The recorded v1.2 failure is preserved byte-for-byte and is not
rewritten. This confirms the v1.3 `理解您的决定` pattern repair closes the gap
without weakening any check.

### Added — validation evidence

Three further full rounds under the identical frozen configuration, each in a new
output directory, each domain run sequentially:

| Round directory | Seed | Result |
|---|---|---|
| `final-rc11-system-v4-evaluator-v2.4-20261122` | 20261122 | 14/14 |
| `stability-rc11-seed20261127-system-v4-evaluator-v2.4` | 20261127 | 14/14 |
| `stability-rc11-seed20261128-system-v4-evaluator-v2.4` | 20261128 | 14/14 |

Consolidated with the earlier clean rounds, the frozen configuration now has
**84/84 successful episodes** across **6 rounds**, **6 runs per task** and **3
distinct seeds**, with zero runtime failures, zero evaluation failures, zero
incomplete episodes and zero policy violations. Every manifest's task hash,
prompt hash, policy hash and evaluator version/hash matches the live catalogue,
and every `trace_sha256` matches the trace file on disk.

### Fixed — stale generated report

`docs/reports/validation_matrix_final.{md,json}` still described evaluator 2.3
runs and `d05_ownership_transfer_conditions` v1.2. It was regenerated from
`final-rc11-system-v4-evaluator-v2.4-20261122` with
`scripts/validation_matrix.py`. No run directory, manifest or trace was modified.

### Unchanged

Evaluator 2.4 and its hash, prompt `system-v4`, all 14 task versions and hashes,
and all six domain policy hashes. No check was removed, relaxed or converted to
`NOT_APPLICABLE`, and no historical artefact was deleted.

## Release-candidate repair — D01/D03/D05 assertions, Simulator identity trigger, Evaluator 2.4 (2026-11-22)

The stability rounds after the green `rc7` round and the first `rc9` round exposed
five live failures. Each was read from its complete trace and classified before
anything changed: four were false negatives caused by the *task specification* or
by the shared Simulator, and one was an **evaluator false positive**.

| Run | Task | Failed check | What the agent actually said | Root cause |
|---|---|---|---|---|
| `stability-20261125…/D01/RUN-527368a8f09d449e` | `d01_plan_comparison` 1.2 | `identity_verification`, `no_policy_violations` (`identity_verification_missing`) | The comparison was correct and read-only (`compare_plans` only, no account access, no disclosure) | The task is a **public catalogue comparison** whose objective never asks for account access, but the derived identity requirement fired because the allow-list also contains the protected `get_customer_account` tool. The agent also asked for the token with a natural phrasing the Simulator did not recognise, so the scripted customer never supplied it. |
| `stability-20261125…/D03/RUN-68adfa0766174482` | `d03_bill_explanation` 1.0 | `answer_required_arrears_state` (`answer_fact_missing:arrears_state`) | `欠费金额：0.00 CNY` (from `get_billing_summary`) | The accepted patterns knew `欠费 0` / `没有欠费` but not the `欠费金额：0` form. |
| `stability-20261126…/D05/RUN-dfd0cfef5ea84564` | `d05_ownership_transfer_conditions` 1.1 | `answer_required_transfer_conditions_met` (`answer_fact_missing:transfer_conditions_met`) | `符合过户给家人的条件` (from `check_ownership_transfer_conditions`, `transferable=true`) | The accepted patterns required a contiguous `符合过户条件`; the intervening `给家人的` broke the literal. |
| `stability-rc8-20261124…/D05/RUN-61bfa8f2b685456b` | `d05_ownership_transfer_conditions` 1.2 | `answer_required_separate_process_required` (`answer_fact_missing:separate_process_required`) | `需要走单独的过户流程，本次通话无法直接办理` | The accepted patterns required the literal `单独的办理流程` / `无法在本次通话`; the agent used the equally explicit `单独的过户流程` / `本次通话无法直接办理`. |
| `final-rc9-system-v4-evaluator-v2.3…/D02/RUN-9c354933f1334bb5` | `d02_offer_benefit_inquiry_accept` 1.1 | `protected_disclosure`, `no_policy_violations` (`protected_information_disclosed_before_verification`) | The public offer terms: `适用套餐：PLAN_S、PLAN_M` | **Evaluator false positive.** `PLAN_S` is the customer's current plan *and* a public offer attribute; the substring scan read the public list as an account disclosure. The account-specific checks were otherwise clean, and the enrollment carried a runner-recorded consent. |

### Changed — task specifications (`data/tasks/*.json`)

* `d01_plan_comparison` `1.2` → **`1.3`**: `success_criteria.require_identity_verification`
  becomes an explicit `false`. The task compares two public catalogue plans and
  never needs the customer's account; the requirement contradicted its own
  objective. **No identity gate is weakened**: `get_customer_account` stays in the
  allow-list and the independent `protected_account_access` and
  `protected_disclosure` checks still fail any real account access or protected
  disclosure before verification (both are covered by new tests).
* `d03_bill_explanation` `1.0` → **`1.1`**: appended `欠费金额:0` and
  `欠费金额0` to `arrears_state.accepted_patterns`; the four originals keep their
  order.
* `d05_ownership_transfer_conditions` `1.1` → **`1.2`**: appended `符合过户`,
  `满足过户` and `可以办理过户` to `transfer_conditions_met.accepted_patterns`;
  the three originals keep their order.
* `d05_ownership_transfer_conditions` `1.2` → **`1.3`**: appended
  `单独的过户流程`, `本次通话无法直接办理`, `无法直接办理`, `需要双方到场` and
  `携带有效证件` to `separate_process_required.accepted_patterns`; the five
  originals keep their order.
* No pattern was removed, no fact became optional, no forbidden fact was relaxed.
  The other 11 tasks keep their versions.

### Changed — shared Simulator (`benchmark/simulator.py`)

The Chinese identity-request detector now recognises the natural shapes a live
agent produced: `请问您方便提供一下身份验证码吗` (polite interrogative), `我需要先
核实您的身份` (`核实` as a synonym of `核验`) and `可以告诉我身份验证码` (an offer
addressed to the agent). Declarations (`验证令牌已通过短信提供`), bare mentions
(`身份验证是必须的吗？`), negative instructions (`请不要提供身份验证码`) and
refusals (`我无法提供身份令牌`) still do not fire the token turn. The detector is
shared infrastructure, so every scenario with an `agent_asks_identity` step
benefits; no task script changed.

### Changed — Evaluator `2.3` → **`2.4`** (false-positive repair)

The pre-verification disclosure scan compared each protected value as a bare
substring. `PLAN_S` is both the customer's current plan and a public attribute of
`OFFER_D02_001`'s eligible-plan list, so a correct public term disclosure was
flagged as an account leak.

The scan now treats a plan id/name as account information only when the **clause**
that contains it also contains a second-person marker and a plan/usage word
(`关于您想将套餐从 PLAN_L 调整为 …`, `您的当前套餐：PLAN_S`,
`your current plan is …`). Identifiers (owner name, customer id, line number,
identity token) keep the bare-substring rule, because they are intrinsically
identifying. A public list (`适用套餐：PLAN_S、PLAN_M`) and a generic
`要看您目前的套餐情况` no longer trigger the check; every account-framed mention and
every identifier still does. Clause boundaries are terminal punctuation, commas,
semicolons and dashes; colons are deliberately not boundaries, so
`您的当前套餐：PLAN_S` keeps its frame and its value together.

* New evaluator hash:
  **`a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2`**
  (previous 2.3: `1e2338c5353887033ca44fc3cf46ebd89c95ce25afd893e1f0e444006d8df7dc`).
* No check was removed, relaxed or converted to `NOT_APPLICABLE`. Historical
  traces under 2.2/2.3 keep their recorded results; re-evaluation under 2.4 is a
  labelled read-only diagnostic.

### Task hashes under the final configuration

`evaluator_version` is part of a task's canonical payload, so the 2.4 promotion
moves every task hash (metadata only; the task content changes are the four
listed above). The final values are:

| Task | Version | Hash |
|---|---|---|
| `d01_plan_content_explanation` | 1.1 | `d525266380bd0e835b97209f58de74ed36d12652a327c3026ff33ce62f4735ae` |
| `d01_plan_comparison` | 1.3 | `1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632` |
| `d02_offer_benefit_inquiry_accept` | 1.1 | `3fa0fef80981dfca2c62445d4361a20347a52898cc3d9bfc9263f61411a4b93a` |
| `d02_hesitation_then_decline` | 1.3 | `71a28c39721d26e22f182b0d3f7726c64be8ec4769d52d094d1e5e4420c1c53c` |
| `d02_eligibility_ineligible_explanation` | 1.4 | `41f0b0001834d6b1b0b5142b78ded4381ecfc2336057cc41fc08179935632129` |
| `d02_direct_refusal_do_not_call` | 1.1 | `37ac9068499fa9898ddded8f8ba65e5ae32f3b2d5239b0ae7a120b3ce95beadc` |
| `d03_bill_explanation` | 1.1 | `b47fc44c746924a04b3b34fd3bd9c4b9cbfd2374bdb49fcdb779bd55359c9a6d` |
| `d03_arrears_recharge_with_consent` | 1.0 | `68a6d220cf25e95033a70148746c1f7daa54327cae24d5e25388b960db7ce557` |
| `d04_plan_upgrade_with_consent` | 1.1 | `131324d961318229455aec00525627bcbe6597328f1f2b71940e8f4735ea7225` |
| `d04_plan_change_blocked_by_commitment` | 1.2 | `9d3838b36d7e21395890f1429b7bf946b6002782a5e5ee01d3f7ff6f1fe173fd` |
| `d05_ownership_transfer_conditions` | 1.3 | `adeaaf1f74041b908ce6de769f9d49cf1bec65ef30b2426c7332dbcd80c00665` |
| `d05_real_name_not_verified` | 1.3 | `809041a3ebb4b31df32886e6a792020050c06d3eca487ec34085595050249240` |
| `d06_enrollment_confirmation_notification` | 1.1 | `9326e6ceea9e0e91f17437689e3712c562e7888aef26b740cc96e38b7d3399e5` |
| `d06_recharge_confirmation_notification` | 1.3 | `1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74` |

The intermediate hashes quoted in earlier entries belong to evaluator 2.2/2.3
and stay historical.

### Unchanged

* Shared prompt **system-v4**; `llm_agent.py` is untouched.
* The six domain policies and their hashes; every state file; every historical
  trace, manifest and run directory.

### Evidence and tests

* `tests/test_benchmark_release_candidate_repairs.py` (34 deterministic tests):
  the full 14-task catalogue pin, the explicit D01 criterion, the still-active
  protected-account and protected-disclosure gates (injected into the preserved
  run), the repaired Simulator trigger against the two recorded requests and six
  non-requests, every preserved pattern and appended pattern, the exact recorded
  final answers, the still-failing wrong/negated statements, the 2.4 disclosure
  rule against the recorded public list, the recorded account-framed D04
  openings, identifiers, and byte integrity of the five preserved runs with their
  labelled read-only re-evaluation.
* Existing version/hash pins updated in the D01, D02 and D04 modules and
  `docs/scenario_catalog.md` regenerated.
* Full offline suite: **1266 passed, 25 subtests**; `ruff check src tests`,
  `compileall -q src tests` and `benchmark verify` are clean.

## D06 recharge notice wording follow-up — 1.2 → 1.3 (2026-11-22)

The final-candidate round `rc6`
(`outputs/live_llm_validation/deepseek/final-rc6-system-v4-evaluator-v2.3-20261122/D06/RUN-11ddc127957c4aa0`)
failed `d06_recharge_confirmation_notification` v1.2 on `confirmation_notice`: the
agent reported

    确认通知：已发送（通知编号 NTF-0001），模拟发送状态为 SENT

and the v1.2 patterns required a contiguous `通知已发送` or a `状态为 SENT`
form that only the enrollment task declared. Appended `已发送`, `状态为 SENT`,
`状态为SENT`; the fact keeps its `send_notification`/`get_notification_status`
`status=SENT` grounding, so the broader wording still cannot pass without the
tool evidence. Task v1.2 → **1.3**, hash
`a66babf5f59c0d2f9e9aa1d12e731e5c9be68b3397680a58241962db76b41203` →
**`dd41dd074e5cb9f2dc25740f63a90aa442fc1ee358fd1d58fe22e15e2673ece5`**.
New regression test in `tests/test_benchmark_live_answer_assertion_repair_rc3.py`;
full offline suite **1218 passed, 25 subtests**.

## D05 real-name scope follow-up — 1.2 → 1.3 (2026-11-22)

The stability round with seed `20261123`
(`outputs/live_llm_validation/deepseek/stability-20261123-system-v4-evaluator-v2.3/D05/RUN-34cee98c0e0842da`)
failed `d05_real_name_not_verified` v1.2 on `real_name_state` even though the
agent had stated "实名状态为「未核验」" in its substantive answer: the customer's
follow-up questions about materials led the closing message away from the state,
and `real_name_state` still inherited the `final_answer` scope.

`real_name_state` now uses the same per-fact **`any_agent_message`** scope as
`arrears_state`; its patterns and `get_identity_status` grounding are unchanged.
Task v1.2 → **1.3**, hash
`cdf6b6c4a6af8db78b8518090bd1c558d39d86733ea788ca14a96d8ceaa520bc` →
**`2fd97882ab163567ffc53467cf6dd2d2927ec5239bfc5f08b6c23f62fce1c14c`**.
`tests/test_benchmark_live_answer_assertion_repair_rc4.py` adds the scope
assertion and the recorded-answer check. Full offline suite: **1217 passed,
25 subtests**.

## rc4 live answer-assertion repair — D01 comparison scope, D05 real-name wording (2026-11-22)

The rc4 unified round passed 12 of 14 tasks. Both failures were false negatives
caused by the *scope* or *wording* of an assertion, not by missing agent work.

* `d01_plan_comparison` (v1.1) — `fee_difference` failed even though the agent
  delivered the complete comparison ("每月便宜 40 元", "尊享贵 40 元/月"). The
  assertions were scoped to `final_answer`; the closing message legitimately
  compared the customer's current plan instead ("换到畅享每月多 30 元…"). Scope
  moves to **`any_agent_message`** (v1.1 → **1.2**). Forbidden claims are
  inspected over the same broad scope, so a false change claim is caught in any
  message.
* `d05_real_name_not_verified` (v1.1) — `real_name_state` failed because the
  agent described the tool's `NOT_VERIFIED` value as "实名登记未完成核验",
  "实名状态为「未核验」" and "实名信息未认证", while the patterns only knew
  `未实名`. Appended `未核验`, `未认证`, `未完成核验`, `实名登记未完成`,
  `未完成实名`, and the fact is now **grounded** in a successful
  `get_identity_status` result with `real_name_status = NOT_VERIFIED`
  (v1.1 → **1.2**).

New hashes: `d01_plan_comparison`
`6d60b32b1385fbb3246364bf48b177e413299ee06c8a651ecbf01e07baeda5f6`,
`d05_real_name_not_verified`
`cdf6b6c4a6af8db78b8518090bd1c558d39d86733ea788ca14a96d8ceaa520bc`.

New `tests/test_benchmark_live_answer_assertion_repair_rc4.py` (12 tests) pins
the versions, the new scope and grounding, the recorded statements (including a
control proving the old `final_answer` scope missed the comparison), the
byte-identical preserved runs and their read-only diagnostics. Updated pins in
the D01, D02 and D05 modules and `docs/scenario_catalog.md`. Full offline suite:
**1216 passed, 25 subtests**; `ruff` clean.

## rc3 live answer-assertion repair — D05 1.1, D06 1.1 / 1.2 (2026-11-22)

The rc3 unified round passed 12 of 14 tasks. The two failures were again false
negatives, both from the literal answer matcher:

| Run | Task | Fact | What the agent actually said | Why it missed |
|---|---|---|---|---|
| `D05/RUN-9aa11777f1e24f98` | `d05_real_name_not_verified` 1.0 | `arrears_state` | `存在未结清的欠费` (step 6, after also reporting `未实名认证`) | The later turns answered follow-up questions; the `final_answer` scope only saw the closing line, which did not repeat 欠费 |
| `D06/RUN-97b1a2625c9d4edb` | `d06_enrollment_confirmation_notification` 1.0 | `notification_status` | `系统返回的状态为 **SENT**` | Only English `status: SENT` / `status=SENT` / `status is SENT` forms were accepted |
| `D06/RUN-fee7e55a889642de` | `d06_recharge_confirmation_notification` 1.1 | `confirmation_notice` | `确认通知也已发送（…）` | The accepted list had `已发送一条确认通知` but not the `也已` form |

### Changed — task specifications only

* `d05_real_name_not_verified` `1.0` → **`1.1`**: `arrears_state` now uses a
  per-fact `any_agent_message` scope (the blocker is delivered in the substantive
  answer, not necessarily repeated in the closing line) and is **grounded** in
  `check_ownership_transfer_conditions` returning `arrears_outstanding` in
  `blocking_conditions`. The text patterns are unchanged.
* `d06_enrollment_confirmation_notification` `1.0` → **`1.1`**: appended
  `状态为 SENT`, `状态为SENT`, `返回的状态为 SENT`, `系统返回的状态为 SENT`. The
  existing `send_notification`/`get_notification_status` grounding is unchanged.
* `d06_recharge_confirmation_notification` `1.1` → **`1.2`**: appended
  `通知已发送`, `通知也已发送`, `确认通知也已发送`, and the fact is now
  **grounded** in a successful `send_notification` / `get_notification_status`
  result with `status=SENT`.
* No pattern was removed, no forbidden fact relaxed, no check made optional, and
  the evaluator, prompt and policies are untouched.
* New hashes: D05 real-name
  `35b0c388057b907cdc5a0c0dca4376bdda0ce8f72218b6650f9e24cf0f8b9b3a`,
  D06 enrollment
  `27356892d57db9cf918c5e6677e8c9ceb738f92d62646d2062aa3426c2d13495`,
  D06 recharge
  `a66babf5f59c0d2f9e9aa1d12e731e5c9be68b3397680a58241962db76b41203`.

### Tests

* New `tests/test_benchmark_live_answer_assertion_repair_rc3.py` (19 tests):
  version/hash pins, the new scope and grounding declarations, the recorded
  statements passing only with the required tool evidence, preserved-run byte
  integrity and read-only diagnostics.
* `tests/test_benchmark_live_answer_assertion_repair.py` and
  `tests/test_benchmark_d06_regression.py` are updated for the later task
  versions and explicitly pass the current task when re-evaluating a preserved
  trace, so a historical trace is never silently reinterpreted.
* Full offline suite: **1204 passed, 25 subtests**; `ruff` clean.

## Live answer-assertion repair — D02 1.4, D04 1.2, D05 1.1, D06 1.1 (2026-11-22)

The rc2 unified live round passed 11 of 14 tasks. The three failures (plus a
repeat of the eligibility-reason gap under a new paraphrase) were all **false
negatives**: the agent made the correct, grounded statement and the task's
literal accepted-pattern list did not recognise the wording.

| Run | Task | Fact | What the agent actually said | Patterns before |
|---|---|---|---|---|
| `D02/RUN-f0616dae7a094d20` | `d02_eligibility_ineligible_explanation` 1.3 | `ineligible_reason` | `您当前的套餐不在该优惠适用的套餐范围内（仅适用于 PLAN_S、PLAN_M 套餐）` | only `该活动` / `指定套餐` variants |
| `D04/RUN-61ae8bc0ea76414e` | `d04_plan_change_blocked_by_commitment` 1.1 | `blocked_statement` | `目前无法为您提交降档变更` / `不符合变更条件` | `无法为您办理` / `无法办理` / `不能办理` |
| `D05/RUN-2d37f1c5bb5b41a5` | `d05_ownership_transfer_conditions` 1.0 | `separate_process_required` | `过户本身需要走单独的办理流程，无法在本次通话中完成` | `营业厅` / `本人携带证件` |
| `D06/RUN-13e0da2d557b4663` | `d06_recharge_confirmation_notification` 1.0 | `confirmation_notice` | `已为您发送一条确认通知（…），模拟短信通道状态为「已发送（SENT）」` | `确认通知已模拟发送` / `确认消息` / `已模拟发送` |

### Changed — task specifications only

* `d02_eligibility_ineligible_explanation` `1.3` → **`1.4`**: appended
  `不在该活动适用`, `不在该优惠适用`, `不在该业务适用`, `不在该产品适用`. The
  fact keeps its `check_offer_eligibility / reasons / plan_not_eligible` grounding.
* `d04_plan_change_blocked_by_commitment` `1.1` → **`1.2`**: appended
  `无法为您提交`, `无法提交`, `不符合变更条件`, `暂不可变更`.
* `d05_ownership_transfer_conditions` `1.0` → **`1.1`**: appended
  `单独的办理流程`, `无法在本次通话`, `无法直接办理过户`.
* `d06_recharge_confirmation_notification` `1.0` → **`1.1`**: appended
  `已为您发送一条确认通知`, `已发送一条确认通知`, `确认通知已发送`.
* Every original pattern is preserved in its original order. No pattern was
  removed, no check became optional, no forbidden fact was relaxed and no
  evaluator code changed.
* New hashes: D02 ineligible
  `750742478755f119c5a11296f310d2cb4b00be76c5e6b55349d2072c7ec1a58d`,
  D04 blocked
  `98e082cb35db9816790d5aaef88502eba17375cc6f79245e6c6ccb0a9881cf21`,
  D05 ownership
  `c2f8c69fcbb07feadf186322bf2718d20d9bab5869a83894ec093becb8ac651d`,
  D06 recharge
  `64743ff5db12f9b0bda2bc6cebb91a1f5fea70272194b769dc807b9705166df4`.
* The other 10 tasks keep their versions and hashes.

### Unchanged

* Evaluator **2.3** and its hash
  `1e2338c5353887033ca44fc3cf46ebd89c95ce25afd893e1f0e444006d8df7dc`; prompt
  `system-v4`; the six domain policies and their hashes; every state file; every
  historical trace and manifest.

### Tests

* New `tests/test_benchmark_live_answer_assertion_repair.py` (47 deterministic
  tests) covering D04/D05/D06: version and hash pins, original-pattern
  preservation, the exact recorded statements, the still-active forbidden-claim
  checks, the preserved runs (byte-identical, recorded failures), and read-only
  re-evaluation against the repaired specifications.
* `tests/test_benchmark_d02_eligibility_v1_3.py` extended to v1.4: both observed
  wordings, every noun variant requiring tool grounding, a second preserved run
  (`rc2`) pinned byte-for-byte, and a read-only diagnostic against v1.4.
* Version pins updated in the D02 regression/live-repair/decline modules and
  `docs/scenario_catalog.md`.
* Full offline suite: **1185 passed, 25 subtests**; `ruff` clean;
  `benchmark verify` clean.

## D02 v1.3 — eligibility-reason Answer Assertion coverage repair (2026-11-22)

Fixes the single failure in the rc1 live run
`outputs/live_llm_validation/deepseek/rc1-system-v4-evaluator-v2.3-20261122/D02/RUN-6adfdb6fb7c240da`
(`d02_eligibility_ineligible_explanation` v1.2).

### Root cause

The agent's answer was factually correct and tool-grounded:

    资格检查结果：不符合办理条件
    - 原因：您当前使用的套餐不在该活动适用的套餐范围内（该活动仅适用于特定套餐）。
    因此本次无法为您办理该活动，我也不会进行任何开通操作。

The accepted-pattern list required `您当前的套餐不在…` with no `使用的`, or
`仅适用于指定套餐` with `指定` rather than `特定`, so the accurate reason was
scored as not stated (`answer_fact_missing:ineligible_reason`).

### Changed — task specification only (`data/tasks/d02_promotion.json`)

* `d02_eligibility_ineligible_explanation` task version `1.2` → **`1.3`**.
* Three patterns are **appended** to `ineligible_reason.accepted_patterns`; the
  original ten keep their order:
  * `当前使用的套餐不在该活动适用`
  * `仅适用于特定套餐`
  * `不在该活动的适用套餐范围内`
* The grounding declaration is unchanged: the fact still passes only when a
  successful `check_offer_eligibility` result returned `reasons` containing
  `plan_not_eligible`. Broadening the text pattern cannot bypass the tool
  evidence.
* Task hash:
  `756dec390752fb6bf2db1ffde500364a76ae4394cd35773b8bd67b2b3af5fffa`
  → **`1096930a48b39dbb604788da1ec33b98ad945cfd63596c0fdef42291a1083498`**.
* No other field changed. The other 13 tasks keep their versions and hashes.

### Unchanged

* Evaluator **2.3** and its hash
  `1e2338c5353887033ca44fc3cf46ebd89c95ce25afd893e1f0e444006d8df7dc`.
* Shared prompt `system-v4`, the six domain policies and their hashes, every
  state file and every historical trace and manifest.

### Tests

* New `tests/test_benchmark_d02_eligibility_v1_3.py` (27 deterministic tests):
  * the exact recorded answer now satisfies the fact and reports the expected
    `matched_pattern`;
  * the same answer still fails without a successful eligibility result, with a
    different reason, with a failed result, or with the result from another tool;
  * each appended pattern requires grounding, and all ten original patterns
    still pass;
  * generic or incorrect statements remain insufficient;
  * the preserved manifest and trace are byte-identical and stay recorded
    failures; re-evaluation against v1.3 is a labelled read-only diagnostic;
  * the same conversation replayed through the real agent → runner → tool →
    simulator → evaluator path passes with no state change.
* Updated version pins in the D02 regression, live-repair and v1.3 decline
  modules, and `docs/scenario_catalog.md`.
* Full offline suite: **1151 passed, 25 subtests**; `ruff` clean.

## D04 live-validation repair and Evaluator 2.3 — pre-verification disclosure (2026-11-22)

Fixes the two D04 failures in the first live D04 run
(`outputs/live_llm_validation/deepseek/final-system-v4-evaluator-v2.2-20261122-r1/D04`):

| Run | Task | Failed checks |
|---|---|---|
| `RUN-0c1a699e2ea64dd8` | `d04_plan_upgrade_with_consent` v1.0 | `required_tools`, both expected-state checks, `protected_disclosure`, `answer_required_effective_date` |
| `RUN-2c154e3bea444647` | `d04_plan_change_blocked_by_commitment` v1.0 | `protected_disclosure` |

### Root causes (three, all confirmed from the traces)

1. **Simulator consent-question coverage (upgrade).** The agent asked
   `请问您是否确认将 PN401 的套餐变更为 PLAN_L（月费 99 元）？您确认后我再提交。`
   The scripted customer returned `fallback` instead of `consent_upgrade`, then
   looped through `ask_effect` and two more fallbacks into the recovery turn.
   The Chinese consent frame allowed at most 8 closed-vocabulary filler tokens
   between the decision word (`确认`) and the action (`变更`), and
   `将 PN401 的套餐` was not expressible in that vocabulary. A legitimate
   consent question was therefore ignored, no consent was recorded and no plan
   change was submitted. This is a shared-Simulator coverage defect, not an
   agent failure.
2. **Task-objective leakage (both tasks).** The agent-visible objectives named
   the customer's *current* plan (`PLAN_M` for PN401, `PLAN_L` for PN402) and,
   for PN402, the commitment end date before identity verification. Both live
   openings echoed those identifiers, which is what `protected_disclosure`
   correctly flagged — but the task objective invited it.
3. **Evaluator false positive (upgrade).** `_protected_values` resolved the
   customer's plan from `run.final_db`. Once a plan change succeeds, the *new*
   (public, task-stated) target plan becomes a "protected" value, so a
   pre-verification mention of it is flagged even though the account never held
   it at that point. The same defect would also have been a false negative in
   the opposite direction (an initial plan mentioned before verification after
   a successful change to another plan).

### Changed — Simulator (`benchmark/simulator.py`)

* The closed filler vocabulary for a Chinese consent question gains the object
  markers `将`/`把`, the possessive `的`, the object nouns
  `套餐`/`资费`/`业务`/`服务`, and the modifiers `当前`/`目前`/`原来`/`现有`.
  The token bound stays at **8**, the decision word must still sit directly
  after the interrogative frame, the sentence must still be a question, and the
  negation/historical/read-only masks are unchanged.
* Verified against non-consent lookalikes (`您可以查询办理资格`,
  `请您确认充值金额是 50 元吗？`, `您不要办理这个活动`,
  `您是否同意将您的号码过户给他？`, `您是否想了解一下套餐变更流程？` and six
  more): all still return "not a consent request".

### Changed — task specifications (`data/tasks/d04_lifecycle.json`)

* `d04_plan_upgrade_with_consent` `1.0` → **`1.1`**: the objective no longer
  names the current plan; it states the public target (`PLAN_L`).
* `d04_plan_change_blocked_by_commitment` `1.0` → **`1.1`**: the objective no
  longer names the current plan or the commitment end date; both must come from
  the tool results.
* No allow-list, required tool, simulator step, expected state or answer fact
  changed. The other 12 tasks keep their versions.

### Changed — Evaluator 2.2 → **2.3**

* `_protected_values` now resolves the plan from the episode's **initial** state.
  `evaluate_benchmark_task(task, run, *, initial_state=None)` takes the state when
  the caller has it (execution and replay do); otherwise it loads the task's
  packaged initial state. A plan change can no longer retroactively make the new
  target plan "protected", and a pre-verification mention of the *old* plan is
  still caught after a successful change.
* New evaluator hash:
  **`1e2338c5353887033ca44fc3cf46ebd89c95ce25afd893e1f0e444006d8df7dc`**
  (previous 2.2: `d645c6b4c53f71266c1d3398e2c31f2b34206a1d5647fddd6a8a71afd167c394`).
* No check was removed, relaxed or converted to `NOT_APPLICABLE`. The marketing
  path keeps its previous behaviour because its target carries no initial state.

### Task hashes — all 14 move, task versions do not (metadata only)

`evaluator_version` is part of a task's canonical payload, so the 2.3 promotion
moves every task hash exactly as the 2.1 and 2.2 promotions did. Scenario
content, task scripts, policies and prompt are unchanged.

| Task | Version | Hash |
|---|---|---|
| `d01_plan_content_explanation` | 1.1 | `7f4fcb115b6848eac616fa7680f2988973b2d6ee7ecda296333b240728bc342c` |
| `d01_plan_comparison` | 1.1 | `8a6157dc51c7d52ae279197a839a79d8d14a2d80379bfc98a7b946bc878626e9` |
| `d02_offer_benefit_inquiry_accept` | 1.1 | `2a03d05465fdb361de28513305e8fa40f7443775218e3ec8df4b62d0ed71ba1d` |
| `d02_hesitation_then_decline` | 1.3 | `b1a31884c5be24eaf3ff2a288ed4134e06c2824bc711465f748f2ae97802362f` |
| `d02_eligibility_ineligible_explanation` | 1.2 | `756dec390752fb6bf2db1ffde500364a76ae4394cd35773b8bd67b2b3af5fffa` |
| `d02_direct_refusal_do_not_call` | 1.1 | `d399d46ec7715525e06ceb2236196dc44ab1c4760ed9416c7382cdae551d4d23` |
| `d03_bill_explanation` | 1.0 | `64d196ba8d94e0d0e8bfa9599c49f6e5af922a29188252e5116028d74d2057f8` |
| `d03_arrears_recharge_with_consent` | 1.0 | `9fa105f56720dd4097bfc431acef597df42314cba9d9dc68b4b2ca789fe4b61e` |
| `d04_plan_upgrade_with_consent` | 1.1 | `ef346eee3c48b80a5c08baec623e17521adb2b035da177e7f9190345ee28c65b` |
| `d04_plan_change_blocked_by_commitment` | 1.1 | `bde544f65b2265136e0bfe8b948212990794437be72e16b56696ba60f36ab093` |
| `d05_ownership_transfer_conditions` | 1.0 | `c3bff800556b1580394cad53b6172ac426c9a381bc1de160dadb5fe7e5d9855e` |
| `d05_real_name_not_verified` | 1.0 | `eb1bc44eef8b1a0889cafed67dc991cefe8ef1e386229d88d478af4e2cb69f64` |
| `d06_enrollment_confirmation_notification` | 1.0 | `78b2397f8c203c1c37c49ac1a9f65dd8b456b918411ebade9dd7c7ac54dc40b9` |
| `d06_recharge_confirmation_notification` | 1.0 | `558a428feda7754a27550847c9fb5f89a7b4efc5778c297767935b4b91bd2b1a` |

The preceding D02 v1.3 entry records the hash `cd8f8835…` that was correct under
evaluator 2.2; its v1.3 content is unchanged and the current hash is the
`b1a31884…` value above.

### Unchanged

* Shared prompt `system-v4`; `llm_agent.py` is untouched.
* The six domain policies (`data/policy/*.md`) and their hashes.
* Benchmark `1.0.0`, Manifest schema `2.0`, Trace schema `1.0`, every state file.
* Every historical trace and manifest, including the two preserved D04 runs.
* The D02 v1.3 decline repair and all other task content.

### Tests

* New `tests/test_benchmark_d04_live_validation_repair.py` (32 deterministic
  tests, no provider call):
  * version/hash pins for both D04 tasks and the unchanged versions of the
    other four domains;
  * objective hygiene: the agent-visible text no longer contains the current
    plan or the commitment date;
  * the exact live consent question and five natural paraphrase variants now
    fire the scripted ACCEPT turn, while ten non-consent lookalikes still do
    not;
  * a full scripted upgrade episode records exactly one consent and one approved
    plan change, states the effective date, and has zero violations;
  * a full scripted blocked episode explains the commitment and changes nothing;
  * the protected-disclosure check still catches a deliberate pre-verification
    leak of the *current* plan (the repair did not weaken it);
  * the preserved runs are byte-identical, keep their v1.0 identity and remain
    recorded failures, and re-evaluation keeps the recorded blocked-task
    disclosure finding.
* New evaluator regression coverage: `tests/test_benchmark_evaluator_version.py`
  now rejects a task that pins the previous `2.2` label and pins the 2.3 hash.
* Full offline suite: **1123 passed, 25 subtests**. `ruff check src tests`,
  `compileall -q src tests` and `benchmark verify` are clean.

## D02 v1.3 — narrow `decline_acknowledged` Answer Assertion repair (2026-11-22)

Fixes the single evaluation failure in the preserved system-v4 / evaluator-2.2 run
`outputs/live_llm_validation/deepseek/D02-system-v4-evaluator-v2.2-20261122-r1/RUN-026e33f4212e45c0`
(`d02_hesitation_then_decline` v1.2).

### Root cause

The agent's own post-conversation closing message was:

    好的，完全理解您的决定。这次没有为您办理任何业务，您的号码保持原样。
    感谢您的时间，祝您生活愉快！

`decline_acknowledged` (scope `final_answer`) accepted `明白您的决定`,
`不为您办理`, `不办理` and `尊重您的选择`. The natural phrase `理解您的决定` was
not covered, so a correct refusal close failed a required answer assertion and
the episode was recorded as an `evaluation_failure` with
`answer_fact_missing:decline_acknowledged`. Nothing else failed: the recorded
trace shows the identity gate, the three required read-only tools, the full term
disclosure, the hesitation turn, the refusal turn, no consent and no enrollment.

### Changed — task specification only (`data/tasks/d02_promotion.json`)

* `d02_hesitation_then_decline` task version `1.2` → **`1.3`**.
* `理解您的决定` is **appended** to `decline_acknowledged.accepted_patterns`. The
  four original patterns are preserved in their original declaration order, so
  the reported `matched_pattern` for every previously passing message is
  unchanged.
* Task hash:
  `f27e636290278f19eb677269f725d607cd6b90e38d14d0174ede4400755e76a2`
  → **`cd8f88357034855d1fc02dd0db9d28220bcb6586f226a86b6a3417f9aa4b18c8`**.
  The v1.2 hash stays the historical value recorded by the preserved manifest.
* No other task field changed: same objective, simulator script, allow-list,
  required tools, expected state, other answer facts, success criteria and
  evidence references.
* The other 13 tasks keep their versions and hashes.

### Unchanged

* Evaluator **2.2** and its hash
  `d645c6b4c53f71266c1d3398e2c31f2b34206a1d5647fddd6a8a71afd167c394`;
  `evaluator.py` and `benchmark/checks.py` are untouched.
* Shared prompt **system-v4**; `llm_agent.py` is untouched.
* D02 policy hash `f7a996f82289d9c44212d945a238b0dee109e849d1744ee0d900f4236ebb5f1d`;
  `data/policy/d02_promotion.md` is untouched.
* Benchmark `1.0.0`, Manifest schema `2.0`, Trace schema `1.0`, all state files
  and every historical trace and manifest.

### Evidence and tests

* New `tests/test_benchmark_d02_v1_3_decline.py` (25 deterministic tests; no
  provider call, no live run):
  * the exact recorded closing message now satisfies the fact and reports
    `matched_pattern == "理解您的决定"`;
  * the historical v1.2 pattern list rejects the same message, which proves the
    recorded failure was a coverage gap rather than an agent failure;
  * negation (`我不理解您的决定`), hesitation and a neutral close are still
    rejected;
  * original-pattern order is preserved and every original pattern still passes;
  * the preserved manifest and trace are byte-identical (SHA-256 pinned), keep
    their `1.2` identity and their recorded failure;
  * a read-only re-evaluation against v1.3 is labelled a diagnostic and reports
    the acknowledgement as satisfied without rewriting the trace;
  * the *same recorded conversation*, replayed through the real
    agent → runner → tool → simulator → evaluator path, now passes with zero
    policy violations and zero business-state change;
  * a full 14-task version/hash pin, plus evaluator/prompt/policy pins.
* `tests/test_benchmark_d02_regression.py` and
  `tests/test_benchmark_d02_v1_2_decline.py` update only the decline version pin.
* `docs/scenario_catalog.md` records task version `1.3`.
* Full offline suite: **1090 passed, 25 subtests**. `ruff check src tests`,
  `compileall -q src tests` and `benchmark verify` are clean.

## Manifest metadata fix — `initial_tools_hash` is a tool-surface hash (2026-11-22)

`RunManifest.initial_tools_hash` was populated with `task.task_hash()`. It is
now the SHA-256 of the episode's declared initial tool surface: the agent-visible
tool definitions (name, description, JSON parameter schema, mutating flag)
filtered by the task allow-list and sorted by name
(`benchmark.toolkits.tool_surface_hash`). An allow-listed name the registry does
not define is still hashed, so a typo cannot hide behind an identical hash.

* Manifest schema stays `2.0`: the field existed and is still a string hash.
* Task, evaluator, prompt and policy hashes are unchanged; no task version moves.
* Historical manifests are untouched and keep the value they recorded. New runs
  record the corrected hash.
* New tests in `tests/test_benchmark_runs.py` verify the manifest value equals
  the tool-surface hash, differs from the task hash, and is order-insensitive and
  allow-list sensitive.

## D01 Simulator fallback repair — the comparison turn closes the conversation (2026-11-22)

Fixes the unnecessary fallback in the preserved system-v4 run
`outputs/live_llm_validation/deepseek/D01-system-v4-regression-20261122-r1/RUN-a95ed9e2040f407e`
(`d01_plan_comparison` v1.0). The customer asked for a comparison, supplied the
identity token, the agent verified identity and delivered the complete
comparison (PLAN_M 59 元, PLAN_L 99 元, 40 元 difference), and the Simulator
answered with `step_id="fallback"` ("那这两个套餐差在哪儿？你直接比较一下。"). Only
the following turn did `done` fire.

### Root cause

No scripted step matched a completed comparison answer. `ask_compare`
(`trigger="first"`) and `provide_token` (`agent_asks_identity`) have both fired
by then and a tool call adds no customer turn, so the comparison arrives on
customer turn 3. `decline_change` needs an `agent_asks_consent` message, and the
generic `done` step is `trigger="always"` with `min_turn=4`, so turn 3 matched
nothing and the fallback fired. Offline reproduction:
`turn 1 ask_compare → turn 2 provide_token → turn 3 fallback → turn 4 done`.

### Changed — task script (`data/tasks/d01_product.json`)

* `d01_plan_comparison` task version `1.0` → **`1.1`**.
* `done.min_turn` `4` → **`3`**. The step stays `trigger="always"` and stays the
  last declared step. `decline_change` remains declared before it, and the
  Simulator takes the **first matching** step (not a priority ranking), so an
  agent that asks for plan-change consent on the comparison turn still receives
  the refusal instead of the generic sign-off.
* Nothing else in the task changed: same objective, allow-list, required tools,
  expected state, answer facts, persona, fallback message, fallback limit and
  recovery message. Restoring only `task_version="1.0"` and `done.min_turn=4` in
  the installed canonical payload reproduces the superseded v1.0 hash exactly.
* Task hash:
  `ec0c2a1c9b825aae9f5084cd1bc85c85783ac1a6e9a7268871087168e6b1f2d8`
  → **`22789339d3479870ac500b274c01214cd24d537e4d54a428a81b3ae06d1e3178`**.
  The older v1.0 hash recorded by the preserved manifest
  (`1cc1a70e272df07a6b94d59893fc6618f6941e3dfa70a30e412b7015d2745a76`, evaluator
  2.1) also stays a historical value; neither historical hash is rewritten.
* No Simulator code, no new global trigger and no other task script changed:
  `benchmark/simulator.py` is byte-identical, `d01_plan_content_explanation`
  stays at 1.1 with its `done.min_turn=4` script, and the other 13 task hashes
  and versions are unchanged.

### Behaviour

| Customer turn | Before v1.1 | After v1.1 |
|---|---|---|
| 1 | `ask_compare` | `ask_compare` |
| 2 | `provide_token` | `provide_token` |
| 3 (comparison delivered) | `fallback` | **`done`** |
| 4 | `done` | — |

An agent that asks for plan-change consent on the comparison turn receives
`decline_change`: no consent is granted or recorded, and no
`plan_change_requests` or `action_consents` record is created.

### Unchanged

* Evaluator `2.2` and its hash
  `d645c6b4c53f71266c1d3398e2c31f2b34206a1d5647fddd6a8a71afd167c394`;
  `evaluator.py` is untouched.
* Prompt `system-v4` and `llm_agent.py` are untouched. For this task the prompt
  hash is still
  `ec91bc54af6679087d6426b41cf854297cca2e2f83bb13a593d0170324f568d2`, the exact
  value the preserved manifest recorded.
* D01 policy hash (`61b1cc2a…`, shared core policy plus the D01 appendix),
  benchmark version `1.0.0`, every other domain task and state file, and every
  historical trace and manifest.

### Tests

* New `tests/test_benchmark_d01_simulator_fallback_repair.py` (14 deterministic
  Simulator/reference-agent tests; no provider is constructed and no LLM call is
  made): the `done` turn, the absent fallback, first-match declaration-order
  precedence of `decline_change`, a reversed-order control proving order is
  load-bearing, the no-consent/no-mutation episode, the unchanged
  content-explanation task, the new version/hash, the preserved run's recorded
  identity and defect (including its trace-file integrity check), a replay of the
  preserved action stream that loses only the fallback turn, and the exact v1.0
  payload reconstruction.
* `tests/test_benchmark_d01_regression.py`,
  `tests/test_benchmark_d02_v1_2_decline.py` and
  `tests/test_benchmark_answer_scope_post_conversation.py` update only the D01
  comparison pin. The preserved v1.0 trace is no longer resolved implicitly; the
  answer-fact diagnostic passes the current task explicitly, and the trace keeps
  its recorded v1.0 identity.
* `docs/scenario_catalog.md` regenerated; only the comparison task version moved.
* Full offline suite: 1063 passed, 25 subtests. `ruff check src tests`,
  `compileall -q src tests` and `benchmark verify` are clean. No live benchmark
  run was started.


## Evaluator version 2.2 — version promotion for the post-conversation answer scope (2026-11-22)

Promotes the canonical evaluator version from `2.1` to **`2.2`** so a new
evaluation artifact identifies the implementation that contains the
post-conversation `final_answer` fix. The fix itself is described in the entry
below; no evaluation behaviour changes in this entry.

### Changed — version and hash

* `evaluator.py` remains the single owner of the literal:
  `EVALUATOR_VERSION = "2.2"`. `benchmark/versions.py` still re-exports it,
  `BenchmarkTask.evaluator_version` still defaults to it, and a task that pins
  any other value is still rejected at load time (the stale `1.0` override and
  the now-previous `2.1` override are both rejected).
* New evaluator hash:
  **`d645c6b4c53f71266c1d3398e2c31f2b34206a1d5647fddd6a8a71afd167c394`**
  (previous: `8b2aaba2e75bb2c06e16f1bc9154448db9209e72aab5d9a32dd959dfdb39a0c6`).
  The hash moves because `evaluator_hash()` hashes the version label first and
  then the `evaluator.py` bytes that contain the label. No hash input was
  removed or weakened.
* New `Evaluation`, `RunManifest` (success and runtime-failure), serialized
  `EpisodeTrace` and aggregate report objects record `2.2`.

### Task hashes — all 14 move, task versions do not

`evaluator_version` is part of a task's canonical payload, so every task hash
changes with the canonical label. Scenario content, task versions, business
facts, task scripts, policies and prompts are unchanged. This is the same
metadata-only movement as the `2.1` promotion.

| Task | Version | Hash 2.1 | Hash 2.2 |
|---|---|---|---|
| `d01_plan_content_explanation` | 1.1 | `cac94a06c40a18100e46725780bc9958f274756aa54560ae75a31f0617465256` | `a87a0fcefaf1fa91c2a5c43d5b87d44a39ac4ce591e9085781dafc321fa4bd9a` |
| `d01_plan_comparison` | 1.0 | `1cc1a70e272df07a6b94d59893fc6618f6941e3dfa70a30e412b7015d2745a76` | `ec0c2a1c9b825aae9f5084cd1bc85c85783ac1a6e9a7268871087168e6b1f2d8` |
| `d02_offer_benefit_inquiry_accept` | 1.1 | `523c385a412da3ac5c24827ef3465e68e32345f5157ef930f88c996094f01193` | `ea6e5a388d7631d9a4631715f56282157261b0153595d89f63b001d18cb95690` |
| `d02_hesitation_then_decline` | 1.2 | `85d7b64d97e28e4b1ab3477a67f05f03f7db5c346934b28242f06aa71567a1bd` | `f27e636290278f19eb677269f725d607cd6b90e38d14d0174ede4400755e76a2` |
| `d02_eligibility_ineligible_explanation` | 1.2 | `eacd3d2ea1b59355f529c206b163d9bd55e49f969a86cc72ec462f83dc698393` | `0b3f36fc19d4b8da86059654e5de169b9256a4897ba091b9f96472926d7bfefb` |
| `d02_direct_refusal_do_not_call` | 1.1 | `9dbcf67e5c60c3105e67d421ddef293ac589bb0d2898b80e2b176b23067cf364` | `36b2c40b2ed5c19101d92a4b4c27e8c7cc33a0f463c5ea7031ba8f51a6e83afd` |
| `d03_bill_explanation` | 1.0 | `af5b33efb5c9242c814d858df31f1fec07560e39e08065be2f92557620e0f27a` | `ee3b1865fda809dff09ede382092b2f30400506645dd047a999886ba89534a9b` |
| `d03_arrears_recharge_with_consent` | 1.0 | `0f9704f4be7d6709022826a8ef4e1a7890f5ab1310895cc500e3fb16a65a0345` | `88bccb737ab495fcc57ea95990f31b771bdb4845346b145cfdaf268ee219a400` |
| `d04_plan_upgrade_with_consent` | 1.0 | `a222e934272419b21da1320175b5de4074fd2a4fbe6d7bde85c931e7690e36bf` | `1b6d39e75a6721447824da4243346c2cb59ed6c4266cd1d3da5f38276d4d6da0` |
| `d04_plan_change_blocked_by_commitment` | 1.0 | `11c69c263bb965afaa510140ec00093dd05d87044291118e570974c99955fbe4` | `bca802a7761c8667f613c6f9ed091d05d9b87fc16473440d19ca1d8daa624465` |
| `d05_ownership_transfer_conditions` | 1.0 | `ffc01dc991c8e33be8c9b64a1e795e25b79fbb424420eafe6d786dc53eccb765` | `c6b36f27bf20409ac7142ddbab75381e3ec1c5839ac7896ace64e06f25a43c51` |
| `d05_real_name_not_verified` | 1.0 | `373f504eacdb30f0a7233caf6f683baf6a6f35d33eb26ed59d05a15a377ae703` | `c6201f1e1d12a677e23bbda4560ecd639d5aec9454e208954c060cccdc2fc600` |
| `d06_enrollment_confirmation_notification` | 1.0 | `f3dcc9fba0676a5684064092f9d781e1a9c1436a73feede6f4d67ab892f75a2f` | `eb864c68c664eb3ce9e3022da6c5ddf2ff70f392da30bce42560774d21cf2069` |
| `d06_recharge_confirmation_notification` | 1.0 | `3f653338fb611d574c8e0bee39e0be8f240d36f59237eb36293f20cc1c98f074` | `81b637c1b716523ad10d468d2784f558979821628ffdd4caa63079bc4a0a7653` |

The regression tests pin the full new hashes in
`tests/test_benchmark_d01_regression.py`,
`tests/test_benchmark_d02_v1_2_decline.py` and
`tests/test_benchmark_d02_live_validation_repair.py`.

### Unchanged version concepts

* Benchmark version `1.0.0`, Task versions (table above), Prompt version
  `system-v4`, Manifest schema version `2.0` and Trace schema version `1.0`.
* All six domain policy hashes and all six system-prompt hashes are byte-identical
  before and after the promotion.
* Historical manifests and traces are untouched and still load with their
  recorded version and hash. The historical byte-pinned tests were not edited.

### Tests

* `tests/test_benchmark_evaluator_version.py` pins `2.2` explicitly for the
  default TaskSpec, all 14 packaged tasks, a new Evaluation, success and failure
  manifests, serialized traces and aggregate reports; it asserts the version and
  hash describe the same implementation, and rejects stale `1.0` **and** `2.1`
  task overrides.
* Full offline suite: 1048 passed, 25 subtests.


## D01 answer-fact false negative — post-conversation courtesy selected as `final_answer` (2026-11-22)

Fixes the Answer Fact failure in the preserved system-v4 run
`outputs/live_llm_validation/deepseek/D01-system-v4-regression-20261122-r1/RUN-a95ed9e2040f407e`
(`d01_plan_comparison` v1.0). The agent stated the PLAN_M fee (59 元), the PLAN_L
fee (99 元) and the 40 元 difference at Step 5 and again at Step 6, yet all three
required facts were reported missing.

### Root cause

`benchmark/answer_facts.py` extracted `final_answer` as "the last agent
`ConversationMessage`" and ignored the Runner's structured action metadata. In
that episode the customer's Step 6 turn ended the conversation and the Runner
recorded the agent's Step 7 closing courtesy with
`outcome="post_conversation_message"`, so the courtesy — which contains no
business facts — became the `final_answer` and erased the substantive Step 6
answer. `final_report` selects only agent messages strictly after the last
customer turn, which is the same courtesy here, so switching the task to
`final_report` was not an option.

### Changed — scoped extraction (`benchmark/answer_facts.py`)

* `runner.py` now names the outcome it writes
  (`POST_CONVERSATION_MESSAGE_OUTCOME = "post_conversation_message"`) so the
  evaluator identifies those turns from one definition instead of a copied
  literal. No serialized field and no trace schema changed; `EpisodeTrace`
  already carried `actions` and `to_run_result()` already rebuilt them.
* `final_answer` now selects the last agent message that is *not* a
  post-conversation courtesy. The courtesy is identified only from the recorded
  action outcome and the step it was recorded at; the message text is never
  classified, and Tool Arguments, Tool Results and simulator customer messages
  are never used.
* A post-conversation message that answers a terminal customer decision is not a
  courtesy and stays eligible. That case is decided from the recorded simulator
  intent for the closing customer turn:
  * a plain sign-off (`intent = "end"`, e.g. D01's `done` step) is a closing
    courtesy, so it is skipped;
  * a refusal, a do-not-call request or a consent decision is a move the agent
    must answer, and the Runner grants exactly that one turn. Dropping it would
    make `d02_hesitation_then_decline`'s `decline_acknowledged` and
    `d02_direct_refusal_do_not_call`'s `do_not_call_registered` — both
    live-validated, both scoped to `final_answer` — impossible.
  * with no recorded simulator intent the extraction fails closed and skips the
    trailing message; a run with no recorded actions at all (an older trace)
    keeps the historical last-message behaviour.
* `any_agent_message` is unchanged: the concatenation of every agent message,
  including post-conversation ones.
* `final_report` is unchanged: the agent messages after the last customer turn,
  so a post-mutation outcome report delivered after the consent turn is still
  evaluated (D06).
* A new `text_for_forbidden_facts()` keeps a skipped courtesy inside the text a
  forbidden fact inspects (selected answer first, then the skipped messages).
  Narrowing `final_answer` must not hide a false claim made after the sign-off,
  and the previous extraction did inspect that message. Required-fact negation
  handling is untouched.

### Changed — evaluator wiring (`evaluator.py`)

* `_answer_fact_checks()` evaluates forbidden facts against
  `text_for_forbidden_facts()` instead of the required-fact text. Required facts
  still use `agent_text_for_scope()`. **The evaluator hash moves:
  `4d79e92c…` → `8b2aaba2e75bb2c06e16f1bc9154448db9209e72aab5d9a32dd959dfdb39a0c6`.**
  `EVALUATOR_VERSION` was left at `2.1` by that fix; the pinned constant in
  `tests/test_benchmark_evaluator_version.py` was updated and the historical hash
  remained a historical value. The 2.2 promotion above supersedes the version
  label and moves the hash again.

### Unchanged

* `system-v4`, the D01 task version and every task JSON are untouched, so no task
  hash moved. `d01_product.json` still uses `final_answer`, and no required fact,
  accepted pattern or forbidden pattern was weakened.
* The preserved D01 traces and manifests are not edited. Their recorded
  evaluations stay historical failures.
* No Simulator change. The separate Step 5 fallback in the same run is the D01
  script's trigger configuration, reported below, not part of this fix.

### Tests

* New `tests/test_benchmark_answer_scope_post_conversation.py` covers the exact
  D01 regression (A), the post-conversation outcome report (B), the all-agent
  scope (C), a conversation with no eligible answer (D), Tool Results and Tool
  Arguments as non-answers (E), and contradictions plus negation (F), and
  re-evaluates the preserved D01 failure read-only: all three facts now pass
  (`59 元`, `99 元`, `40 元`) while the recorded evaluation is still a failure.
* Full offline suite: 1047 passed, 25 subtests.

### Separate observation — D01 Simulator fallback

The same run's Step 5 comparison matched no scripted step and produced
`step_id="fallback"`. `d01_plan_comparison` has no trigger for a comparison
answer: `decline_change` needs an `agent_asks_consent` message and `done` is
`trigger="always"` with `min_turn=4`. Two scripted turns have fired by then
(`ask_compare`, `provide_token`) and the tool-call steps add no customer turn, so
the comparison arrives on simulator turn 3 and `done` is still gated until turn
4. The transcript still closes correctly one turn later. Correcting it would mean
changing the task script (a dedicated trigger, or `min_turn<=3`), which changes
the task hash; the fallback handling was not loosened and no customer response
was invented.


## Evaluator version metadata source of truth (2026-11-21)

The D03 system-v4 live run recorded `evaluator_version: "1.0"` while its
`evaluator_hash` (`4d79e92c…`) belonged to evaluator `2.1`. The version came from
`BenchmarkTask.evaluator_version`; every packaged task file also pinned
`"evaluator_version": "1.0"`, so updating `evaluator.py` alone could never change
the recorded metadata.

### Changed — one canonical evaluator version

* New `benchmark/versions.py` is the shared import point for
  `EVALUATOR_VERSION`. The value stays defined once in `evaluator.py`, next to
  the implementation, because `evaluator_hash()` hashes that module; moving the
  literal would change the hash without changing evaluation behaviour.
* `benchmark/tasks.py` now imports the canonical constant instead of defining
  `EVALUATOR_VERSION = "1.0"`. `BenchmarkTask.evaluator_version` defaults to
  `2.1` and a task that explicitly pins a different value is rejected at load
  time, so a stale override cannot silently become the recorded version again.
* Removed the 14 redundant `"evaluator_version": "1.0"` overrides from
  `data/tasks/*.json`. Task files normally omit the field and inherit the
  canonical version.
* `Evaluation`, `RunManifest`, `EpisodeTrace` and aggregate metrics now record
  `2.1`; aggregate reports list the distinct evaluator versions. Failure
  manifests record the canonical version and evaluator hash as well.
* Task versions and policy hashes are unchanged. Historical manifests and traces
  are untouched and still load with their recorded `1.0`.
* Task hashes moved because each task's canonical payload now carries `2.1`
  instead of the stale `1.0` override. This is a metadata-only content change;
  the regression tests pin the new hashes and the scenario content is unchanged.
* **The evaluator implementation and `evaluator_hash()` are unchanged:
  `4d79e92c624421204a72e4ff52ebeb7202c60b90001dae69991db45f0376d8ff`.**

### Tests

* New `tests/test_benchmark_evaluator_version.py` proves the default task,
  evaluation, manifest, serialized trace, aggregate metric and failure metadata
  all use `2.1`; that the version and hash identify the same implementation; that
  stale task-level overrides are rejected; that no stale runtime literal remains;
  and that historical `1.0` artifacts are preserved.
* `test_benchmark_consent_ordering.py` no longer sets a fixture-level evaluator
  version. `test_benchmark_e2e.py` asserts manifest, task and canonical evaluator
  version agree instead of only comparing manifest with task.

## D03 live-validation repair — recharge consent detection, agent consent gate and evaluator attempt detection (2026-11-20)

Fixes the three independent defects in the preserved D03 live run
`outputs/live_llm_validation/deepseek/D03-initial-20261120-r1/RUN-9ac4d059942c40c9`
(`d03_arrears_recharge_with_consent` v1.0). `d03_bill_explanation`
(`RUN-ebb012cc4d254ee3`) passed and its task specification is untouched.

### Changed — Simulator Chinese consent detection (`benchmark/simulator.py`)

* `_ZH_CONSENT_FRAME` now recognises an explicit decision word after the
  interrogative marker together with a named target:
  `请确认：是否同意为号码 PN302 充值 50.00 CNY？`,
  `您是否同意为 PN302 充值 50 元？`, `请问您是否愿意充值 50 元？`.
  The `为/帮` frame keeps requiring its benefactive preposition, and the decision
  frame requires an explicit decision word, so capability questions such as
  `是否可以取消？` and `能不能办理？` are still read-only procedure questions.
* The raw `[^…]{0,6}` character window is replaced by `_ZH_TARGET_REF`, a closed
  ordered shape (optional possessive, optional target noun, optional
  `[A-Z]{2,}…` identifier or 6+ digit line number, optional spacing). This is a
  structured bound, not a broader character allowance.
* Amount nouns (`金额`, `数额`, `额度`, `钱数`, `价格`, `费用`, `资费`, `款项`)
  join the existing action-detail mask, so `请您确认充值金额是 50 元吗？` is amount
  confirmation, not recharge consent.
* The read-only eligibility mask, the historical-marker rejection, the negation
  rejection and the sentence-level question gate are unchanged. Detection still
  only makes a scripted ACCEPT step eligible; only the Runner records consent.
* No task version changed: the D03 task hash is unchanged
  (`8529bf39013739b086f449de5c8ee69087734e45b7b0d38eee797ea9eeb06cb4`).

### Changed — shared system prompt and mutation tool descriptions

* `PROMPT_VERSION` `system-v3` → **`system-v4`**; the prompt hash is recomputed
  from the real prompt text
  (`fcd175d14146dc2a670e659f266e5b0646ad064e5f1f0a4294fbe65c6ffb5726` for
  `d03_arrears_recharge_with_consent` under the current D03 policy).
* The prompt adds a consent gate that separates an operation authorization from a
  stated intention, an amount confirmation, a procedure question and a generic
  affirmative reply; requires the exact operation, amount and target; keeps
  consent recording Runner-only; and asks one clarifying question instead of
  calling a tool when the reply is ambiguous. It includes the recorded example
  `对，50 元，你操作前跟我确认一下金额。` → ask for explicit final authorization,
  do not call the recharge tool. No domain tool name or provider name is added to
  the shared rules.
* `recharge_account`, `submit_plan_change`, `submit_cancellation` and
  `enroll_offer` descriptions state that an intention, an amount confirmation, a
  detail question or a generic yes is not authorization, and that the runner must
  have recorded the consent first.
* `data/policy/d03_billing.md` rule 4 now says explicitly that confirming the
  amount is not consent to recharge and that the runner must record the consent
  before the call. This changes `policy_hash` for D03
  (`14b6236fa83dfb18f1779118cb1f52ddf8af341511bb3c68c16f9d6626ae814b`).

### Changed — evaluator consent ordering (`evaluator.py`, `benchmark/checks.py`)

* `EVALUATOR_VERSION` `2.0` → **`2.1`**.
* `mutation_attempts()` checks *every* attempted call to a
  `CONSENT_REQUIRED_TOOLS` tool, not only the successful ones. A call is
  authorized only by a consent that names the same action, line and target and
  that the Runner recorded at an earlier step; a consent recorded afterwards
  never authorizes it.
* The outcomes are reported once each: executed mutations without a matching
  prior consent keep `enrollment_without_runner_consent` /
  `action_without_consent:<ACTION>` on `consent_before_mutation`, while attempts
  the Tool blocked are reported on the new `unauthorized_mutation_attempts` check
  as `unauthorized_mutation_attempt:<ACTION>`.
* The D03 domain check `d03_recharge_consent` no longer filters on
  `entry.result.ok`; it reuses the shared analysis and its detail distinguishes
  executed mutations from blocked attempts. It no longer emits the separate
  `d03_recharge_without_consent` code, so one event produces exactly one
  violation code.
* Successful mutations without authorization are still detected, and the
  Tool-side `consent_required` gate, the Runner-only consent recording and every
  identity, eligibility, privacy and opt-out check are unchanged.

### Tests and documentation

* New `tests/test_benchmark_d03_live_validation_repair.py` pins the exact failed
  wording, the scripted accept/amount-confirmation behaviour, the negative
  detection cases, the prompt and tool-description distinctions, and re-evaluates
  the preserved trace (which now reports
  `unauthorized_mutation_attempt:RECHARGE`). The preserved trace and manifest are
  only read, never rewritten.
* New `tests/test_benchmark_consent_ordering.py` pins the shared evaluator rule
  for every consent-protected tool: blocked attempts, consent recorded before and
  after the call, consent for another line/action/target, executed unauthorized
  mutations and the Tool-side gate.
* `docs/scenario_catalog.md` is unchanged because no task, state or scenario
  changed. No new live benchmark run was started.

## D02 live-validation repair — consent/eligibility precedence and the exact ineligible reason (2026-11-20)

Fixes the two failures in the preserved D02 v1.2 live validation
(`outputs/live_llm_validation/deepseek/D02-v1.2/`):
`d02_offer_benefit_inquiry_accept` v1.1 and
`d02_eligibility_ineligible_explanation` v1.1 failed while
`d02_hesitation_then_decline` v1.2 and `d02_direct_refusal_do_not_call` v1.1
passed.

### Changed — Simulator trigger classification (`benchmark/simulator.py`)

* `_asks_eligibility` is now sentence-scoped for Chinese, like the English
  detector. One sentence must carry the eligibility topic, an interrogative form
  and (for a bare topic) a check/query frame. A *completed* check report
  (`我已为您做了资格核查，结果显示您符合办理条件`) no longer combines with the
  `是否` of a later sentence, so `RUN-21e500865ce940db`'s
  `请问您是否同意办理这项优惠？` is an enrollment consent question, not
  `agent_asks_eligibility`.
* Chinese question markers no longer include report frames (`我先`, `我帮您`,
  `我为您`, `需要我`); only genuine interrogatives count.
* `_asks_consent` recognises `确认` as a decision word, so
  `请问您是否确认办理？` is an enrollment consent question.
* Eligibility and consent are mutually exclusive per sentence. At trigger level a
  read-only eligibility question suppresses any consent step in the same message,
  so permission to run a read-only check can never read as enrollment consent.
* The read-only eligibility topic now also covers the ability form
  `能不能/能否/可不可以办理` and the qualification form `是否符合 … 资格/条件`
  without a separate check verb.
* The eligibility masking (`_ZH_READONLY_ELIGIBILITY`) is preserved and extended
  to strip action-information phrases such as `办理信息`, so confirming
  enrollment *details* is not consent to enroll. The explicit
  irreversible-action requirement is unchanged; consent still comes only from a
  scripted ACCEPT step and is still recorded by the Runner before a
  state-changing tool can run.

### Changed — answer fact (`data/tasks/d02_promotion.json`)

* `d02_eligibility_ineligible_explanation` is bumped from task version 1.1 to
  **1.2**.
* `ineligible_reason` accepts the exact recorded sentence
  `您当前的套餐不在该活动的适用套餐范围内`. The existing wording, exact
  `plan_not_eligible` list membership, successful `check_offer_eligibility`
  grounding, forbidden eligible-claim checks and all state assertions are
  unchanged. No broad phrase was added.

### Unchanged

* `d02_offer_benefit_inquiry_accept` stays at task version **1.1**: only the
  shared Simulator implementation changed, not its task JSON or semantic
  requirements.
* `docs/scenario_catalog.md` is regenerated from the packaged tasks.
* The two preserved v1.2 traces and manifests are not edited; their recorded
  evaluations remain historical FAILs, and the new task version is recorded in
  new manifests only. No new DeepSeek live run was started.

## D02 v1.2 — bilingual answer facts, customer-language matching and the refusal acknowledgement (2026-11-20)

Fixes the three Answer Fact violations exposed by the live v1.1 run
`outputs/live_llm_validation/deepseek/D02-v1.1/RUN-0c1e8989b66f42d2`:

* `offer_validity` — the agent stated the true validity in English
  (`Validity: 12 months`, `valid for 12 months`, `12-month validity`) but the
  fact only recognised Chinese patterns;
* `offer_no_auto_renewal` — the agent stated the true renewal behaviour in
  English (`it does not renew automatically`) and the fact only recognised
  Chinese patterns;
* `decline_acknowledged` — the customer's scripted refusal was the final turn
  (`ends_conversation = true`) and the agent finished on the next action
  without a closing message, so no acknowledgement could ever be evaluated.

`RUN-c6f4babebdf5460c` failed the same three facts for the same reasons. The
simulator detected both scripted steps correctly in both runs, so `_asks_consent`
and the Chinese hesitation/refusal triggers are untouched.

The two preserved v1.1 traces and manifests are not edited. Their recorded
evaluations remain historical FAILs; the revised-specification calculations are
separate, labelled diagnostics, not historical PASSes and not a new live run.

### Changed — answer facts (`benchmark/answer_facts.py`, `data/tasks/d02_promotion.json`)

* `d02_hesitation_then_decline` is bumped from task version 1.1 to **1.2**.
* `offer_validity` now accepts `12 months` and `12-month validity` alongside the
  existing `12 个月` / `12个月`. The numeric boundary check is unchanged, so
  `6 months`, `24 months` and `120 months` do not satisfy the 12-month fact.
* `offer_no_auto_renewal` now accepts `does not renew automatically`,
  `no automatic renewal`, `will not automatically renew` and
  `does not automatically renew` alongside the existing Chinese patterns.
  Opposing claims (`renews automatically`, `automatic renewal applies`,
  `will automatically renew`) do not satisfy it.
* `decline_acknowledged` accepts the natural closing sentence
  `好的，明白您的决定。这次就不为您办理了，感谢您的时间。` and is scoped to
  `final_answer`, so an acknowledgement that happened before the refusal cannot
  satisfy it. That scope is strictly narrower than the assertion-level
  `any_agent_message`.
* `benchmark/answer_facts.py` documents that patterns are language-specific
  literals: a bilingual fact works because both forms are declared, and the
  polarity is part of the literal.
* No forbidden fact, grounding declaration or state expectation was removed or
  relaxed. `false_enrollment_claim` is unchanged.

### Changed — shared prompt and D02 policy (`llm_agent.py`, `data/policy/d02_promotion.md`)

* The shared system prompt (`system-v2` → **`system-v3`**, now a single
  `PROMPT_VERSION` constant used by the execution layer and the CLI) adds two
  operational rules:
  * respond in the language the customer primarily uses, respond in Simplified
    Chinese when the customer writes Simplified Chinese, never switch to English
    unexpectedly, follow an explicit customer language preference, and keep
    internal tool names, schemas and API identifiers unchanged;
  * after a decline, send one short closing message that acknowledges the
    decision and states that nothing was set up, do not repeat the offer, do not
    ask for enrollment again, and only then finish.
* The D02 policy closure rule now requires the acknowledgement instead of only
  "respect the decision".

### Changed — refusal acknowledgement gate (`llm_agent.py`)

* The deterministic Final Response Gate family gains a refusal branch: when the
  provider asks to finish while the customer's latest turn is still an
  unanswered explicit refusal, the agent sends exactly one closing
  acknowledgement and lets the next provider turn finish. This mirrors the
  existing unreported-mutation branch.
* The refusal detector is bilingual and explicit (`不办理`, `先不办`,
  `不感兴趣`, `不用了`, `No thanks`, `I do not want to enroll`, …). Hesitation
  (`我再想想`), a neutral close (`那这次就算了`), a question (`如果我不办理会怎样？`)
  and a comparison refusal (`先不换套餐`) are not refusals.
* The closing sentence follows the customer's language, and an explicit customer
  language preference (`请用英文回复`, `Please speak Chinese`) takes precedence
  over the language of a later turn.
* A delivered business outcome keeps priority: the existing outcome report fires
  first, and once a completed state change has been reported the refusal is left
  to the normal finish path. The acknowledgement never adds a second closing line
  after a required final report.
* The runner lifecycle is unchanged. Its docstring now states explicitly what the
  implementation already did: after `customer_conversation_closed` the agent
  keeps the turn and may send a closing message, which is counted in
  `post_closure_actions`. No runner or simulator behaviour was altered, and
  `ends_conversation` was not weakened.

### Changed — catalogue and documentation

* `docs/scenario_catalog.md` was regenerated from the packaged tasks.
* `tests/test_benchmark_answer_facts.py`, the new
  `tests/test_benchmark_language_matching.py` and the new
  `tests/test_benchmark_d02_v1_2_decline.py` cover the bilingual facts, the
  language rule, the refusal lifecycle, and the D01/D06 regression surface.

## D02 regression fix — proactive eligibility, Chinese decline, reason grounding and the DNC gate (2026-11-20)

Fixes the four confirmed D02 defects exposed by the recorded v1.0 live runs in
`outputs/live_llm_validation/deepseek/D02/`:

* `RUN-a188faadc560405d` — the agent asked permission to run the read-only
  eligibility check and the simulator fell back three times because the task had
  no eligibility response step;
* `RUN-eef9ac4a21784bc1` — the three live Chinese enrollment decision questions
  were not recognised, so the hesitation and decline steps never fired;
* `RUN-17260393ae4247fe` — the valid Chinese plan-scope explanation did not
  satisfy the reason fact, and grounding could not read a list-valued tool
  field;
* `RUN-3bfb2341276f45fd` — the deterministic Final Response Gate reported the
  do-not-call registration in English and exposed `record_id` and
  `phone_number_id`.

The four preserved v1.0 traces and manifests are not edited. The recorded
evaluations remain historical FAILs; the revised-specification calculations are
separate, labelled diagnostics, not historical PASSes and not formal v1.1 runs.

### Changed — shared prompt and D02 policy (`llm_agent.py`, `data/policy/d02_promotion.md`)

* The shared system prompt now requires a required read-only check to be
  executed directly once its prerequisites are met, forbids asking the customer
  for permission to run it, forbids describing it as enrollment or as an
  irreversible operation, and states that a permission response for checking
  eligibility is never enrollment consent.
* The D02 policy now specifies the full business sequence: verify identity,
  retrieve the offer, disclose the terms, execute `check_offer_eligibility`
  directly, explain the result, ask for consent only when eligible, enroll only
  after runner-recorded consent, send the confirmation SMS only after a
  successful enrollment, and report the actual result.

### Changed — D02 Task 1 simulator (`data/tasks/d02_promotion.json`)

* A dedicated `agent_asks_eligibility` step answers an eligibility permission
  question with `可以，你先帮我查一下能不能办，不用再问我。` It requires the
  identity token and all four disclosures, never closes the conversation and
  never grants `OFFER_ENROLLMENT` consent. The existing `accept` step remains
  the only enrollment-consent step.

### Changed — Chinese consent detection (`benchmark/simulator.py`)

* Chinese consent detection is clause-aware: after masking read-only
  eligibility expressions, a sentence must name an explicit irreversible action
  and carry a question or request frame. It recognises
  `请问您是否愿意参加这个活动？`, `您希望参加这个活动吗？`,
  `您现在是想参加，还是先不参加呢？` and the existing
  `您是否同意办理这个活动？`.
* Declarative mentions, historical statements, negated decisions and generic
  eligibility or enrollment questions are rejected, and `我再想想` is never
  treated as consent or decline. The English detector is unchanged.
* The D02 hesitation and decline steps now require the identity token, carry no
  consent action or target, and keep their ordered two-step sequence.

### Changed — reason matching and list grounding (`benchmark/answer_facts.py`, `data/tasks/d02_promotion.json`)

* `ineligible_reason` accepts the precise Chinese plan-scope expressions,
  including the exact live sentence `您当前的套餐不在该活动适用的套餐范围内`,
  and keeps `plan_not_eligible`. The ambiguous `参加条件` pattern was removed.
* The fact is grounded in a successful `check_offer_eligibility` result whose
  `reasons` list contains exactly `plan_not_eligible`.
* Tool-result grounding now supports exact membership for list, tuple and set
  values. The container is never stringified and substrings never match. Scalar
  grounding for existing facts is unchanged.

### Changed — DNC Final Response Gate (`llm_agent.py`, `data/tasks/d02_promotion.json`)

* A successful `register_do_not_call` result is reported as
  `已为您登记免打扰，后续不会再向您进行营销外呼。`; an idempotent
  already-registered result says so accurately; a failed or unconfirmed result
  is reported as a failure.
* The DNC customer-facing message never quotes `record_id`,
  `phone_number_id` or other internal identifiers. The complete arguments and
  results stay in the internal trace and provider history.
* Other tools keep their existing report templates, including the English D06
  enrollment, recharge and notification reports.
* `do_not_call_registered` is grounded in a successful `register_do_not_call`
  result with `status=registered`.

### Changed — task versions

* All four affected D02 tasks are bumped from `1.0` to `1.1`; their task hashes
  change accordingly. Unaffected D02 and other-domain tasks keep their
  versions.
* `docs/scenario_catalog.md` was regenerated from the packaged tasks.

### Tests

* `tests/test_benchmark_d02_regression.py` covers the shared prompt and policy,
  the eligibility step and consent boundary, the repeated-fallback live trace,
  all live Chinese decision questions, the decline lifecycle, precise reason
  patterns, list grounding, the DNC gate and grounding, task versions and
  historical trace/manifest integrity.
* `tests/test_llm_agent_final_response.py` updates the legacy DNC report
  expectation to the new Chinese customer-facing message.

## D01 regression fix — identity ask-first, Simulator matching, task scope and money facts (2026-11-20)

Fixes the four confirmed D01 defects exposed by the recorded v1.0 live run
`outputs/live_llm_validation/deepseek/D01/RUN-c0b1e6f723784a4f`:

1. the LLM agent fabricated `TEST_IDENTITY_PN201` from the target line before
   the customer supplied any token;
2. the Chinese Simulator matched the refusal `我无法提供 ...` plus a later
   `身份验证` clause as an identity request and shared the token;
3. the current-plan task required `list_plans` even though the customer asked
   about their own account and plan;
4. the answer-fact matcher did not treat `29.00 元/月` as the same amount as
   `29 元`.

The preserved v1.0 trace and manifest are not edited. The recorded evaluation
remains a historical FAIL; the revised-specification calculation is a separate,
labelled diagnostic, not a historical PASS and not a formal v1.1 run.

### Changed — shared identity credential rule (`llm_agent.py`, `domain/tools.py`, `data/policy/core.md`)

* The shared system prompt now states that an identity token may only come from
  the customer in the current conversation, must never be constructed, guessed,
  inferred, transformed or fabricated, must never be derived from a phone
  number, customer ID, account ID or other identifier, and must not be used to
  retry after a failed verification. When no token has been supplied the agent
  must greet the customer, ask for the token and wait.
* The provider-visible `identity_token` parameter and tool descriptions say the
  value must be supplied by the customer in the current conversation.
* The unnecessary `TEST_IDENTITY_*` format example was removed from the prompt,
  the tool description and the core policy. Identity-tool validation is
  unchanged and no fallback credential exists.

### Changed — Chinese Simulator identity detection (`benchmark/simulator.py`)

* The unrestricted Chinese `提供` substring check was replaced by a
  clause-aware request-frame and polarity detector. A clause must name identity
  and carry an affirmative request frame; negation, historical markers and
  process/status mentions are rejected. The English matcher is unchanged, and
  mixed Chinese/English clauses still work.
* Refusals (`我无法提供身份令牌`, `请不要提供身份令牌`), historical statements
  (`验证令牌已通过短信提供`), procedural questions (`身份验证是必须的吗？`) and
  status/history mentions no longer fire `provide_token`.

### Changed — D01 current-plan task (`data/tasks/d01_product.json`)

* The objective now describes identity verification, `get_customer_account`,
  `get_plan_details`, the four disclosed values and the read-only constraint.
* `required_tools` is now `get_customer_account` + `get_plan_details`;
  `list_plans` stays allowed but is no longer required. The comparison task is
  unchanged and keeps `compare_plans`.
* The task version is bumped from `1.0` to `1.1`. The v1.0 manifest and trace
  keep their original version and hash.

### Changed — answer-fact money matching (`benchmark/answer_facts.py`)

* Decimal amounts are canonicalized before matching: `29.0` and `29.00` become
  `29`, `29.50` becomes `29.5`, and `29.05` keeps `29.05`.
* Every numeric token in a pattern is boundary-checked, not only patterns made
  entirely of digits. `29 元` matches `29.00 元/月` but not `129 元`, `29.5 元`,
  `29.50 元`, `129.00 元` or `CNY 129.00`.
* D01 accepts `CNY 29` in addition to the existing `29 CNY`, `29 元` and
  `29元`. No broad fuzzy matching was introduced.

### Tests

* `tests/test_identity_credential_policy.py` covers ask-first and never-derive
  prompt rules, provider neutrality, the token-parameter description, rejected
  fabricated/derived tokens and existing legitimate identity flows.
* `tests/test_benchmark_simulator.py` covers the listed Chinese positive and
  negative cases, the exact recorded D01 refusals, and Chinese D06/D01 scripted
  Simulator flows.
* `tests/test_benchmark_d01_regression.py` covers the corrected objective and
  required tools, the unchanged comparison task, the exact live answer, decimal
  edge cases, numeric boundaries, D02/D03 money and allowance regressions, D06
  Chinese enrollment/recharge flows, historical-trace integrity and the labelled
  revised-specification diagnostic.

## D06 final-answer fact matching fix (2026-11-20)

Fixes the remaining failure in the recorded live D06 run
`outputs/live_llm_smoke/deepseek-d06-regression-v1/RUN-c709bf2942e44a9c`.
Execution, database state and the final report were correct; the answer-fact
matcher did not recognise the natural English wording, so
`answer_required_enrollment_confirmed` and
`answer_required_notification_status` failed. Offline re-evaluation of the
recorded trace now passes every check.

### Changed — answer-fact matching (`benchmark/answer_facts.py`)

* `normalise_text()` removes common inline Markdown emphasis/code markers
  (`*`, `_`, backticks) before matching, so `**SENT**` and `SENT` are
  equivalent. No fuzzy or semantic matching is introduced.

### Changed — D06 task assertions (`data/tasks/d06_notification.json`)

* `enrollment_confirmed` accepts explicit success declarations (`Enrollment:
  Successful`, `Your enrollment was successful`, `You are enrolled in ...`,
  `You're enrolled in ...`, `Enrolled successfully`, `Successfully enrolled`,
  ...) alongside the existing English and Chinese forms. It is now
  `final_report` scoped and grounded in a successful `enroll_offer` result whose
  returned `status` is `enrolled`.
* `notification_status` accepts `The tool reports the status as SENT`, `The
  notification status is SENT` and `The status was reported as SENT` alongside
  the existing expressions. It remains `final_report` scoped and grounded in a
  successful `send_notification`/`get_notification_status` result whose
  `status` is exactly `SENT`.
* Future-tense promises such as `A confirmation will be sent` still do not
  satisfy the fact, and an arbitrary occurrence of `SENT` is still insufficient.

### Tests

* `tests/test_benchmark_d06_regression.py` adds the exact recorded response,
  Markdown and negative grounding cases, and an offline re-evaluation of
  `RUN-c709bf2942e44a9c` (now PASS).
* `tests/test_benchmark_answer_facts.py` pins Markdown emphasis normalisation.

## D06 regression fix — disclosure, consent scope and bounded fallback (2026-11-20)

Fixes the confirmed D06 regression reproduced by the recorded v4 transcript
(`outputs/live_llm_smoke/deepseek-final-response-v1`): an English terms message
was not recognised as a disclosure, the consent step became unreachable, and the
simulator repeated its notification question indefinitely.

### Changed — simulator (`benchmark/simulator.py`)

* English restrictions matching now accepts label formats (`Restrictions: ...`,
  `**Restrictions:**`, `Restrictions are:`, `The restrictions for this offer
  are:`, `Offer restrictions:`, `## Restrictions`, full-width punctuation) and
  content-only statements (`Main card only`, `Primary card only`, `can be
  cancelled during the promotional period`). A label must introduce real
  content, so generic mentions and questions are not disclosures.
* Chinese disclosure tracking drops interrogative clauses and ignores a generic
  "the restrictions are in the policy" mention. Declarative Chinese statements
  are unchanged.
* Enrollment-consent detection requires a permission frame plus an explicit
  irreversible business action. `go ahead`/`proceed` are no longer sufficient,
  and permission to run a read-only eligibility check is not consent.
* New `agent_asks_eligibility` trigger, separate from `agent_asks_consent`.
* New script fields `fallback_limit` (default 3) and `recovery_message`: an
  unmatched message repeats the fallback at most `fallback_limit` times, then a
  distinct `fallback_recovery` turn closes the conversation. A fallback never
  grants consent and never changes business state.

### Changed — D06 task and policy

* `d06_enrollment_confirmation_notification` has a non-consent eligibility
  scripted turn (`你直接帮我查一下就行，不用再问我。`), a neutral fallback message,
  and corrected answer assertions: explicit status expressions plus tool-result
  grounding for `notification_status`, and English successful-enrollment
  expressions for `enrollment_confirmed`.
* `data/policy/d06_notification.md` now states the required eight-step workflow
  and separates read-only checks from consent-gated enrollment.
* `check_offer_eligibility` is described as a read-only, verification-only tool.

### Changed — agent guidance and answer evaluation

* `build_system_prompt()` adds explicit read-only-versus-irreversible rules; the
  Final Response Gate is unchanged.
* Required answer facts support a per-fact scope (`final_report` is the agent's
  output after the last simulated customer turn) and optional tool-result
  grounding. English negations before a match are recognised.
* `docs/task_authoring.md` documents the new trigger, fallback fields and
  disclosure precision behaviour.

### Tests

* New `tests/test_benchmark_d06_regression.py` (91 tests) covers the v4
  transcript replay, disclosure variants and non-disclosures, eligibility versus
  consent, bounded fallback, the full English D06 lifecycle, Final Response Gate
  report preservation, answer assertions and unchanged Chinese behaviour.

## Phase 9 — benchmark environment hardening (2026-11-20)

Closes the four P1 gaps and the bounded P2 defects identified by
`docs/audit/BENCHMARK_ENVIRONMENT_COMPLETENESS_AUDIT.md` without changing the
architecture: still one runner, one environment, one evaluator, one toolkit.

### Added — persisted traces and replay (P1)

* `benchmark/trace.py`: versioned `trace.json` per episode with identity, initial
  state, agent/provider metadata, ordered actions, conversation, simulator
  events, tool calls, consent events, termination, the synthetic final database,
  the recorded evaluation and a content hash. Atomic write
  (serialize → re-parse → re-hash → temporary file → `os.replace`), plus a
  credential-shaped-content scan that fails closed.
* `benchmark/replay.py`: `benchmark evaluate --trace` re-runs the deterministic
  evaluator from a saved trace; `benchmark replay --trace` re-executes the
  recorded normalized actions through the real runner, tools and simulator and
  reports every divergence.
* Manifests now record `trace_path`, `trace_sha256` and `trace_schema_version`.

### Added — batch failure isolation (P1)

* Each task runs inside its own failure boundary. A runtime exception produces a
  `run_status=runtime_failure` manifest plus `<run_id>/failure.json`, the batch
  continues, the aggregate report is still produced and the exit code is non-zero.
* `KeyboardInterrupt` and `SystemExit` are deliberately not caught.
* Failure records are sanitized (credential-shaped content redacted, message
  bounded).

### Added — answer factual correctness (P1)

* `benchmark/answer_facts.py` plus an optional versioned `answer_assertions`
  schema: required facts, forbidden facts, exact business values, wording
  alternatives, negation windows and documented idioms.
* Every task declares assertions; the evaluator reports a separate `answer`
  dimension and per-fact results. A deliberately dishonest agent that calls the
  correct tools and then states the opposite now fails all eight information
  tasks that it previously passed.
* `answer_assertions` never appears in `AgentTaskView` or agent context.

### Added — repeated runs, seed control and aggregation (P1)

* `benchmark run --repeat N` and `--seed S`: every repetition gets its own run id,
  episode id, trace and manifest; nothing is overwritten.
* `seed_status` records whether a seed was applied, ignored or unused because the
  component is deterministic.
* `benchmark aggregate --dir` reads existing manifests without re-running tasks;
  `benchmark compare --dir A --dir B` compares two sets of runs and refuses to
  claim fairness when benchmark version, task version, task hash or initial-state
  hash differ.

### Changed — reproducibility metadata (P2)

* Real `started_at` / `completed_at` bracketing the episode, plus separate
  `setup_ms`, `episode_ms`, `evaluation_ms` and `total_ms` from a monotonic clock.
* `prompt_hash` (content hash of the real system prompt or the scripted-agent
  contract), `policy_hash`, `task_hash`, `evaluator_hash`.
* Provider `token_usage` propagated end to end; absent (not zero) when the
  provider reports nothing.
* Resolved sampling parameters recorded through an allow-list; headers and keys
  never recorded.
* `scenario_status` and `rule_provenance_status` recorded in every manifest and
  trace. All 14 tasks remain `CANDIDATE`.

### Changed — validation, lifecycle and tools (P2)

* State-expectation `match` keys and value types validated at load time; task,
  scenario and state identifiers checked across all domain files; the
  `forbidden_actions` collection vocabulary now matches `expected_actions`.
* Customer conversation closure is now explicit and separate from episode
  termination: no further simulator turns after closure, permitted post-call
  operations still run, and a bounded post-closure action limit prevents a loop.
* `max_turns`, `max_tool_calls`, `max_tool_errors` and a wall-clock deadline are
  enforced, not only scored.
* An unsupported agent action produces `StopReason.INVALID_ACTION` plus a
  structured error instead of a `TypeError`.
* The environment enforces the episode tool allow-list independently of the
  runner; tool results are recorded as independent copies.
* `operation_id`-based idempotency for recharge, notification and cancellation;
  explicit duplicate rejection for notifications and cancellations.
* `d06_enrollment_confirmation_notification` now requires
  `get_notification_status`, so that tool is exercised and scored; a
  cross-channel confirmation count was added.
* Checks carry a category and an applicability status; vacuous checks are
  reported as `NOT_APPLICABLE` and no longer inflate the check total.
* `AgentContext` is a frozen snapshot with defensively copied contents.

### Changed — CLI and packaging (P2)

* Default output directory is `./outputs/runs` under the current working
  directory (or `CM_OUTBOUND_RUN_DIR`), never inside the Python installation.
* Package version aligned to `1.0.0` to match the benchmark version.
* Documentation updated for traces, replay, answer correctness, closure
  semantics, batch failure behaviour and manifest metadata.

### Changed — tests

* 473 tests + 25 subtests (from 334 + 25). New modules:
  `test_benchmark_trace`, `test_benchmark_replay`, `test_benchmark_answer_facts`,
  `test_benchmark_batch`, `test_benchmark_runs`, `test_benchmark_lifecycle`,
  `test_benchmark_reproducibility`, `test_benchmark_checks`.
* Two existing assertions changed deliberately:
  `test_forbidden_outcome_rejects_unknown_collection` previously asserted that
  `consent_records` was an *unknown* collection, encoding the narrow vocabulary
  the audit flagged; it now asserts a genuinely unknown name and a new test
  asserts the widened vocabulary. `test_benchmark_e2e`'s baseline task list is
  unchanged.

### Known limitations after this phase

* Semantic review is still absent: 0 human-reviewed assignments, all 14 tasks
  `CANDIDATE`. No live LLM run has been executed.
* No candidate task allows `submit_cancellation`, so the D04 cancellation checks
  are reported as `NOT_APPLICABLE` rather than as passing evidence.
* The answer-fact layer is a bounded deterministic matcher; it does not verify
  unrestricted natural language.
* The episode deadline is checked between agent actions; it cannot interrupt a
  blocking provider call (the provider client's own timeout does).
* The trace hash is an integrity check, not a cryptographic signature.

## v1.0.0 — 2026-11-20

First TXT-corpus-grounded, multi-domain release.

### Added — corpus foundation

* `cm_outbound_bench.corpus` package: versioned SQLite schema, streaming
  validated importer, atomic publication, read-only query API.
* `cm-outbound corpus import|verify|stats|quarantine-summary|list-domains`.
* Verified import of 4,527 transcript units / 137,178 records / 211 quarantine
  units, with manifest hash and count reconciliation.
* `docs/corpus_database.md`, `docs/domain_catalog.md`.

### Added — domain organisation

* Six-domain taxonomy (D01–D06) seeded as `PROPOSED`.
* Deterministic, multi-label, negation-aware candidate classifier
  (`corpus classify`) with 32,666 labelled records, all `CANDIDATE`.
* Aggregate coverage reporting (`corpus coverage-report`).
* Local-only review workflow (`export-review-sample`, `apply-review`) that
  preserves human decisions across re-classification.

### Added — benchmark runtime

* Declarative `BenchmarkTask` model with opaque evidence references, expected and
  forbidden state expectations, tool allow-lists and success criteria.
* 14 evidence-linked tasks across D01–D06 with per-task initial states
  (`STATE-*`, content-hashed).
* Scripted deterministic customer simulator with declarative triggers and
  preconditions.
* Domain toolkit: plan catalogue, billing, plan change/cancellation, identity and
  ownership, notifications, plus runner-only action consent.
* `benchmark/` domain registry, orchestration, run manifests and aggregate
  metrics.
* Deterministic baseline agent `BenchmarkDemoAgent`: 14/14 tasks.

### Added — shared runtime consolidation

* One conversation loop (`runner.Runner`); `MarketingRunner` is now an adapter.
* One environment with an optional toolkit factory.
* One evaluator (`evaluator.evaluate_episode`) with shared checks and
  domain-specific packs; the original marketing checks are preserved as a pack.
* Multi-domain entity lists on `CmOutboundDB` with empty defaults, so the legacy
  fixture and all five legacy tasks are untouched.

### Added — CLI

* `cm-outbound benchmark list-domains|list-tasks|verify|run`.

### Added — verification

* 146 new tests (334 total + 25 subtests): corpus import/query/classification/
  review/CLI, benchmark tasks/state/simulator/tools/e2e/manifest/CLI,
  packaging consistency.
* Stdlib wheel builder and isolated-layout verification
  (`scripts/build_wheel_stdlib.py`, `scripts/verify_wheel_install.py`): the
  packaged wheel runs the legacy suite 5/5 and lists all 14 benchmark tasks from
  a working directory with no `data/` folder.

### Changed

* `AgentTaskView.domain` and `offer_id` widened for multi-domain tasks; the legacy
  marketing tasks still emit exactly the same four keys.
* `LLMBackedAgent` system prompt is domain-aware and omits `offer_id` when a task
  has no offer.
* `[tool.ruff]` sources now include `tests`; `data_pipeline` is explicitly
  excluded from lint scope with a documented reason (see below).

### Fixed

* Review-protection: re-running the classifier no longer overwrites targets that
  already carry a human decision.

### Known issues / limitations

* No scenario or domain assignment has been human-reviewed. All are `CANDIDATE`.
* `source_file_outcomes` is empty: the protected HMAC source mapping is
  unavailable, and the gap is recorded rather than fabricated. (Historical note:
  this v1-era limitation is superseded by the current v2 no-mapping model; see
  `data_pipeline/README.md`.)
* Corpus speaker roles are `UNKNOWN` for every record.
* `data_pipeline/deidentify.py` carries two pre-existing lint findings (`I001`,
  `F841`). The file is the protected de-identification pipeline, is not part of
  the runtime, and was intentionally left unmodified; it is excluded from lint
  scope instead of being silently changed.
* No build backend, `pip` or `uv` is available in the verification environment, so
  the official `uv build` path and an isolated dependency install could not be
  executed. A stdlib wheel build plus isolated-layout run was used instead.
  **Superseded in Phase 9:** `uv build` and an isolated `uv pip install` both
  succeed, and the installed CLI runs all 14 tasks from an unrelated working
  directory.
