# GitHub Dataset Upload Manifest

Generated from the actual local repository state: every entry was measured on
disk (size, record count, SHA-256). No file was edited to fit this manifest and
no record was removed to meet a size limit.

## 1. Summary

| Metric | Value |
|---|---:|
| Decision dataset files (`data/decision/`, `outputs/decision/`) | 3,641 |
| Decision dataset bytes | 94,530,096 |
| Legacy synthetic data files | 22 |
| Whole proposed upload | 5,071 files / 113,258,312 bytes |

## 2. Access classification of the decision datasets

| Class | Meaning | Files |
|---|---|---:|
| Class 1 — approved processed research data | Agent-visible Tasks, reconstructed conversations, split artifacts, sample plans, metadata, aggregate reports | 27 |
| Class 2 — restricted evaluator data | Private references, frozen Gold Labels, annotation passes/adjudications, blind scoring records, traces, channel-review package | 3,614 |

Note on the frozen Gold Labels: the task brief lists "Frozen LLM-Gold
references" as a Class 1 example and "expected decision labels" as a Class 2
example. Because the frozen files contain the expected decisions, this manifest
assigns them the more conservative **Class 2** and keeps them physically
separated from `data/decision/tasks/tasks.jsonl`.

Class 2 material is never loaded by the Agent Task loader; see
`src/cm_outbound_bench/decisions/store.py` (`agent_visible_payload`,
`FORBIDDEN_PUBLIC_KEYS`) and `tests/test_decision_isolation.py` /
`tests/test_decisions_isolation_gold.py`.

## 3. Primary dataset artifacts (per-file)

