# Data Privacy and Provenance Limitations

This document states exactly what the released project contains, what it must
never contain, and which provenance claims it can and cannot support.

---

## 1. Data classes

| Data | Classification | Location | Shipped in wheel | Included in this private repository |
|---|---|---|---|---|
| Raw TXT transcripts | Confidential source | `data/TXT/` (outside this repository) | No | **No** |
| De-identified corpus (`cm_outbound_v2`) | Confidential even after masking | `data/deidentified/` (outside this repository) | No | **No** |
| Corpus SQLite database | Confidential derived artefact | `data/corpus_db/` (outside this repository) | No | **No** |
| Transcript-id HMAC key (one-way ids; no mapping table) | Secret | not present in this project | No | **No** |
| Synthetic runtime database | Synthetic benchmark data | `data/synthetic/` | Yes | Yes |
| Benchmark tasks, policies, states | Synthetic benchmark data | `data/tasks/`, `data/policy/`, `data/synthetic/states/` | Yes | Yes |
| Corpus aggregate reports | Non-sensitive aggregates | `outputs/corpus/` (generated locally) | No | No |
| Run manifests (legacy synthetic) | Non-sensitive metrics | `outputs/runs/` | No | Yes |
| Legacy synthetic live validation evidence | Synthetic conversations only | `outputs/live_llm_full/`, `outputs/live_llm_smoke/`, `outputs/live_llm_validation/` | No | Yes |
| Real-transcript decision artifacts: reconstructed calls, decision Tasks, split manifest, private references | Confidential de-identified text — Class 1 / Class 2 | `data/decision/` | No | **Yes** — restricted to the owner and the supervisor |
| LLM Gold Labels, annotation/adjudication passes, Group A traces, blind scoring records, evaluation packets | Confidential derived research artifacts — Class 2 | `outputs/decision/` | No | **Yes** — restricted to the owner and the supervisor |

## 2. Rules that the implementation enforces

1. Raw TXT files are never opened by the benchmark, the importer or the tests.
   The importer reads only the de-identified `calls.jsonl` and
   `quarantine.jsonl`.
2. Quarantined transcript text is never restored and never stored. The database
   keeps quarantine metadata only.
3. Transcript text is withheld by default: `RecordView.text_masked` is `None`
   unless a caller explicitly passes `include_text=True`.
4. No corpus text reaches the agent. The corpus package has no import path to the
   runtime, and a test asserts that no evidence identifier appears in any
   captured `AgentContext`.
5. Task and scenario files store opaque transcript identifiers and record ranges
   only. Notes are written by hand and contain no transcript content.
6. Reports, manifests and CLI default output contain no transcript text. Tests
   use a synthetic marker string to prove this.
7. The review workflow writes local files owner-only and requires an explicit
   `--include-text` opt-in to embed masked text.
8. No corpus text is transmitted anywhere: no network code exists in the corpus
   package, and classification is local and deterministic.
9. The wheel never contains the corpus database, transcript text, the
   transcript-id HMAC key or any entity mapping (`tests/test_packaging.py`).

## 3. Provenance: what is known and what is not

### Known and verified

* Corpus counts and hashes are recomputed on every import and must match the
  manifest: 4,527 units, 137,178 records (94,428 speech / 42,750 event), 211
  quarantine units.
* All eleven original record fields are preserved with their original types.
* Record sequence numbers are 0-based and contiguous; timing is monotonic within
  every unit.
* `role` is `UNKNOWN` for all 137,178 records.

### Not known, and therefore not claimed

* **Per-source-file provenance.** The current v2 pipeline produces **no mapping
  table**: `transcript_id` is an HMAC-SHA256 of the original file name under a
  local secret key, so the transformation is one-way and the key is not
  distributed with the corpus. A consumer therefore cannot reconcile a
  transcript back to its source file. `source_file_outcomes` exists but is
  empty, and the gap is recorded as the `source_file_outcomes_unavailable`
  data-quality issue; no fabricated mapping is inserted. The older v1-era
  "protected mapping" design is obsolete and is not part of the current
  workflow (see `data_pipeline/README.md`).
* **Speaker roles.** Nothing in the project infers Agent/Customer from channel
  identifiers. The simulated customer is a synthetic construct, never a
  reconstruction of a real participant.
* **Channel semantics.** Channel ids are preserved verbatim and left
  uninterpreted.
* **Business-intent prevalence.** Domain coverage measures lexical evidence
  coverage under a hand-curated rule set. It is not a frequency estimate.
* **Official China Mobile policy.** No statement in a call proves official
  policy. Business rules used by tasks are explicit synthetic benchmark
  assumptions unless marked `AUTHORITATIVE_POLICY_VERIFIED` — none currently are.
* **Human review.** Zero scenarios and zero domain assignments have been
  human-adjudicated. Every task is a `CANDIDATE`.

## 4. Residual risk

`text_masked` retains residual re-identification risk. The database must be
treated as confidential local research material:

* do not copy transcript text into external tools, chat interfaces or cloud
  services;
