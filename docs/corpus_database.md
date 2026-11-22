# Corpus Database

The corpus database is the empirical foundation of the benchmark. It stores the
final de-identified corpus (`cm_outbound_v2`) in SQLite together with provenance,
quarantine metadata, the domain taxonomy and — from Phase 2 onward — domain
assignments and scenario patterns.

It is **research infrastructure**, not benchmark runtime data:

* it lives outside the Python package (`data/corpus_db/`);
* it is never bundled into a wheel or sdist;
* it is never readable by a benchmark agent;
* it never contains transcript text in any log, report or CLI default output.

---

## 1. Location and environment

| Item | Value |
|---|---|
| Default database | `<project-parent>/data/corpus_db/cm_outbound_corpus.sqlite` |
| Corpus input | `<project-parent>/data/deidentified/cm_outbound_v2/` |
| `CM_OUTBOUND_CORPUS_DB` | Overrides the database path |
| `CM_OUTBOUND_CORPUS_DIR` | Overrides the corpus input directory |
| `CM_OUTBOUND_DATA_ROOT` | Overrides the shared `data` root |

Resolution order: explicit CLI argument → environment variable →
`CM_OUTBOUND_DATA_ROOT` → project-checkout default.

## 2. Creating the database

```bash
cd cm-outbound-bench
.venv/bin/python -m cm_outbound_bench.cli corpus import
```

Import properties:

1. **Verified inputs.** `manifest.json` is loaded; the SHA-256 of `calls.jsonl`
   and `quarantine.jsonl` and their line counts are recomputed and compared with
   the manifest before anything is written.
2. **Streaming.** Units are read line by line and records are inserted in
   batches (default 2,000 rows). Memory is bounded regardless of corpus size.
3. **Transactional.** All rows are written inside one `BEGIN IMMEDIATE`
   transaction into a temporary file in the destination directory.
4. **Atomic publication.** The temporary database is closed, reopened, checked
   with `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, a recount and a
   per-unit `record_count` reconciliation, and only then moved into place with
   `os.replace`.
5. **No silent overwrite.** An existing database with different inputs raises
   `database_exists` unless `--rebuild` is given explicitly.
6. **Idempotent.** Re-importing identical inputs reports `already_present` and
   does not touch the file; the file hash before and after is identical.
7. **Fail-loud typing.** Every record field is type-checked against the corpus
   schema. A wrong type or a missing field aborts the import and leaves no
   database behind.

Schema and importer versions are recorded in `corpus_metadata` and in every
`import_runs` row.

## 3. Schema

`STRICT` tables, foreign keys enabled, schema version `1.0`.

### `corpus_metadata` (single row)

`schema_version`, `corpus_version`, `source_manifest_hash`, `source_calls_hash`,
`source_quarantine_hash`, `imported_at`, `expected_*`/`imported_*` counts for
transcript units, records and quarantine units, `importer_version`, `notes`.

### `import_runs`

One row per published import: `run_id`, `importer_version`, `schema_version`,
`started_at`, `completed_at`, `status`, input hashes and counts, output counts,
`validation_status`, safe `error_summary`.

### `source_file_outcomes` (currently empty — documented limitation)

`source_id`, `source_content_sha256`, `source_size_bytes`, `outcome`,
`transcript_id`, `quarantine_reason_codes_json`, `processing_run_id`.

The current v2 pipeline produces **no source-file → transcript mapping table**:
`transcript_id` is a one-way HMAC-SHA256 of the original file name under a local
secret key, so the importer cannot reconstruct the source file. The table exists
so that provenance can be restored later without a schema migration, but **it is
empty and the database does not claim per-source-file provenance.** This
limitation is also recorded as the `source_file_outcomes_unavailable`
data-quality issue. (The older v1-era protected-mapping design is obsolete; see
`data_pipeline/README.md`.)

### `transcript_units`

`transcript_id` (PK), `schema_version`, `duration_ms`, `record_count`,
`processing_run_id`.

### `transcript_records`

Composite primary key `(transcript_id, seq)`, `WITHOUT ROWID`. All eleven
original fields are preserved with their original names and types:

| Column | Source field | Notes |
|---|---|---|
| `seq` | `seq` | 0-based, contiguous within a unit |
| `start_ms` | `start_ms` | integer milliseconds |
| `end_ms` | `end_ms` | integer milliseconds |
| `channel_id` | `channel_id` | integer, semantics unverified |
| `role` | `role` | always `UNKNOWN` in this corpus |
| `payload_type` | `payload_type` | `SPEECH` or `EVENT` |
| `event` | `event` | `NULL` for speech records |
| `text_masked` | `text_masked` | `NULL` for event records |
| `is_truncated` | `is_truncated` | stored as `0`/`1` |
| `asr_flags_json` | `asr_flags` | JSON array, order preserved |
| `redaction_flags_json` | `redaction_flags` | JSON array, order preserved |

Repeated identical event or speech records are preserved: the primary key is
`(transcript_id, seq)`, never content.

### `quarantine_units`

`transcript_id` (PK), `reason_codes_json`, `affected_seq_json`, `status`,
`processing_run_id`. Quarantined transcript text is **not** in this database.

### `data_quality_issues`

`issue_id`, `target_type`, `target_id`, `issue_code`, `severity`,
`resolution_status`, `processing_run_id`, `detail_json`.

Every import records at least: `role_unknown_for_all_records`,
`channel_semantics_unconfirmed`, `source_file_outcomes_unavailable`,
`records_empty_after_masking`, `records_truncated`,
`quarantined_units_excluded`. Detected unit anomalies
(`units_with_noncontiguous_seq`, `units_with_timing_inversion`,
`units_with_unexpected_schema_version`) are added only when non-zero.

### `domain_taxonomy`

`domain_id` (PK), `name`, `domain_class` (`PRIMARY_BUSINESS` |
`CROSS_CUTTING`), `description`, `taxonomy_version`, `status`
(`PROPOSED` | `ACTIVE` | `RETIRED`).

D01–D06 are seeded by the importer with status **`PROPOSED`**. A taxonomy that
has not been validated against corpus evidence never claims `ACTIVE`.

### `domain_assignments`

`assignment_id`, `target_type` (`RECORD` | `TRANSCRIPT_UNIT`), `target_id`,
`domain_id`, `label_method` (`RULE_CANDIDATE`, `MODEL_SUGGESTED`,
`HUMAN_REVIEWED`, `ADJUDICATED`), `confidence`, `review_status` (`CANDIDATE`,
`HUMAN_REVIEWED`, `ADJUDICATED`, `REJECTED`), `is_primary`,
`assignment_version`, `reviewer_or_model`, `created_at`.

Unique on `(target_type, target_id, domain_id, assignment_version)`, so a
re-run with a new `assignment_version` supersedes rather than mutates history.

### `scenario_patterns` and `scenario_reviews`

`scenario_patterns` stores the bridge from corpus evidence to benchmark
scenarios: primary/secondary domains, `pattern_type`,
`observed_pattern_summary`, intent and speech-act labels, workflow stage,
compliance conditions, `evidence_refs_json`, `validation_status`,
`rule_provenance_status` and `version`.

`rule_provenance_status` is one of `OBSERVED_PATTERN`,
`HUMAN_REVIEWED_PATTERN`, `AUTHORITATIVE_POLICY_VERIFIED`,
`SYNTHETIC_BENCHMARK_ASSUMPTION`, `SAFETY_STRESS_SCENARIO`.

`scenario_reviews` records adjudication: `review_id`, `scenario_id`,
`review_status`, `reviewer`, `review_notes_safe`, `reviewed_at`.

## 4. Query interface

```python
from cm_outbound_bench.corpus import CorpusDatabase