| Path | Category | Split | Bytes | Format | Records | SHA-256 (16) | De-identified | Conversation text | Hidden future turns | Private GT | Sensitive IDs | Class | Eligible |
|---|---|---|---:|---|---:|---|---|---|---|---|---|---|---|
| `data/decision/calls/calls.jsonl` | processed conversations | all | 43,945,354 | jsonl | 4527 | `50f73ef5d416c255…` | yes | yes | no | no | none found | Class 1 | yes |
| `data/decision/calls/calls.manifest.json` | conversation manifest | all | 623 | json | object | `74d5746fb7a1358a…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/tasks/tasks.jsonl` | agent-visible tasks | all | 13,658,681 | jsonl | 10116 | `e0d40ef6825904a5…` | yes | yes | no | no | none found | Class 1 | yes |
| `data/decision/tasks/references.jsonl` | private references | all | 8,157,082 | jsonl | 10116 | `4c2690e04083bf21…` | yes | yes | yes (private) | no | none found | Class 2 | yes |
| `data/decision/tasks/tasks.manifest.json` | task catalogue manifest | all | 1,645 | json | 10116 | `042f49b1002fe74f…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/splits/split_manifest.json` | split manifest | all | 510,988 | json | object | `c74e00ed9f9d8c36…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/business_sources/inventory.json` | business source inventory | all | 17,724 | json | object | `8137139b7f7b22c3…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/samples/dev_baseline_evaluation_sample.json` | sample plan | dev | 206,718 | json | object | `cd804018b050c53e…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/samples/train_r_annotation_pilot.json` | sample plan | train_r | 54,466 | json | object | `89c6ae136fce9a45…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/samples/train_r_feedback_sample.json` | sample plan | train_r | 160,898 | json | object | `a7f9f4fec680880f…` | yes | no | no | no | none found | Class 1 | yes |
| `data/decision/annotations/dev_baseline_evaluation_sample/annotation_packets.jsonl` | annotation packets | dev | 1,225,992 | jsonl | 400 | `27fa6991e8c8275f…` | yes | yes | no | no | none found | Class 2 | yes |
| `data/decision/annotations/train_r_annotation_pilot/annotation_packets.jsonl` | annotation packets | train_r | 309,427 | jsonl | 100 | `cd2c8255a18342b2…` | yes | yes | no | no | none found | Class 2 | yes |
| `data/decision/annotations/train_r_feedback_sample/annotation_packets.jsonl` | annotation packets | train_r | 934,836 | jsonl | 300 | `b84bb1511423ed90…` | yes | yes | no | no | none found | Class 2 | yes |
| `data/decision/annotations/output_review/agent_output_review_packets.jsonl` | output review packets | dev | 1,011,289 | jsonl | 400 | `beaa0819ed2e0d37…` | yes | yes | no | no | none found | Class 2 | yes |
| `outputs/decision/annotations/train_r/final_references.jsonl` | FROZEN GROUND TRUTH | train_r | 2,786,000 | jsonl | 400 | `03d2e23bed2e8d77…` | yes | yes | yes (private) | yes | none found | Class 2 | yes |
| `outputs/decision/annotations/dev/final_references.jsonl` | FROZEN GROUND TRUTH | dev | 2,745,745 | jsonl | 400 | `a7d12662af0745b4…` | yes | yes | yes (private) | yes | none found | Class 2 | yes |
| `outputs/decision/annotations/manifests/frozen_annotation_manifest.json` | FROZEN GT manifest | train_r+dev | 903 | json | object | `14a61f7dbba07797…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/annotations/manifests/annotation_run_manifest.json` | annotation run manifest | all | 6,423 | json | object | `20beccac33095e81…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/annotations/manifests/annotation_overlap_report.json` | overlap report | all | 1,567 | json | object | `5b0534f23efecf4b…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/annotations/dev/main/run_manifest.json` | annotation run manifest | dev | 1,945 | json | object | `8a1a4fe98f59b249…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/annotations/train_r/feedback/run_manifest.json` | annotation run manifest | train_r | 1,847 | json | object | `c4f9d72598612f1e…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/annotations/train_r/pilot/pilot_analysis.json` | pilot analysis | train_r | 2,815 | json | object | `44691e90b4a1c744…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/evaluation/group_a/adjudicated_results.jsonl` | Group A scoring | dev | 288,127 | jsonl | 400 | `70760076dd11e3a8…` | yes | yes | yes (private) | no | none found | Class 2 | yes |
| `outputs/decision/evaluation/group_a/scorer_a.jsonl` | Group A scoring pass A | dev | 775,821 | jsonl | 400 | `6911442dfa17df5c…` | yes | yes | no | no | none found | Class 2 | yes |
| `outputs/decision/evaluation/group_a/scorer_b.jsonl` | Group A scoring pass B | dev | 696,987 | jsonl | 400 | `711274b156818423…` | yes | yes | no | no | none found | Class 2 | yes |
| `outputs/decision/traces/20261125T164504Z-group-a-dev-eval/run_set_manifest.json` | Group A run manifest | dev | 122,391 | json | object | `6ddcbe7dbbcccabe…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/traces/20261125T164504Z-group-a-dev-eval/evaluation.json` | Group A evaluation | dev | 3,159 | json | object | `5a353aa2c53ee449…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/channel_review/channel_review_summary.json` | channel review summary | all | 21,930 | json | object | `c0daf7c60f7a7a47…` | yes | no | no | no | none found | Class 2 | yes |
| `outputs/decision/channel_review/channel_3_samples.jsonl` | channel 3 samples | all | 24,610 | jsonl | 12 | `c935584eb5103e50…` | yes | yes | no | no | none found | Class 2 | yes |
| `outputs/decision/channel_review/channel_4_samples.jsonl` | channel 4 samples | all | 25,455 | jsonl | 12 | `6d0947981faee446…` | yes | yes | no | no | none found | Class 2 | yes |
| `outputs/decision/channel_review/channel_5_samples.jsonl` | channel 5 samples | all | 3,909 | jsonl | 2 | `23e15963b4c982fc…` | yes | yes | no | no | none found | Class 2 | yes |
| `outputs/decision/reports/group_a_llm_gold_baseline_report.json` | Group A aggregate | dev | 3,840 | json | object | `5a7dbec71fbb4249…` | yes | no | no | no | none found | Class 1 | yes |
| `outputs/decision/reports/group_a_llm_gold_baseline_report.md` | Group A aggregate | dev | 2,683 | md | - | `ad443413faa775d7…` | yes | no | no | no | none found | Class 1 | yes |
| `outputs/decision/reports/group_a_business_baseline_report.json` | Group A aggregate | dev | 9,388 | json | object | `d0bb08aa82f2be01…` | yes | no | no | no | none found | Class 1 | yes |
| `outputs/decision/reports/group_a_business_baseline_report.md` | Group A aggregate | dev | 6,409 | md | - | `7ccc55df27032e8f…` | yes | no | no | no | none found | Class 1 | yes |
| `outputs/decision/reports/group_a_error_analysis_report.md` | error analysis | dev | 31,129 | md | - | `0b3d124cc910f6b3…` | yes | no | no | no | none found | Class 1 | yes |
| `outputs/decision/reports/llm_gold_quality_report.md` | gold quality report | train_r+dev | 3,597 | md | - | `35696576f0bf1b7c…` | yes | no | no | no | none found | Class 1 | yes |
| `outputs/decision/reports/conversation_identity_audit.md` | identity audit | all | 1,391 | md | - | `e6374ccf732eac37…` | yes | no | no | no | none found | Class 1 | yes |

