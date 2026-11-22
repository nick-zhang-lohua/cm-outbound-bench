# Release Readiness Report — CM Outbound Benchmark 1.0.0

**Status:** Engineering MVP release candidate; packaging frozen.
**Evaluated on:** 2026-11-22 (Asia/Hong_Kong).
**Final documentation audit:** 2026-11-22 (documentation and reporting only; no
evidence changed).
**Final packaging freeze:** 2026-11-22 (lockfile metadata, README formatting and
this report's wording only; no evidence changed).
**Repository:** this checkout (`cm-outbound-bench/`), validated from the WSL2
Ubuntu environment recorded in section A.

This report is generated from the installed catalogue, the frozen configuration
(`docs/development/release_config.json`), the persisted run manifests/traces and
the offline test suite. It never infers a result that is not backed by an
artefact on disk.

---

## 0. Executive verdict

**READY (Engineering MVP).** All 14 candidate tasks across the six domains
executed and passed under one frozen configuration in every one of the six clean
rounds, with zero runtime failures, zero evaluation failures, zero incomplete
episodes and zero policy violations. Three independent seeds per task were run
for stability; **all 84 episodes across the six rounds passed**. Two earlier
stability attempts hit an external provider outage and are reported separately
in section C.3; they are not counted as task failures.

This is an **engineering** readiness verdict. It is **not** a claim of
human-expert business validation, statistical stability, or production
telephony readiness. All tasks remain `CANDIDATE` with
`SYNTHETIC_BENCHMARK_ASSUMPTION` provenance; see section E.

---

## A. Final configuration

All values below are computed from the installed catalogue by
`python scripts/release_config.py` and copied verbatim into
`docs/development/release_config.json`.

| Component | Value |
|---|---|
| Benchmark version | `1.0.0` |
| Shared system prompt | `system-v4` (the prompt embeds the task objective, so the prompt hash is per task; see the task table) |
| Evaluator version | `2.4` |
| Evaluator hash | `a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2` |
| Manifest schema | `2.0` |
| Trace schema | `1.0` |
| Domains / tasks | 6 / 14 |
| Task status | all `CANDIDATE` |
| Provenance | all `SYNTHETIC_BENCHMARK_ASSUMPTION` |
| Provider | DeepSeek, OpenAI-compatible protocol (`deepseek`) |
| Model | `deepseek-flash` |
| Tool mode | native `function_calling` |
| Sampling | `temperature=0.2`; `thinking.type=disabled`; recorded `seed` per run |
| Provider config | `./providers.toml`; credential read from `DEEPSEEK_API_KEY` only |
| Runtime | WSL2 Ubuntu, Linux `6.18.33.2-microsoft-standard-WSL2`, Python `3.12.16`, Pydantic `2.14.3` |

### Domain policy hashes

| Domain | Policy hash |
|---|---|
| D01 | `61b1cc2a69524193770f2095303554167c0fc922f7e32d858f3a6bfc0adeb1d2` |
| D02 | `f7a996f82289d9c44212d945a238b0dee109e849d1744ee0d900f4236ebb5f1d` |
| D03 | `14b6236fa83dfb18f1779118cb1f52ddf8af341511bb3c68c16f9d6626ae814b` |
| D04 | `12cf91d209562a7236d003a7991efb34bc7889b1f5b6882d5571adc2537a62e5` |
| D05 | `fa681a435a87457c632dffc8495b28cd47ebb7a83b5d3b35db5d150bd6557235` |
| D06 | `4c5053792e8051b8d6cc1890d0a0afd2891134da6f0e41415e0f0085a5bcc301` |

These are the hashes of the exact policy text shown to the agent (shared
`core.md` plus the domain appendix).

### Tasks, versions, hashes and per-task identities

| Domain | Task | Version | Task hash | Initial state | Initial tools hash |
|---|---|---|---|---|---|
| D01 | `d01_plan_content_explanation` | 1.1 | `d525266380bd0e835b97209f58de74ed36d12652a327c3026ff33ce62f4735ae` | `STATE-D01-001` | `046c244e2b9ddc673856f3603db3c8b1553b1c9936c502e2cbe7a475e99efc0b` |
| D01 | `d01_plan_comparison` | 1.3 | `1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632` | `STATE-D01-002` | `956b7c3e1d8777b322eef704c9d5a28266d14930f417a19403259bba76ca768c` |
| D02 | `d02_offer_benefit_inquiry_accept` | 1.1 | `3fa0fef80981dfca2c62445d4361a20347a52898cc3d9bfc9263f61411a4b93a` | `STATE-D02-001` | `7740f777ce98f0a708035a6f6b68ff02edb4fb7121283ea460e905f4b5e6cc05` |
| D02 | `d02_hesitation_then_decline` | 1.3 | `71a28c39721d26e22f182b0d3f7726c64be8ec4769d52d094d1e5e4420c1c53c` | `STATE-D02-002` | `7740f777ce98f0a708035a6f6b68ff02edb4fb7121283ea460e905f4b5e6cc05` |
| D02 | `d02_eligibility_ineligible_explanation` | 1.4 | `41f0b0001834d6b1b0b5142b78ded4381ecfc2336057cc41fc08179935632129` | `STATE-D02-003` | `c728e415e69d41b1b091f8cb25c61ea73791385ece2fd979c6acea26c580a7b7` |
| D02 | `d02_direct_refusal_do_not_call` | 1.1 | `37ac9068499fa9898ddded8f8ba65e5ae32f3b2d5239b0ae7a120b3ce95beadc` | `STATE-D02-004` | `4955318d9100c0a82156a288ed06cf0ed02a4813f828c9af048fa24cfb40ae90` |
| D03 | `d03_bill_explanation` | 1.1 | `b47fc44c746924a04b3b34fd3bd9c4b9cbfd2374bdb49fcdb779bd55359c9a6d` | `STATE-D03-001` | `1eb1b4748b0a134de800e1dbe2574a14438043b08bad29dce72480986ee8050c` |
| D03 | `d03_arrears_recharge_with_consent` | 1.0 | `68a6d220cf25e95033a70148746c1f7daa54327cae24d5e25388b960db7ce557` | `STATE-D03-002` | `f807cb1eb62c6a63c42fa39e7b0c2d9d505157c36234bcb2163084cc4bbd8e67` |
| D04 | `d04_plan_upgrade_with_consent` | 1.1 | `131324d961318229455aec00525627bcbe6597328f1f2b71940e8f4735ea7225` | `STATE-D04-001` | `fe56d6f238b6a6c4496ed2fd9da25ff5a97cecf1ccd58758bacd4f027b7f5331` |
| D04 | `d04_plan_change_blocked_by_commitment` | 1.2 | `9d3838b36d7e21395890f1429b7bf946b6002782a5e5ee01d3f7ff6f1fe173fd` | `STATE-D04-002` | `fe56d6f238b6a6c4496ed2fd9da25ff5a97cecf1ccd58758bacd4f027b7f5331` |
| D05 | `d05_ownership_transfer_conditions` | 1.3 | `adeaaf1f74041b908ce6de769f9d49cf1bec65ef30b2426c7332dbcd80c00665` | `STATE-D05-001` | `219ab8ab2f51f36910c2ecaa8a94f5967ada20b76517c62d6a67f87f43f22a56` |
| D05 | `d05_real_name_not_verified` | 1.3 | `809041a3ebb4b31df32886e6a792020050c06d3eca487ec34085595050249240` | `STATE-D05-002` | `edd9962931295450572e9e42affd24f317408944c8e50af8f9daee61cfe8ab8d` |
| D06 | `d06_enrollment_confirmation_notification` | 1.1 | `9326e6ceea9e0e91f17437689e3712c562e7888aef26b740cc96e38b7d3399e5` | `STATE-D06-001` | `bf704f52e2925e3b2b71c0dab95df6938e6569547b8ad175920ec3730956ee9c` |
| D06 | `d06_recharge_confirmation_notification` | 1.3 | `1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74` | `STATE-D06-002` | `fdc45cd6064c5d04c42cbbafea33bd3abb38e1ab93f33eca3e6a7cd21087e476` |

Per-task system-prompt hashes (the prompt embeds the objective, so it is
per-task by construction) are recorded in every manifest and in
`docs/development/release_config.json`.

## B. Domain validation matrix

Source round: `outputs/live_llm_validation/deepseek/final-rc10-system-v4-evaluator-v2.4-20261122`
(output-directory identifier), seed `20261122`, one episode per task, executed
2026-11-22 10:54:22–10:56:57 UTC. Every row is a persisted episode whose
`trace_sha256` was re-verified against the file on disk. The machine-generated
matrix of the second release-candidate round under the identical frozen
configuration (`final-rc11-…`, section C.4) is stored at
`docs/reports/validation_matrix_final.md` / `docs/reports/validation_matrix_final.json`;
both rounds contain one verified episode for each of the 14 tasks.

**Common identity for every row:** evaluator `2.4`
(`a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2`), prompt
`system-v4`, provider `deepseek` / `deepseek-flash`, repetition count `3` (this
round plus the two stability rounds in section C).

### B1. Task identity, run identity and trace integrity

| Domain | Task | Ver | Task hash | Run ID | Status | Trace SHA-256 |
|---|---|---|---|---|---|---|
| D01 | `d01_plan_content_explanation` | 1.1 | `d525266380bd0e835b97209f58de74ed36d12652a327c3026ff33ce62f4735ae` | `RUN-8b14168f3b3f4f4c` | success | `a009cdf1a95bebecd6c94c04013ace3082cd5a338cea1c5fe31eaaf4b3d4e8f1` |
| D01 | `d01_plan_comparison` | 1.3 | `1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632` | `RUN-5186368343c04137` | success | `c333b2362ce1f6e9561db4e012b59c51f6f4694c6588b4d9f0b865af53915bc7` |
| D02 | `d02_offer_benefit_inquiry_accept` | 1.1 | `3fa0fef80981dfca2c62445d4361a20347a52898cc3d9bfc9263f61411a4b93a` | `RUN-84248791a5d84b0c` | success | `bd04eea4156f2ffae99390d037adb2b0da8997637bfacfd0e7e30836ee7f1b35` |
| D02 | `d02_hesitation_then_decline` | 1.3 | `71a28c39721d26e22f182b0d3f7726c64be8ec4769d52d094d1e5e4420c1c53c` | `RUN-9c8599d5b4414e9b` | success | `672f1cbd7959a102e19def04d2e6953347782318f76bb1b8ff2c049518d6d1cd` |
| D02 | `d02_eligibility_ineligible_explanation` | 1.4 | `41f0b0001834d6b1b0b5142b78ded4381ecfc2336057cc41fc08179935632129` | `RUN-30f09863c7694022` | success | `dac92c231ed6c712c37a18de97ac9ba74ba73fef5e4862e2ef2f4161bd3fa523` |
| D02 | `d02_direct_refusal_do_not_call` | 1.1 | `37ac9068499fa9898ddded8f8ba65e5ae32f3b2d5239b0ae7a120b3ce95beadc` | `RUN-4404bc7a021d418c` | success | `a62893841bf6243b74e3579c437277edbe4fc2880232d6112f70f69fcb146744` |
| D03 | `d03_bill_explanation` | 1.1 | `b47fc44c746924a04b3b34fd3bd9c4b9cbfd2374bdb49fcdb779bd55359c9a6d` | `RUN-e057f306a9b3477a` | success | `ff6a17f4a51cd5f1bd0f8d209becfb1c25f794dfd2d4a4ebb2feed2c072ce2dc` |
| D03 | `d03_arrears_recharge_with_consent` | 1.0 | `68a6d220cf25e95033a70148746c1f7daa54327cae24d5e25388b960db7ce557` | `RUN-d0c2c95b7f344ae7` | success | `360e51fd056713039c12ff913aa5b2134e41ae6b0fa1af2aa716ec76e4358368` |
| D04 | `d04_plan_upgrade_with_consent` | 1.1 | `131324d961318229455aec00525627bcbe6597328f1f2b71940e8f4735ea7225` | `RUN-56fa850d33234986` | success | `4df1141f03d188252690abe24f786e6ddea3bff402345689aa29e7b31633c717` |
| D04 | `d04_plan_change_blocked_by_commitment` | 1.2 | `9d3838b36d7e21395890f1429b7bf946b6002782a5e5ee01d3f7ff6f1fe173fd` | `RUN-89dd74696f2647d8` | success | `8bc4492a24e339056a88b3549ca3f3c612c12a0e9dc7665fcf6cbf5fba04d14e` |
| D05 | `d05_ownership_transfer_conditions` | 1.3 | `adeaaf1f74041b908ce6de769f9d49cf1bec65ef30b2426c7332dbcd80c00665` | `RUN-02eb53d38afd45e3` | success | `39cfce84955225b7c0e0910b625094a142f12ed5cba7157bf132cec8dbae0e33` |
| D05 | `d05_real_name_not_verified` | 1.3 | `809041a3ebb4b31df32886e6a792020050c06d3eca487ec34085595050249240` | `RUN-8a180c1aefac441d` | success | `67aa06c12df0c588e8c25b2b4bf48cc572a1aad9ad28bb36bcd45ae32c2348ab` |
| D06 | `d06_enrollment_confirmation_notification` | 1.1 | `9326e6ceea9e0e91f17437689e3712c562e7888aef26b740cc96e38b7d3399e5` | `RUN-27193e9fcaf74e6e` | success | `d5ad91056e94ae3d9cd99dd21f225bd3e9b884f4c28b10c6c009b7fb7e008e46` |
| D06 | `d06_recharge_confirmation_notification` | 1.3 | `1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74` | `RUN-de780b83b5534272` | success | `a3627746b906f1a343c3eff8bae0835c5f49d2ee91840530bbf722fdb1cf63c4` |

### B2. Run metrics

| Domain | Task | Tools | Turns | Latency (ms) | Tokens | Answer facts | Policy violations | Final state |
|---|---|---:|---:|---:|---:|---:|---|---|
| D01 | `d01_plan_content_explanation` | 3 | 8 | 10675 | 23754 | 1.00 | none | unchanged |
| D01 | `d01_plan_comparison` | 3 | 6 | 12062 | 23121 | 1.00 | none | unchanged |
| D02 | `d02_offer_benefit_inquiry_accept` | 5 | 7 | 12285 | 36883 | 1.00 | none | changed |
| D02 | `d02_hesitation_then_decline` | 3 | 9 | 10695 | 32274 | 1.00 | none | unchanged |
| D02 | `d02_eligibility_ineligible_explanation` | 3 | 6 | 8271 | 23603 | 1.00 | none | unchanged |
| D02 | `d02_direct_refusal_do_not_call` | 1 | 3 | 4160 | 11411 | 1.00 | none | changed |
| D03 | `d03_bill_explanation` | 3 | 13 | 13913 | 36405 | 1.00 | none | unchanged |
| D03 | `d03_arrears_recharge_with_consent` | 3 | 7 | 8410 | 26686 | 1.00 | none | changed |
| D04 | `d04_plan_upgrade_with_consent` | 5 | 10 | 14497 | 44579 | 1.00 | none | changed |
| D04 | `d04_plan_change_blocked_by_commitment` | 3 | 6 | 8697 | 23110 | 1.00 | none | unchanged |
| D05 | `d05_ownership_transfer_conditions` | 3 | 10 | 11462 | 26698 | 1.00 | none | unchanged |
| D05 | `d05_real_name_not_verified` | 3 | 10 | 10734 | 27273 | 1.00 | none | unchanged |
| D06 | `d06_enrollment_confirmation_notification` | 6 | 7 | 13788 | 43938 | 1.00 | none | changed |
| D06 | `d06_recharge_confirmation_notification` | 5 | 7 | 9302 | 37124 | 1.00 | none | changed |

“changed” is expected only for the four tasks whose business operation mutates
state (enrollment, recharge, plan change, notification); the other ten are
read-only or refusal scenarios and must leave the state hash untouched.

### B3. Per-task prompt hash

| Domain | Task | Prompt hash |
|---|---|---|
| D01 | `d01_plan_content_explanation` | `dceca853dd875e6c14b60ec180eb72b605eae0833ac02622ddc3ec5bf56e1f85` |
| D01 | `d01_plan_comparison` | `ec91bc54af6679087d6426b41cf854297cca2e2f83bb13a593d0170324f568d2` |
| D02 | `d02_offer_benefit_inquiry_accept` | `dc3ec9de524965cf7fd64efcc94233239efadb3b54297a941304d95e1033dcb0` |
| D02 | `d02_hesitation_then_decline` | `0ae7036934842cc631aeaaa17f77630d83e56428b8f9e177bcbd77c0e681086b` |
| D02 | `d02_eligibility_ineligible_explanation` | `e5227aa98d1db3fbf7589af286dbc60288d9f1d994324770f3c3b735e65229d1` |
| D02 | `d02_direct_refusal_do_not_call` | `2f3f81f688f6a5ac23f41a5da38df1f36275614e34617fc2d3998886cd04afc6` |
| D03 | `d03_bill_explanation` | `0bd007d7ab113d384b541b8a2b519efa981612f3849ff7fc9bf8adf311d0f6e0` |
| D03 | `d03_arrears_recharge_with_consent` | `fcd175d14146dc2a670e659f266e5b0646ad064e5f1f0a4294fbe65c6ffb5726` |
| D04 | `d04_plan_upgrade_with_consent` | `bfb49664e31b4edf4ee45d183f6b41114488e1aa6af45b99de99d56845e2578f` |
| D04 | `d04_plan_change_blocked_by_commitment` | `d9aee5dc0d05623d42be7e88dce778c87fa2af8a34c76eddee15731551b0e069` |
| D05 | `d05_ownership_transfer_conditions` | `978836aebd19bbcc2359b83b2a8e8705df05fac7ee3ef5ef65fac8b00b8de9d8` |
| D05 | `d05_real_name_not_verified` | `6e2d1f8de12d4df953fb8d1d12b49a4948b215a0d058b16b6fd369811bde7e3a` |
| D06 | `d06_enrollment_confirmation_notification` | `c03d96f4971250221c3eee261356cc778b8f0fb57f17490fca26b51e7c657c4d` |
| D06 | `d06_recharge_confirmation_notification` | `cd45480e389f30f81b143f1b085221c80552b177f4aea90d2c836b82a47c4f6e` |

The policy hash for each row is the domain policy hash in section A; the same
`policy_hash` is recorded in the manifest and trace of that run.

### B4. Checks exercised, and every conditional `NOT_APPLICABLE`

No check was converted to `NOT_APPLICABLE` in order to obtain a pass. In the
final round each episode evaluated between **14 and 23 applicable checks**
(83 distinct names across the round: 45 answer-fact checks, 26 state expectations,
plus lifecycle, tool and policy checks). The complete set of `NOT_APPLICABLE`
names and their frequency was audited:

| Check | N/A / 14 | Legitimate reason and when it becomes load-bearing |
|---|---:|---|
| `unauthorized_mutation_attempts` | 14 | Applies only when the Tool *blocked* a consent-gated mutation. No blocked attempt occurred in a passing run. The five tasks that *executed* such a mutation exercised `consent_before_mutation` instead. Becomes applicable and fails if a blocked attempt lacked consent. |
| `offer_cancellation_consent` | 14 | No task requests cancellation. |
| `no_marketing_after_opt_out` | 13 | Applies only where an opt-out is registered (the DNC task). |
| `sms_after_enrollment` | 13 | Applies only where an enrollment occurs. |
| `consent_before_mutation` | 9 | Applies exactly in the five tasks with a consent-gated mutation. |
| `identity_verification` | 2 | `d01_plan_comparison` explicitly requires no identity check (public catalogue comparison); `protected_account_access`/`protected_disclosure` stayed applicable and passed. |
| `d02_eligibility_before_enrollment`, `d02_offer_terms_disclosed` | 3 | Apply only to the enrollment task. |
| `d03_recharge_consent`, `d03_recharge_consistency` | 1 | Apply only to the recharge task. |
| `d04_plan_change_consistency`, `d04_no_noop_change` | 1, 1 | Apply only where a plan change occurs (the blocked task must not change the plan). |
| `d04_cancellation_applied` | 2 | Applies only where a cancellation is requested. |
| `d05_transfer_conditions` | 2 | Applies only if an `OWNERSHIP_TRANSFER` consent exists — see limitation 13. |
| `d06_no_duplicate_notification` | 2 | Applies only when ≥2 notifications exist. |
| `protected_account_access` | 1 | Applies only where an account-access tool is in the allow-list. |

`consent_before_mutation` being applicable in exactly five episodes matches the
five `consent_action` declarations in the catalogue
(`OFFER_ENROLLMENT` ×2, `RECHARGE` ×2, `PLAN_CHANGE` ×1). That is the evidence
that consent ordering is genuinely evaluated rather than merely declared.

## C. Aggregate results

### C.0 Round identity: directory identifier, seed and execution time

The 84-episode evidence set is exactly the six clean rounds below. Three
different concepts appear in their names and metadata and must not be conflated:

* **Output directory identifier** — the round directory name. The trailing
  `20261122` in the `rc10`/`rc11` names is a run label (it coincides with the
  execution date here but is not evidence of it); the `20261127` and `20261128`
  tokens are **random seeds**, not dates.
* **Random seed** — `random_seed` (and `sampling.seed`) in every manifest;
  `seed_status` is `applied` in all 84 manifests.
* **Execution timestamp** — `started_at` / `completed_at` in every manifest.

All six rounds actually executed on **2026-11-22**, between 10:54 and 11:29 UTC
(18:54–19:29 Asia/Hong_Kong). No manifest under `outputs/` has a `started_at`
later than 2026-11-22, so no directory suffix is evidence of a later run.

| Output directory identifier | Seed | Episodes | Successes | Execution window (UTC) | Execution window (HKT) |
|---|---:|---:|---:|---|---|
| `final-rc10-system-v4-evaluator-v2.4-20261122` | 20261122 | 14 | 14 | 2026-11-22 10:54:22–10:56:56 | 2026-11-22 18:54:22–18:56:56 |
| `stability-rc10b-20261127-system-v4-evaluator-v2.4-20261122` | 20261127 | 14 | 14 | 2026-11-22 11:13:21–11:17:13 | 2026-11-22 19:13:21–19:17:13 |
| `stability-rc10b-20261128-system-v4-evaluator-v2.4-20261122` | 20261128 | 14 | 14 | 2026-11-22 11:17:14–11:21:51 | 2026-11-22 19:17:14–19:21:51 |
| `final-rc11-system-v4-evaluator-v2.4-20261122` | 20261122 | 14 | 14 | 2026-11-22 11:20:31–11:23:30 | 2026-11-22 19:20:31–19:23:30 |
| `stability-rc11-seed20261127-system-v4-evaluator-v2.4` | 20261127 | 14 | 14 | 2026-11-22 11:23:52–11:26:22 | 2026-11-22 19:23:52–19:26:22 |
| `stability-rc11-seed20261128-system-v4-evaluator-v2.4` | 20261128 | 14 | 14 | 2026-11-22 11:26:26–11:29:09 | 2026-11-22 19:26:26–19:29:09 |

Every episode in these rounds used evaluator `2.4`, prompt `system-v4`, agent
type `llm` and the 14 frozen task versions in section A. The validation-matrix
provenance paragraph records the same distinction for the matrix round.

#### C.0.1 The 80-second overlap between `stability-rc10b-20261128` and `final-rc11`

The round windows of `stability-rc10b-20261128-…` (ends 11:21:51 UTC) and
`final-rc11-…` (starts 11:20:31 UTC) overlap. The overlap is **real, not a
round-level artefact**: two independent runs were executing concurrently. The
conclusion below rests on process records and episode-level intervals, not on the
round windows alone.

* **Two distinct processes, two distinct jobs.** Each round was launched by its
  own background job owned by a different agent session:
  `pwsh-e1794f153b004664` (owner `session-59068dc8…`, `retry_stability.sh`,
  registered 11:13:19.215 UTC, settled 11:21:51.759 UTC) and
  `pwsh-3ba93c894fc74ceb` (owner `session-07f9e5c1…`, `run_round.sh` for
  `final-rc11…`, registered 11:20:30.760 UTC, settled 11:23:30.682 UTC). Their
  lifetimes overlap for 81.0 s; their episode intervals overlap for 79.7 s
  (11:20:31.929–11:21:51.607). Each job owns a separate `bash` process, and every
  domain inside a round is a separate `python -m cm_outbound_bench.cli benchmark
  run` process, so the two rounds never shared a benchmark process.
* **The episodes really interleave.** Sweeping all 84 episode intervals gives 12
  contiguous intervals with **exactly 2 episodes in flight** — never more — from
  11:20:31.929 to 11:21:51.607. Six `stability-rc10b-20261128` episodes (D04×2,
  D05×2, D06×2) and seven `final-rc11` episodes (D01×2, D02×4, D03×1) were in
  flight at some point during the overlap. **No two episodes of the same task
  overlapped** (0 same-task pairs across the whole 84-episode set), so no task was
  ever executed twice simultaneously.
* **Independent episode state.** Every episode receives its own deep copy of a
  validated, declarative initial state (`InitialState.instantiate()` →
  `db.deep_copy()`, `benchmark/state.py`; passed as `initial_db` in
  `benchmark/execution.py`). Results are written to a per-episode directory keyed
  by a fresh UUID (`output_dir / "RUN-<uuid4-hex16>"`), so manifests and traces
  cannot collide across concurrent rounds. There is **no writable runtime
  database, no lock file and no shared mutable state file** in the benchmark
  runtime; the corpus SQLite database is read-only and is not opened by
  `benchmark run`. The only shared resources are read-only inputs: the catalogue
  JSON, the prompt/policy text, `providers.toml`, the credential environment
  variable and the interpreter/`site-packages`.
* **Concurrent provider requests.** Each turn is an independent, stateless
  OpenAI-compatible request carrying its own conversation and recorded seed, so
  concurrency cannot mix transcripts, tool results or consent records. It can
  contend for endpoint capacity and rate limits, and therefore affect wall-clock
  latency. The overlap shows no latency signature of its own: both rounds
  finished 14/14 with zero runtime failures and every manifest recording
  `error: null`, and `stability-rc10b-20261128` was already slow *before* the
  overlap (D02 episodes 31 093–35 225 ms at 11:17:37–11:19:24, D03 arrears
  33 877 ms at 11:19:41–11:20:15), while the six episodes that ran inside the
  overlap took 11 537–24 094 ms — inside that round's own range.
* **Multiple, not single, sources of host contention.** During part of the window
  a second agent session also ran read-only documentation checks and offline
  gates (pytest, Ruff, `compileall`) on the same host. Those write no benchmark
  evidence, but they do share CPU with the two rounds.

**Interpretation of validation stability.** Both rounds remain valid, complete
and independent evidence: each is a full 14-task round under the identical frozen
configuration, both are 14/14 with zero evaluation failures, zero incomplete runs
and zero policy violations, and both manifests and traces are immutable and
hash-verified (section D.3). The overlap does not change any verdict, because
verdicts are computed from each episode's own state, tool trace and conversation.
What the overlap does affect is **latency as a controlled measurement**: episodes
in the overlapping window shared host CPU and provider capacity, so their
wall-clock latency is descriptive only and must not be compared across rounds as
if it were measured under isolation. The stability claim in C.5 is a pass/fail
observation over six independent rounds, not a timing benchmark, and it is
unchanged. No live run was repeated for this freeze and no historical manifest or
trace was edited.

### C.1 Final release-candidate round

Round `final-rc10-system-v4-evaluator-v2.4-20261122` (output-directory
identifier), seed `20261122`; executed 2026-11-22 10:54:22–10:56:56 UTC.

| Metric | Value |
|---|---|
| Tasks | 14 |
| Successful tasks | 14 |
| Success rate | 1.0000 |
| Episodes | 14 |
| Runtime failures | 0 |
| Evaluation failures | 0 |
| Incomplete runs | 0 |
| Policy violations | 0 (rate 0.0000) |
| Mean latency | 10 639.4 ms |
| Mean tool calls | 3.50 |
| Mean turns | 7.79 |
| Total tokens | 416 859 (prompt 409 014 + completion 7 845) |

| Domain | Tasks | Runs | Successes | Rate | Mean latency (ms) | Mean tools | Mean turns | Violations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| D01 Product & Package | 2 | 2 | 2 | 1.000 | 11 368.5 | 3.00 | 7.00 | 0 |
| D02 Promotion & Offer | 4 | 4 | 4 | 1.000 | 8 852.8 | 3.00 | 6.25 | 0 |
| D03 Billing & Recharge | 2 | 2 | 2 | 1.000 | 11 161.5 | 3.00 | 10.00 | 0 |
| D04 Lifecycle | 2 | 2 | 2 | 1.000 | 11 597.0 | 4.00 | 8.00 | 0 |
| D05 Identity & Ownership | 2 | 2 | 2 | 1.000 | 11 098.0 | 3.00 | 10.00 | 0 |
| D06 Notification & Fulfillment | 2 | 2 | 2 | 1.000 | 11 545.0 | 5.50 | 7.00 | 0 |

### C.2 Stability repetitions

Two additional independent seeds were run after the final round, each into a new
output directory. Each task therefore has **three** episodes under the exact
final configuration (same task versions/hashes, prompt, policy and evaluator).

| Round | Seed | Episodes | Successes | Evaluation failures | Runtime failures | Incomplete | Mean latency (ms) | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `final-rc10-…` | 20261122 | 14 | 14 | 0 | 0 | 0 | 10 639.4 | 416 859 |
| `stability-rc10b-20261127-…` | 20261127 | 14 | 14 | 0 | 0 | 0 | 16 161.4 | 411 753 |
| `stability-rc10b-20261128-…` | 20261128 | 14 | 14 | 0 | 0 | 0 | 19 390.4 | 404 842 |
| **Total** | | **42** | **42** | **0** | **0** | **0** | | **1 233 454** |

| Task | Runs | Successes | Rate | Mean latency (ms) | Mean tools | Mean turns | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| `d01_plan_content_explanation` | 3 | 3 | 1.000 | 9 754.3 | 3.00 | 8.00 | 71 264 |
| `d01_plan_comparison` | 3 | 3 | 1.000 | 10 209.3 | 2.33 | 6.00 | 61 140 |
| `d02_offer_benefit_inquiry_accept` | 3 | 3 | 1.000 | 18 800.0 | 5.00 | 7.00 | 110 654 |
| `d02_hesitation_then_decline` | 3 | 3 | 1.000 | 18 523.7 | 3.00 | 9.00 | 96 715 |
| `d02_eligibility_ineligible_explanation` | 3 | 3 | 1.000 | 17 526.0 | 3.00 | 6.00 | 70 901 |
| `d02_direct_refusal_do_not_call` | 3 | 3 | 1.000 | 5 229.3 | 1.00 | 3.00 | 34 233 |
| `d03_bill_explanation` | 3 | 3 | 1.000 | 19 535.0 | 3.00 | 12.33 | 102 099 |
| `d03_arrears_recharge_with_consent` | 3 | 3 | 1.000 | 16 721.7 | 3.00 | 7.00 | 79 827 |
| `d04_plan_upgrade_with_consent` | 3 | 3 | 1.000 | 19 666.7 | 5.00 | 9.00 | 123 624 |
| `d04_plan_change_blocked_by_commitment` | 3 | 3 | 1.000 | 13 268.0 | 3.00 | 6.00 | 69 570 |
| `d05_ownership_transfer_conditions` | 3 | 3 | 1.000 | 21 939.0 | 3.00 | 10.00 | 79 867 |
| `d05_real_name_not_verified` | 3 | 3 | 1.000 | 12 664.0 | 3.00 | 10.33 | 85 734 |
| `d06_enrollment_confirmation_notification` | 3 | 3 | 1.000 | 19 463.3 | 6.00 | 7.33 | 136 044 |
| `d06_recharge_confirmation_notification` | 3 | 3 | 1.000 | 12 258.3 | 5.00 | 7.00 | 111 782 |

All 42 manifests carry the final task hashes, evaluator 2.4 and its hash, the
`system-v4` prompt hashes and the six policy hashes; the audit in section D found
zero metadata or trace-integrity mismatches.

### C.3 External provider outage (reported, not hidden)

Two earlier stability attempts (seeds `20261123` and `20261125`) ran while the
provider endpoint was intermittently unreachable. The harness recorded them as
structured `runtime_failure` episodes (`agent_protocol_error:network_error`,
zero turns); it did not convert them into passes and nothing was deleted.

| Attempt | Episodes | Successes | Runtime failures | Evaluation failures |
|---|---:|---:|---:|---:|
| `stability-rc10-20261123-…` | 14 | 0 | 14 | 0 |
| `stability-rc10-20261125-…` | 14 | 8 | 6 | 0 |

The two replacement rounds (`20261127`, `20261128`) above were run after
connectivity returned, into brand-new output directories. The outage episodes are
counted in the runtime-failure total of the whole evidence tree, not in the task
success rate; they are not evidence of task behaviour.

Their manifests still contain `agent_protocol_error:network_error` plus the
`answer_fact_missing:*` / identity markers that follow from evaluating an episode
with zero turns, because the evaluation of the empty episode is recorded rather
than suppressed. They are protocol/runtime consequences, not agent policy
violations: no business tool was dispatched, no consent was recorded and the
initial and final state hashes are identical. The three successful rounds in
C.1/C.2 have a policy-violation count of exactly zero.

**Honest characterisation of stability:** 84/84 successful episodes over six
rounds and three seeds is a positive stability observation for this
model/provider pair. It is not a confidence interval, and six samples per task
cannot establish statistical stability.

### C.4 Additional independent rounds (same frozen configuration)

Three further full rounds were executed into brand-new output directories under
the identical frozen configuration (evaluator `2.4` / `a12c74ab…`, prompt
`system-v4`, the 14 task versions/hashes in section A, the six policy hashes):
a second release-candidate round at seed `20261122`, and one stability round at
each of seeds `20261127` and `20261128`. Their directory identifiers, seeds and
manifest execution windows are listed in section C.0. Each was audited
independently: task version, task hash, prompt hash, policy hash and evaluator
version/hash match the live catalogue in every manifest, and every
`trace_sha256` equals the SHA-256 of the trace file on disk.

| Round | Seed | Episodes | Successes | Runtime failures | Evaluation failures | Incomplete | Mean latency (ms) | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `final-rc11-system-v4-evaluator-v2.4-20261122` | 20261122 | 14 | 14 | 0 | 0 | 0 | 12 372.4 | 412 869 |
| `stability-rc11-seed20261127-system-v4-evaluator-v2.4` | 20261127 | 14 | 14 | 0 | 0 | 0 | 10 322.6 | 406 912 |
| `stability-rc11-seed20261128-system-v4-evaluator-v2.4` | 20261128 | 14 | 14 | 0 | 0 | 0 | 11 276.8 | 420 108 |
| **Total (C.4)** | | **42** | **42** | **0** | **0** | **0** | | **1 239 889** |

### C.5 Consolidated evidence

| Metric | Value |
|---|---|
| Clean rounds under the frozen configuration | **6** |
| Episodes | **84** |
| Successful episodes | **84** |
| Success rate | **1.0000** |
| Runtime failures | **0** |
| Evaluation failures | **0** |
| Incomplete runs | **0** |
| Policy violations | **0** (rate 0.0000) |
| Runs per task | **6** |
| Distinct seeds per task | **3** (`20261122`, `20261127`, `20261128`) |
| Mean latency | 13 360.5 ms |
| Mean tool calls | 3.48 |
| Mean turns | 7.73 |
| Total tokens | 2 473 343 |

Per-domain consolidations: D01 12/12, D02 24/24, D03 12/12, D04 12/12,
D05 12/12, D06 12/12 — 84/84 with zero policy violations.

Six episodes per task over three distinct seeds is still a **stability
observation, not a confidence interval**. It strengthens the earlier three-sample
result; it does not establish statistical stability.

## D. Engineering verification

### D.1 Offline gates

| Gate | Command | Result |
|---|---|---|
| Full test suite | `./.venv/bin/python -m pytest -q` | **1266 passed, 25 subtests** |
| Lint | `./.venv/bin/python -m ruff check src tests` | clean (`All checks passed!`) |
| Byte-compile | `./.venv/bin/python -m compileall -q src tests` | clean (no output, exit 0) |
| Catalogue | `./.venv/bin/python -m cm_outbound_bench.cli benchmark verify` | `Catalog OK: 14 tasks across 6 domains.` |

The suite includes the full offline regression groups: six domain regression
modules, all 14 task catalogue checks, simulator and tool tests, identity and
consent-ordering tests, Answer Fact matching/scope/post-conversation tests,
evaluator version/hash tests, historical-trace compatibility tests,
reproducibility tests, demo-agent end-to-end tests, and the provider layer.

The README's `uv` wrappers were additionally executed end-to-end in an isolated
copy of this tree with `uv 0.13.8` and the same CPython 3.12.16: `uv sync`,
`uv run cm-outbound benchmark verify`, `uv run pytest -q` (1266 passed, 25
subtests), `uv run ruff check src tests`, `uv run python -m compileall -q src
tests`, the documented `benchmark run --agent demo` shape (14 manifests written
to a temporary directory, exit 0), and `benchmark evaluate` / `replay` /
`aggregate` all exited 0.

**Lockfile correction (final packaging freeze).** The earlier audit recorded that
the committed `uv.lock` named project version `0.1.0` while `pyproject.toml`
declared `1.0.0`, so `uv lock --check` failed. That is now fixed: `uv lock`
regenerated in an isolated copy changed **exactly one line** — the project entry
`version = "0.1.0"` became `version = "1.0.0"`. The diff contains no other hunk.
The resolved set is unchanged: 13 packages before and after, the same 13 names,
the same registry sources, and byte-identical wheel/sdist hashes for the 12
dependency entries; only the project's own metadata moved, which is what
`pyproject.toml` already declared.
`pyproject.toml` was **not** modified to match the stale lock. With the
regenerated lock: `uv lock --check` exits 0, `uv sync` installs
`cm-outbound-bench==1.0.0` plus the same 12 pinned packages,
`uv run pytest -q` reports 1266 passed and 25 subtests, `uv run ruff check src
tests` is clean, `uv run python -m compileall -q src tests` is clean,
`uv run cm-outbound benchmark verify` prints `Catalog OK: 14 tasks across 6
domains.`, and `scripts/release_config.py` still emits a byte-identical
`docs/development/release_config.json`
(`17432454d8e436e9b57cf77c27380bc3b4d0854ace76c3ab7e1a115b0cb19dbd`).
The lockfile change was therefore accepted as minimal and safe.

Documentation and secret checks at the same revision: all 21 relative Markdown
links across the 14 Markdown files resolve (the 33 backticked paths that do not
resolve are entries in the cleanup changelog that intentionally name removed
files), and a secret scan over the 1,328 deliverable files found nine synthetic
test fixtures and no credentials.

The release-candidate repair module
`tests/test_benchmark_release_candidate_repairs.py` contributes 34 focused
tests: the full 14-task catalogue pin, the explicit D01 identity criterion, the
still-active protected-account and protected-disclosure gates (injected into a
preserved run), the repaired Simulator identity trigger (two recorded requests,
six non-requests), every preserved/appended Answer Fact pattern, the exact
recorded final answers, the still-failing wrong/negated statements, the evaluator
2.4 public-list vs account-frame rule, and byte integrity + labelled read-only
re-evaluation of the five preserved failure runs.

### D.2 Demo agent end-to-end

`benchmark run --agent demo --output-dir outputs/runs/final-demo-v2.4-20261122`
(no provider call, deterministic agent):

* 14 tasks, **14 success**, no runtime failures;
* no policy violations;
* evaluator 2.4;
* 14 distinct task hashes, all matching the frozen catalogue.

This is a scripted end-to-end check of the runtime and evaluator, not a
substitute for the live LLM evidence in section B.

### D.3 Historical artefact integrity

An audit of every persisted run under `outputs/` (read-only) recomputed the
inventory from the manifests and traces themselves. Historical and final
evidence are reported separately, then combined.

| Population | Manifests | Traces | `success` | `evaluation_failure` | `runtime_failure` | No recorded status |
|---|---:|---:|---:|---:|---:|---:|
| Historical (all other rounds) | 369 | 327 | 253 | 49 | 25 | 42 |
| Final six clean rounds (C.0) | 84 | 84 | 84 | 0 | 0 | 0 |
| **Combined tree** | **453** | **411** | **337** | **49** | **25** | **42** |

* **453** `RUN-*.json` manifests and **411** `trace.json` files were found.
  Every one of the 411 trace-backed manifests has a `trace_sha256` that matches
  the file on disk (recomputed SHA-256), and every trace is referenced by exactly
  one manifest: **411/411 verified, zero mismatches, zero missing, zero
  unreferenced, zero load errors**. All 411 traces were also loaded through
  `load_trace`, which re-verifies the trace schema version, the recorded
  `integrity.trace_hash` against the recomputed content hash, the final-state
  hash against the stored database, and the trace safety gate: **411/411
  passed**.
* The 42 "No recorded status" entries are the early demo manifests from the
  pre-trace era (all under `outputs/runs/`): they have no `trace_path`, no
  `run_status` and no `random_seed`, and their `started_at` values are all
  2026-11-20 03:40–03:47 UTC. They are historical and out of scope for trace
  verification; they were not modified.
* The historical `success` count is 253, not 337: 337 is the **combined**
  success count (253 historical + 84 final). The earlier edition of this report
  printed the combined number next to the historical manifest total; the table
  above replaces that mixed presentation.
* Historical failures are retained, never rewritten: `evaluation_failure` 49 and
  `runtime_failure` 25 are historical verdicts, and the five preserved failure
  runs are additionally pinned byte-for-byte (manifest and trace SHA-256) by the
  regression module.
* No historical run directory was deleted, renamed or rewritten. The 84 final
  episodes are a subset of the same tree and are byte-identical to the records
  audited in sections B and C.
* The 84 final manifests were re-checked field by field against
  `docs/development/release_config.json` (task version/hash, prompt
  version/hash, policy hash, evaluator version/hash, initial-tools hash,
  benchmark/manifest/trace schema versions): **84/84 match, zero mismatches**.
  Each of the 14 tasks has exactly six runs, and each of the three seeds is used
  28 times (two rounds per seed).

### D.4 Output-directory discipline

Every live round used a new directory that did not exist before the run, and the
runner script refuses to reuse an existing domain directory:

```
outputs/live_llm_validation/deepseek/
  final-rc10-system-v4-evaluator-v2.4-20261122/        (final, 14/14)
  stability-rc10b-20261127-system-v4-evaluator-v2.4-20261122/  (14/14)
  stability-rc10b-20261128-system-v4-evaluator-v2.4-20261122/  (14/14)
  final-rc11-system-v4-evaluator-v2.4-20261122/        (final, 14/14)
  stability-rc11-seed20261127-system-v4-evaluator-v2.4/        (14/14)
  stability-rc11-seed20261128-system-v4-evaluator-v2.4/        (14/14)
  stability-rc10-20261123-system-v4-evaluator-v2.4-20261122/   (outage, preserved)
  stability-rc10-20261125-system-v4-evaluator-v2.4-20261122/   (outage, preserved)
  final-rc9-system-v4-evaluator-v2.3-20261122/         (superseded, preserved)
  final-rc8-system-v4-evaluator-v2.3-20261122/         (superseded, preserved)
  … all earlier rounds, unchanged …
```

No `RUN-*` directory was overwritten: each episode creates a fresh `RUN-<id>`
folder, and the test suite pins the unique-output behaviour.

The trailing numeric token in a round directory name is a directory label or a
random seed, **not** an execution timestamp. Seed and execution time for each of
the six clean rounds are listed in section C.0; the validation matrix records
the same distinction in its provenance paragraph.

### D.5 Reproducibility checks

* `benchmark evaluate --trace …` re-runs the evaluator over a saved episode
  without a model call and reports whether the recorded verdict is reproduced.
  It **refuses** a trace whose recorded task version differs from the installed
  catalogue, because re-evaluating an older recording answers a different
  question than "is this result reproducible?".
* `benchmark evaluate --trace … --historical-diagnostic` is the explicit opt-in
  for that cross-version question. It relaxes **only** the task-version equality
  check, still enforces task/domain/scenario identity and the final-state hash,
  labels the result `historical_diagnostic: true` with both version labels, never
  writes to the trace, and is never counted as release evidence.
  `tests/test_benchmark_historical_reevaluation.py` (13 tests) covers default
  rejection, identity enforcement, tamper detection, strict replay, CLI text/JSON
  output and the invariant that the evaluator hash does not move.
* `benchmark replay --trace …` deterministically re-executes the recorded actions
  from the original initial state and compares actions, conversation, simulator
  events, tool trace, consent events, stop reason, final-state hash and
  evaluation.
* `tests/test_benchmark_reproducibility.py` and
  `tests/test_benchmark_replay.py` cover both paths for the catalogue.
* Re-evaluating the five preserved failures under the repaired specifications is
  a **labelled read-only diagnostic**; their recorded verdicts remain failures
  and their bytes are unchanged.

### D.6 Packaging note

`dist/` held a wheel and sdist built on 2026-11-20, **before** the
release-candidate repairs. They were stale build artefacts, were never part of
the validated evidence, and were removed in the final repository cleanup. Run
`uv build` before any distribution so the wheel carries the final task, state and
policy JSON. The validated runtime for this report is the source tree under
`.venv` at the frozen configuration in section A.

### D.7 Change set of the final packaging freeze

Three of the 1,328 deliverable files changed in this freeze, and nothing else
(verified by a SHA-256 snapshot of every file outside `.venv` taken before and
after: zero files added, zero removed, three modified, and no change anywhere
under `outputs/`):

| File | Change | Nature |
|---|---|---|
| `uv.lock` | `cm-outbound-bench` metadata `0.1.0` → `1.0.0` | Packaging metadata only; no resolved dependency or source changed |
| `README.md` | Wording and layout trimmed to **148 lines** (from 154); no installation, provider-configuration or Quick Start content removed | Documentation formatting |
| `docs/release_readiness_report.md` | This lockfile record, the C.0.1 overlap analysis and the limitation below | Documentation wording |

The preceding documentation audit had also touched one source file,
`scripts/validation_matrix.py` (its other changes were documentation only: nine
Markdown files reworded and `.env.example` added). That distinction must be
stated precisely, so it is made explicitly here: **benchmark runtime source code
and benchmark behaviour are unchanged, and a non-runtime reporting utility was
updated.** The matrix generator is a reporting-only script that no benchmark
module imports; it reads manifests and writes
`docs/reports/validation_matrix_final.{md,json}`. This report therefore does
**not** claim that no source file of any kind changed — only that no runtime
source, agent, simulator, runner, task specification or version, policy, prompt,
evaluator, manifest schema or trace schema changed.

Verification of that boundary: all 46 modules under `src/cm_outbound_bench/`
(agent, simulator, runner, evaluator, providers, domain model and toolkits) are
**byte-identical** across the whole delivery, as are the 51 test modules, the 22
catalogue data files and `docs/development/release_config.json`, which also
regenerates byte-identically. The evaluator hash (`a12c74ab…`), the six policy
hashes and the 14 task/prompt/initial-tools hashes are therefore unchanged. All
453 manifests and 411 traces are byte-identical to the pre-freeze snapshot, and
the final 84 manifests still pin those same values (section D.3).

---

## E. Known limitations

1. **No human expert validation.** Every task is `CANDIDATE` and
   `SYNTHETIC_BENCHMARK_ASSUMPTION`. Passing synthetic live runs does not promote
   a task to `HUMAN_REVIEWED` or `GOLD`.
2. **Answer assertions are literal pattern lists.** A fact passes only when an
   accepted literal appears as an affirmed statement (optionally grounded in a
   tool result). Repairs in this release added observed paraphrases, but a future
   model may still produce a correct statement outside the list. Such a case is a
   specification gap, not an agent failure; the repair path is documented in
   `docs/task_authoring.md` and was applied repeatedly in this release cycle (see
   `docs/CHANGELOG.md`).
3. **The Simulator is scripted.** Its Chinese trigger detectors are literal
   heuristics. The identity-request detector was extended for the phrasing
   observed in this release; declarations, negations, historical statements and
   non-request mentions remain excluded by tests. Detection in English is
   supported for the identity, eligibility and consent questions, but a
   bilingual paraphrase outside the vocabulary still falls back to the scripted
   fallback turn.
4. **Statistical strength.** Three independent seeds and six episodes per task
   (84 episodes in the final configuration) support a stability observation, not a
   confidence interval. Results are reported per task and never merged across
   evaluator or task versions.
5. **Single provider/model.** Live validation used DeepSeek `deepseek-flash`
   through the OpenAI-compatible adapter with `temperature=0.2`, thinking
   disabled and a recorded seed. No cross-provider or cross-model claim is made.
6. **Evaluator hash coverage.** `evaluator_hash()` content-hashes
   `evaluator.py` + `benchmark/checks.py` (the evaluation contract). Supporting
   deterministic modules (`benchmark/answer_facts.py`, `benchmark/simulator.py`)
   are covered by Task versions/scripts and by the test suite, but are not
   separately hash-pinned in manifests. This is a known auditability limitation,
   unchanged by this release.
7. **Synthetic business rules.** Tariffs, offers, eligibility rules, billing
   amounts and notification semantics are synthetic benchmark assumptions, not
   authoritative China Mobile policy. The corpus evidence references document
   where a pattern was observed, not that the rule is official.
8. **No real side effects.** The benchmark cannot place calls, send SMS, charge
   money or provision service. “Enrollment”, “recharge” and “notification” are
   simulated database operations.
9. **D01 comparison identity criterion.** `d01_plan_comparison` v1.3 explicitly
   does not require identity verification because it is a public catalogue
   comparison. Any actual account access or protected disclosure before
   verification still fails (`protected_account_access`, `protected_disclosure`,
   and the tool-side session gate are all active and tested).
10. **Historical rounds mixed.** Runs under evaluator 2.2/2.3 or earlier task
    versions are retained as historical evidence only. They are never counted as
    evidence for the final configuration.
11. **Aborted attempt retained.** The first `stability-rc9` attempt was aborted by
    a transient import error introduced while the evaluator was being edited; it
    produced no manifests and is excluded from every metric. Nothing was deleted.
12. **`NOT_APPLICABLE` is conditional, not cosmetic.** Several of the 83 distinct
    applicable checks only apply when their precondition occurs — for example
    `unauthorized_mutation_attempts` applies only when the Tool *blocked* a
    consent-gated mutation, `consent_before_mutation` only when such a mutation
    executed, and `sms_after_enrollment` only when an enrollment occurred. Each
    becomes applicable, and fails, if the prohibited behaviour occurs, and the
    state-based `expected_actions`/`forbidden_actions` stay load-bearing
    regardless. Section B4 lists every conditional check with its observed
    frequency so coverage can be audited rather than assumed.
13. **`d05_transfer_conditions` is never exercised by a mutation.** The catalogue
    contains no ownership-transfer tool, and `d05_ownership_transfer_conditions`
    explicitly expects **zero** `OWNERSHIP_TRANSFER` action consents. That domain
    check is therefore `NOT_APPLICABLE` in every current episode. The scenario is
    validated by read-only tool retrieval, the zero-consent state expectation and
    the answer assertions; the check stays armed if a transfer consent ever
    appears while a blocking condition holds.
14. **Task hash couples to the evaluator version.** `evaluator_version` is part of
    a task's canonical payload, so promoting the evaluator changes every task hash
    without changing task semantics. This is the pre-existing, documented
    convention; it is retained rather than silently redefined, because redefining
    it would invalidate every historical task hash.
15. **Provider availability was intermittent.** Two evaluator-2.4 stability
    attempts (seeds `20261123`, `20261125`) were hit by provider network failures
    and are preserved but excluded (section C.3). The six clean rounds in
    C.1/C.2/C.4 were unaffected.
16. **Two clean rounds overlapped in time.** `stability-rc10b-20261128` and
    `final-rc11` ran as two concurrent processes for about 80 s (11:20:31–11:21:51
    UTC), from two different agent sessions, so they shared host CPU and provider
    capacity. Episode state, output directories and provider requests stayed
    independent, no two episodes of the same task ran at once, and both rounds
    completed 14/14 with zero failures; the pass/fail evidence is unaffected. The
    residual limitation is that **latency for the 13 episodes in the overlapping
    window is not an isolated measurement** and should not be compared across
    rounds. See section C.0.1. No run was repeated and no record was edited.

---

## F. Handoff documentation

The repository already provides the requested handoff material; this release
updates its version pins and adds the final evidence.

| Question | Where |
|---|---|
| What is this benchmark? Architecture and boundaries | `README.md`, `docs/architecture.md` |
| Install / environment setup | `README.md` §Installation, `pyproject.toml`, `uv.lock` |
| Provider configuration without secrets | `providers.example.toml`, `.env.example`, `README.md` §Provider Configuration |
| Verify the catalogue | `uv run cm-outbound benchmark verify` |
| Run one domain | `uv run cm-outbound benchmark run --domain D02 --agent llm ...` |
| Run all domains | `uv run cm-outbound benchmark run --agent llm ...` |
| Read a trace / manifest | `README.md` §Outputs and Results, `docs/benchmark_execution.md` |
| Version and hash conventions | `docs/benchmark_execution.md` §4, `docs/task_authoring.md` |
| Add a task or a domain | `docs/task_authoring.md`, `docs/scenario_catalog.md` |
| Failure triage and troubleshooting | `docs/benchmark_execution.md` |
| Full task catalogue and evidence refs | `docs/scenario_catalog.md` |
| Data pipeline and privacy boundary | `data_pipeline/README.md`, `data_pipeline/PIPELINE.md` |
| Frozen configuration and hashes | `docs/development/release_config.json` |

### Reproduction commands

```bash
cd cm-outbound-bench
PY=./.venv/bin/python

# 1. Offline gates
$PY -m pytest -q
$PY -m ruff check src tests
$PY -m compileall -q src tests
$PY -m cm_outbound_bench.cli benchmark verify

# 2. Demo (scripted) agent, all 14 tasks, no provider call
$PY -m cm_outbound_bench.cli benchmark run --agent demo \
    --output-dir outputs/runs/final-demo

# 3. Live provider runs (sequential, unique output directories)
export DEEPSEEK_API_KEY=...          # credential from the provider config's api_key_env
$PY -m cm_outbound_bench.cli benchmark run \
    --domain D01 --agent llm --provider deepseek \
    --provider-config ./providers.toml \
    --output-dir outputs/live_llm_validation/deepseek/<unique-dir>/D01 \
    --seed 20261122 --json
```

`<unique-dir>` must not exist before the run. The harness refuses to reuse an
existing domain directory in this project's scripts, and every CLI invocation
writes a new `RUN-<id>` directory inside the output directory, so no existing
episode can be overwritten.