* do not attach the database to any issue, pull request or report;
* keep review exports inside the authorised local environment and delete them
  when the review is complete;
* never publish the database, the corpus directory or any text excerpt.

### 4.1 Decision-benchmark artifacts

The decision-benchmark artifacts contain conversation-derived text and model
annotations over that text: reconstructed call text, decision-point task prefixes,
the split manifest, private evaluator references, annotation packets, Pass A/Pass B
records, adjudications, Group A execution traces, blind scoring records and
evaluation packets.

**They ARE included in this private research repository** so that the supervisor can
audit and reproduce the research (`.gitignore` deliberately does not exclude
`data/decision/` or `outputs/decision/`). Inclusion is bounded as follows:

* the repository is **private**, owned by the researcher, with read access granted
  only to the supervisor;
* the original raw TXT export, the wider `cm_outbound_v2` corpus, the corpus SQLite
  database and the transcript-id HMAC key remain **outside** the repository and are
  not published anywhere;
* the frozen Gold Labels are research references adjudicated by `deepseek-v4-pro`;
  they are not verified China Mobile policy and must not be cited as such;
* the material must not be mirrored to a public repository, a fork outside the
  owner/supervisor boundary, an issue tracker, a chat interface or any external
  service.

Prompt files, schemas and source code contain no conversation content and are
publishable.

Removing these paths from the working tree is not required for publication; the
access boundary is enforced by repository visibility and collaborator management, and
by the logical Agent-visible / Private-Evaluator / Research-audit separation described
in `README.md`.

### 4.1.1 Verification performed for this publication

The pre-publication inspection (see
`docs/reports/github_security_audit.md`) scanned **421,158 content-bearing values**
across the decision datasets — every speech turn, Task instruction, Gold Label field
and adjudication rationale — for mobile numbers, landline numbers, 15/18-digit
identity numbers, 16–19-digit card numbers, e-mail addresses, credential-bearing URLs
and API-key shapes. Result: **zero findings** in content fields. All apparent matches
in a raw text scan fall inside opaque identifiers (`CALL_<hex>`, `TASK_<hex>`,
`RUN-<hex>`, `DUPGROUP_<hex>`, `*_sha256`, `cache_key`).

Masking coverage measured on the 137,178 reconstructed records: `<PHONE>` 4,734,
`<ACCOUNT>` 315, `<ADDRESS>` 121, `<NAME>` 118, `<ASR_TOKEN>` 921, `<ID_NO>` 18,
`<CARD_NO>` 6, `<PASSWORD>` 1 — consistent with the pipeline's recorded v2 counts.
No surname + honorific personal name survives (226 honorific occurrences, all
address/role forms such as `<NAME>先生`).

Residual risk that inspection cannot close: names without an honorific, addresses
without markers, and business context that narrows a customer down. A sampled human
review is still required before any use wider than owner + supervisor.

## 5. What an external reader of this repository can verify

Without access to the confidential corpus, a reader can still verify:

* the database schema and importer guarantees (code + tests);
* the classification rule set (`lexicon_manifest()` and this documentation);
* that every task carries evidence identifiers and a provenance label;
* that the evaluator inspects real state (tests);
* that the baseline agent completes every task deterministically;
* that no transcript text is required to run or evaluate a task.

They cannot verify the semantic accuracy of the evidence linkage, because that
requires reading the confidential corpus in the authorised environment.

---

## 6. Data-governance status of this publication

This section records the honest state of the authorisation question. It is not a
legal opinion and it is not a substitute for the data owner's decision.

### What was checked

| Check | Result |
|---|---|
| Existing privacy documentation reviewed | `data_pipeline/PIPELINE.md`, `data_pipeline/README.md`, this document, `docs/decision_benchmark.md` |
| Earlier documented boundary | The v1-era text said the decision artifacts are "confidential" and must stay out of Git; only aggregates may be published |
| New instruction | Explicit owner decision to include the approved processed datasets in a **private** repository shared with the supervisor |
| Residual identifier scan | 0 findings in 421,158 content values (§4.1.1) |
| Re-identifiability from context | Not fully closable by automation; documented as residual risk |
| Unsafe files found | None identified in the proposed upload set |
| Data-use / institutional approval on file | **Not found in the repository** |

### Status: owner confirmation required before publication

`docs/reports/github_release_readiness.md` records the following as an open item:

* the repository is intended to be private, but **no written data-use authorisation
  naming private GitHub hosting exists in this workspace**;
* the earlier privacy documentation treated the processed conversation data as
  confidential and explicitly non-publishable; the current task overrides that for a
  private, supervisor-only repository, and that override is an owner decision, not an
  institutional approval;
* therefore the processed conversation datasets in `data/decision/` and
  `outputs/decision/` must be treated as **"included subject to owner confirmation of
  the private-hosting boundary"** before the branch is pushed.

If the owner cannot confirm that private GitHub hosting is inside the permitted
research-use boundary, the safe fallback is to publish code, tests, documentation,
prompts, split-level metadata and aggregate reports only, and keep
`data/decision/` and `outputs/decision/` local. That fallback requires no code change:
both directories are simply left unstaged.