Full 64-hex SHA-256 values for the frozen artifacts are in §5.

## 4. Bulk per-record sets (rollup)

Several artifact families consist of many small files. Each family is pinned as
a whole by a rollup digest: `sha256` over the sorted lines
`"<file-sha256>  <path>\n"`, so a single-file change alters the rollup.

| Set | Files | Bytes | Rollup SHA-256 | Class |
|---|---:|---:|---|---|
| `outputs/decision/annotations/dev/main/records/pass_a` | 400 | 1,984,466 | `6aeaef4246fd722976e14e342163130f7bd7bed361f5683e268162b367b4cfbc` | Class 2 |
| `outputs/decision/annotations/dev/main/records/pass_b` | 400 | 1,937,049 | `be14189bdb47037e09c9da3b2ed45f6e2d54cd6cb1394ef42a026bd3330d8694` | Class 2 |
| `outputs/decision/annotations/dev/main/records/adjudications` | 368 | 508,619 | `76387d193f3ffe588f144c33caa09bbfc08d0d4c0ad62628b0aae3331d291f3c` | Class 2 |
| `outputs/decision/annotations/train_r/feedback/records/pass_a` | 300 | 1,488,411 | `8efae303d443e01eea169e02f3121fb157691782c57c109dfbc30631c8e2c89e` | Class 2 |
| `outputs/decision/annotations/train_r/feedback/records/pass_b` | 300 | 1,478,592 | `0724f7b7a91c68285a42f35b82ba5a7d99dd992af1464dc27a34b901e335e58c` | Class 2 |
| `outputs/decision/annotations/train_r/feedback/records/adjudications` | 283 | 384,524 | `bc0f2290e552cfb980af16d464b349256c4c675515964fdebaeaf32ad2ca410c` | Class 2 |
| `outputs/decision/annotations/train_r/pilot/records/pass_a` | 100 | 501,949 | `257ec80852c3af7822b68c44a7ddb2ffa69acc4dfd7bcd8fb2b41d1a74212dd1` | Class 2 |
| `outputs/decision/annotations/train_r/pilot/records/pass_b` | 100 | 489,619 | `a06fdcbff586196cce96fca5078c5f48692db0b159e64a0a6debfc0d76be11c6` | Class 2 |
| `outputs/decision/annotations/train_r/pilot/records/adjudications` | 97 | 130,199 | `e25d01daf8a2616d32fcf0b9e24d5ae1afdac123b6b339a24f8d601d47c4466f` | Class 2 |
| `outputs/decision/evaluation/group_a/records/pass_a` | 400 | 801,048 | `84fdf20ce176c9a958f46bbb0a04d71dbbf38bfc5ec8d48cf575bbc34dd7b8cb` | Class 2 |
| `outputs/decision/evaluation/group_a/records/pass_b` | 400 | 722,215 | `9728be8fcbd2a67bf526b1767740f3f53fec8961ec8d21b83c2fe52c0f232047` | Class 2 |
| `outputs/decision/traces/20261125T164504Z-group-a-dev-eval` | 402 | 1,276,421 | `9958b1ec1e41cb3128e577cf3b3367312a54305c2129f5fa3fb38ef5d72ba008` | Class 2 |
| `outputs/live_llm_validation` | 1040 | 13,361,959 | `f6a0ff1f2317adbb67321d1d8e1a36cedc2435cdf24bbc7cf46b9c72301a32c2` | Class 1 |
| `outputs/live_llm_full` | 28 | 348,164 | `037cdbc510d959b1f4f609aa5200a622e6e1a01ed6a15a8dc92d6cf95f85d1cd` | Class 1 |
| `outputs/live_llm_smoke` | 12 | 171,580 | `14610029f2824f2aa3921da91c175322481bb7b3f5b9f405fe30629e000c9e15` | Class 1 |
| `outputs/runs` | 100 | 976,968 | `20b88b4695f1949af5e08b3d972688f574b30508be09a6a8f2b84e1f31fc406f` | Class 1 |

