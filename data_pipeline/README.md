# Data Pipeline and Privacy Boundary

**Current pipeline:** `cm_outbound_v2` — pipeline `2.0.0`, record schema `1.0`,
manifest `2.0`. The implementation reference is [PIPELINE.md](PIPELINE.md); this
file is the concise entry point.

The pipeline is offline, local-only, standard-library-only and separate from
benchmark runtime code. It reads the raw CM outbound TXT transcripts read-only
and writes only masked records plus opaque identifiers under
`<project-parent>/data/deidentified/cm_outbound_v2/`.

## No-mapping model (current)

De-identification is **one-way**. The pipeline produces **no entity mapping
table** and no cross-transcript relinking artefact:

* `transcript_id` is `CALL_` + HMAC-SHA256(original file name)[:24], keyed by a
  local secret stored outside the repository
  (`~/.local/share/cm-outbound-bench/keys/transcript_id_hmac.key`, `0600`).
* The key is never shipped and no reverse lookup is written.
* Cross-transcript entity consistency is deliberately **not** preserved: the
  same phone number or name becomes the same placeholder, not a stable
  pseudonym.
* Consequence: a consumer cannot reconstruct per-source-file provenance. The
  corpus database keeps `source_file_outcomes` empty and records the gap as
  `source_file_outcomes_unavailable`; this is an accepted limitation, not a
  missing feature.
* Neither the Agent nor the User Simulator ever sees transcript identifiers or
  masked corpus text.

### Historical: v1 protected-mapping approach (obsolete)

An earlier v1-era design note in this directory proposed maintaining
cross-transcript entity consistency through a protected mapping. That approach
is **historical and obsolete**. The current v2 pipeline replaced it with the
no-mapping model above, and the `cm_outbound_v1` output is retained under
`<project-parent>/data/deidentified/cm_outbound_v1/` as historical evidence only
— it must not be used for new work. Neither dataset ships an entity map, and
re-identification/relinking is out of scope for this project.

## Storage separation

Raw transcripts and the HMAC key must remain outside this repository in
access-controlled storage. The pipeline never writes, copies or logs them, and
the output path is validated before any directory is created; output must stay
inside the approved `<project-parent>/data/deidentified` root.

The pipeline reads no `CM_OUTBOUND_*` environment variable. The old
`CM_OUTBOUND_SECURE_DIR` note described the withdrawn v1-era design and is no
longer part of the documented workflow.

## Processing stages

1. Read each source file read-only (binary open, strict UTF-8).
2. Normalise digit entities and mask names, phone numbers, identity numbers,
   cards, addresses, accounts, verification codes and passwords.
3. Quarantine whole units fail-closed when an entity boundary cannot be proven.
4. Run the independent scanner in-process, then again before publication.
5. Apply the structural gate, write to staging, read back, revalidate and
   promote atomically.
6. Emit `calls.jsonl`, `quarantine.jsonl`, `manifest.json` and the dataset
   `README.md`.

Rule tables, exit codes, the manifest schema and the v1 → v2 repair evidence are
in [PIPELINE.md](PIPELINE.md).

## Non-negotiable rules

- Never commit raw transcripts, the HMAC key or any mapping/relinking artefact.
- Never put real PII in source code, tests, fixtures, documentation or logs.
- Never expose transcript identifiers or masked corpus text to the Agent or the
  User Simulator.
- Never send raw or potentially re-identifiable data to an external LLM API.
- Keep synthetic development data separate from real-data-derived artefacts.
- A sampled human review of masked output is still required before external use.
