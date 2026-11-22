# Data Privacy and Provenance Limitations

This document states exactly what the released project contains, what it must
never contain, and which provenance claims it can and cannot support.

---

## 1. Data classes

| Data | Classification | Location | Shipped in wheel |
|---|---|---|---|
| Raw TXT transcripts | Confidential source | `data/TXT/` | No |
| De-identified corpus (`cm_outbound_v2`) | Confidential even after masking | `data/deidentified/` | No |
| Corpus SQLite database | Confidential derived artefact | `data/corpus_db/` | No |
| Transcript-id HMAC key (one-way ids; no mapping table) | Secret | not present in this project | No |
| Synthetic runtime database | Synthetic benchmark data | `data/synthetic/` | Yes |
| Benchmark tasks, policies, states | Synthetic benchmark data | `data/tasks/`, `data/policy/`, `data/synthetic/states/` | Yes |
| Corpus aggregate reports | Non-sensitive aggregates | `outputs/corpus/` (generated locally, not committed) |
| Run manifests | Non-sensitive metrics | `outputs/runs/` | No |

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