## 5. Frozen Ground Truth integrity (must not change)

| Artifact | Records | Bytes | SHA-256 | Expected | Match |
|---|---:|---:|---|---|---|
| `outputs/decision/annotations/train_r/final_references.jsonl` | 400 | 2,786,000 | `03d2e23bed2e8d771284d3b2a79b88d1f792e5d6fbecff642360f0112c4c4bdd` | `03d2e23bed2e8d77…` | **YES** |
| `outputs/decision/annotations/dev/final_references.jsonl` | 400 | 2,745,745 | `a7d12662af0745b4800161a1b93bbd0ada988d7a0bc671e3710c482446ba44b6` | `a7d12662af0745b4…` | **YES** |

Ground Truth version `cm-outbound-llm-gold-v1`; source `deepseek_v4_pro`;
model `deepseek-v4-pro`; `policy_verified = false` on all 800 references;
scored/unscorable — Train-R 393/7, Dev 391/9; `test_annotated = false`.

## 6. Dataset version and split assignment (unchanged)

| Field | Value |
|---|---|
| Dataset version | `cm_outbound_v2` |
| Task schema version | `1.0` |
| Private reference schema | `1.0` |
| Split seed | `20261125` |
| Split algorithm | `grouped_greedy_relative_deficit_v1` |
| Groups | 4,506 (4,494 singleton + 12 near-duplicate groups over 30 calls) |
| Train-F | 26.67% target — 1,207 calls / 2,726 Tasks |
| Train-R | 26.67% target — 1,206 calls / 2,695 Tasks |
| Dev | 13.33% target — 603 calls / 1,314 Tasks |
| Test | 33.33% target — 1,508 calls / 3,381 Tasks |

## 7. De-identification status of the included data

| Check | Result |
|---|---|
| Content-field scan for CN mobile / ID-card / landline / bank-card / e-mail / credential-URL / API-key shapes | **0 findings** in 421,158 content values |
| Masking placeholders | `<PHONE>` 4,734 · `<ACCOUNT>` 315 · `<ADDRESS>` 121 · `<NAME>` 118 · `<ASR_TOKEN>` 921 · `<ID_NO>` 18 · `<CARD_NO>` 6 · `<PASSWORD>` 1 |
| Surviving surname + honorific personal names | 0 (226 honorific occurrences are role/politeness forms) |
| Apparent raw-text matches | all inside opaque identifiers (`CALL_<hex>`, `TASK_<hex>`, `RUN-<hex>`, `DUPGROUP_<hex>`, `*_sha256`, `cache_key`) |
| Residual risk | names without honorifics, addresses without markers, context narrowing — human sampling required before wider distribution |

## 8. Upload eligibility

| Decision | Paths |
|---|---|
| **Eligible — Class 1** | `data/decision/tasks/tasks.jsonl`, `data/decision/calls/`, `data/decision/splits/`, `data/decision/samples/`, `data/decision/business_sources/`, `outputs/decision/reports/`, legacy `data/tasks/`, `data/policy/`, `data/synthetic/` |
| **Eligible — Class 2, keep separated** | `data/decision/tasks/references.jsonl`, `data/decision/annotations/`, `outputs/decision/annotations/`, `outputs/decision/evaluation/`, `outputs/decision/traces/`, `outputs/decision/channel_review/` |
| **Requires owner confirmation** | every path above under `data/decision/` and `outputs/decision/` — see `github_release_readiness.md` §16 |
| **Excluded** | raw `data/TXT/`, `data/deidentified/`, `data/corpus_db/`, raw audio, `.env*`, `providers.toml`, keys, virtual environments, caches |