with CorpusDatabase.open_read_only() as corpus:
    statistics = corpus.get_statistics()
    units = corpus.list_transcript_units(limit=20)
    unit = corpus.get_transcript_unit(units[0].transcript_id)
    records = corpus.list_transcript_records(unit.transcript_id)
    window = corpus.find_records_in_window(start_ms=0, end_ms=60_000)
    events = corpus.count_events()
    channels = corpus.count_channels()
    quarantine = corpus.get_quarantine_summary()
    domains = corpus.list_domains()
    integrity = corpus.validate_integrity()
```

The handle opens SQLite with `mode=ro` and `PRAGMA query_only = ON`. Writes
raise `sqlite3.OperationalError`.

### Text access is explicit

`RecordView` reports `has_text` and `text_length` by default and leaves
`text_masked` as `None`. Passing `include_text=True` is the only way to obtain
the masked text. This keeps accidental leakage out of reports, logs and agent
prompts.

## 5. CLI

```bash
cm-outbound corpus import [--database PATH] [--corpus-dir PATH] [--rebuild] [--json]
cm-outbound corpus verify [--database PATH] [--json]
cm-outbound corpus stats [--database PATH] [--json]
cm-outbound corpus quarantine-summary [--database PATH] [--json]
cm-outbound corpus list-domains [--database PATH] [--json]
```

Exit codes: `0` success, `1` validation failure, `2` missing input or usage
error. No command prints transcript text.

## 6. Verified import result

| Item | Value |
|---|---:|
| Transcript units | 4,527 |
| Records | 137,178 |
| Speech records | 94,428 |
| Event records | 42,750 |
| Quarantine units | 211 |
| `PRAGMA integrity_check` | `ok` |
| Foreign key violations | 0 |
| Unit `record_count` mismatches | 0 |
| Database size | 47,833,088 bytes |
| Database SHA-256 | `953c8f2f7b4e56632a7fe1e437d63509e216bd46ded0218b6a50a9491aa07ffd` |

The size and hash above record the import verified when the corpus database was
first built. The database file is a derived artefact, rebuilt locally from the
de-identified corpus and never committed to the package, so a later re-import
produces a different file and a different hash.

## 7. Known limitations

1. **No per-source-file provenance.** The v2 pipeline produces no mapping table;
   transcript ids are one-way HMACs. See `source_file_outcomes`.
2. **Speaker roles are unknown.** `role` is `UNKNOWN` for all 137,178 records.
   Nothing in this database may be used to infer Agent/Customer turns from
   `channel_id`.
3. **Channel semantics unverified.** Preserved verbatim, not interpreted.
4. **Quarantined units are metadata only.** Their text was never delivered and
   is not stored here.
5. **`text_masked` retains residual re-identification risk.** The database is
   confidential local research material. Do not copy text into external tools.
