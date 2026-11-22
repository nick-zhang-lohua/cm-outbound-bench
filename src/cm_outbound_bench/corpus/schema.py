"""Versioned SQLite schema for the de-identified corpus database.

The corpus database is a research artefact that lives outside the distributable
Python package. It stores the de-identified transcript corpus together with
provenance, quarantine metadata, domain taxonomy and (from later phases) domain
assignments and scenario patterns.

Design rules encoded here:

* Every table is ``STRICT`` so SQLite refuses silent type coercion.
* Foreign keys are declared for every structural relationship.
* Original corpus fields keep their original names and value types.
* No table may contain raw source file names, paths, HMAC secrets or entity maps.
"""

from __future__ import annotations

SCHEMA_VERSION = "1.0"
IMPORTER_VERSION = "1.0.0"
TAXONOMY_VERSION = "1.0"
CORPUS_VERSION = "cm_outbound_v2"
#: The ``schema_version`` value carried by every transcript unit in the corpus.
CORPUS_UNIT_SCHEMA_VERSION = "1.0"

DDL: tuple[str, ...] = (
    """
    CREATE TABLE corpus_metadata (
        metadata_id INTEGER PRIMARY KEY CHECK (metadata_id = 1),
        schema_version TEXT NOT NULL,
        corpus_version TEXT NOT NULL,
        source_manifest_hash TEXT NOT NULL,
        source_calls_hash TEXT NOT NULL,
        source_quarantine_hash TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        expected_transcript_units INTEGER NOT NULL,
        imported_transcript_units INTEGER NOT NULL,
        expected_records INTEGER NOT NULL,
        imported_records INTEGER NOT NULL,
        expected_quarantine_units INTEGER NOT NULL,
        imported_quarantine_units INTEGER NOT NULL,
        importer_version TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT ''
    ) STRICT
    """,
    """
    CREATE TABLE import_runs (
        run_id TEXT PRIMARY KEY,
        importer_version TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL
            CHECK (status IN ('running', 'completed', 'failed')),
        input_hashes_json TEXT NOT NULL,
        input_counts_json TEXT NOT NULL,
        output_counts_json TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        error_summary TEXT
    ) STRICT
    """,
    """
    CREATE TABLE source_file_outcomes (
        source_id TEXT PRIMARY KEY,
        source_content_sha256 TEXT,
        source_size_bytes INTEGER,
        outcome TEXT NOT NULL
            CHECK (outcome IN ('DELIVERED', 'QUARANTINED', 'UNKNOWN')),
        transcript_id TEXT,
        quarantine_reason_codes_json TEXT NOT NULL DEFAULT '[]',
        processing_run_id TEXT NOT NULL,
        FOREIGN KEY (transcript_id) REFERENCES transcript_units(transcript_id),
        FOREIGN KEY (processing_run_id) REFERENCES import_runs(run_id)
    ) STRICT
    """,
    """
    CREATE TABLE transcript_units (
        transcript_id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        record_count INTEGER NOT NULL CHECK (record_count >= 0),
        processing_run_id TEXT NOT NULL,
        FOREIGN KEY (processing_run_id) REFERENCES import_runs(run_id)
    ) STRICT
    """,
    """
    CREATE TABLE transcript_records (
        transcript_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        payload_type TEXT NOT NULL,
        event TEXT,
        text_masked TEXT,
        is_truncated INTEGER NOT NULL CHECK (is_truncated IN (0, 1)),
        asr_flags_json TEXT NOT NULL,
        redaction_flags_json TEXT NOT NULL,
        PRIMARY KEY (transcript_id, seq),
        FOREIGN KEY (transcript_id) REFERENCES transcript_units(transcript_id)
            ON DELETE CASCADE
    ) STRICT, WITHOUT ROWID
    """,
    """
    CREATE TABLE quarantine_units (
        transcript_id TEXT PRIMARY KEY,
        reason_codes_json TEXT NOT NULL,
        affected_seq_json TEXT NOT NULL,
        status TEXT NOT NULL,
        processing_run_id TEXT NOT NULL,
        FOREIGN KEY (processing_run_id) REFERENCES import_runs(run_id)
    ) STRICT
    """,
    """
    CREATE TABLE data_quality_issues (
        issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        issue_code TEXT NOT NULL,
        severity TEXT NOT NULL
            CHECK (severity IN ('info', 'warning', 'error')),
        resolution_status TEXT NOT NULL
            CHECK (resolution_status IN ('open', 'accepted', 'resolved')),
        processing_run_id TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (processing_run_id) REFERENCES import_runs(run_id)
    ) STRICT
    """,
    """
    CREATE TABLE domain_taxonomy (
        domain_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        domain_class TEXT NOT NULL
            CHECK (domain_class IN ('PRIMARY_BUSINESS', 'CROSS_CUTTING')),
        description TEXT NOT NULL,
        taxonomy_version TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('PROPOSED', 'ACTIVE', 'RETIRED'))
    ) STRICT
    """,
    """
    CREATE TABLE domain_assignments (
        assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_type TEXT NOT NULL
            CHECK (target_type IN ('RECORD', 'TRANSCRIPT_UNIT')),
        target_id TEXT NOT NULL,
        domain_id TEXT NOT NULL,
        label_method TEXT NOT NULL
            CHECK (label_method IN (
                'RULE_CANDIDATE', 'MODEL_SUGGESTED', 'HUMAN_REVIEWED', 'ADJUDICATED'
            )),
        confidence REAL,
        review_status TEXT NOT NULL
            CHECK (review_status IN (
                'CANDIDATE', 'HUMAN_REVIEWED', 'ADJUDICATED', 'REJECTED'
            )),
        is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
        assignment_version TEXT NOT NULL,
        reviewer_or_model TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (target_type, target_id, domain_id, assignment_version),
        FOREIGN KEY (domain_id) REFERENCES domain_taxonomy(domain_id)
    ) STRICT
    """,
    """
    CREATE TABLE scenario_patterns (
        scenario_id TEXT PRIMARY KEY,
        primary_domain_id TEXT NOT NULL,
        secondary_domain_ids_json TEXT NOT NULL DEFAULT '[]',
        pattern_type TEXT NOT NULL,
        observed_pattern_summary TEXT NOT NULL,
        intent_labels_json TEXT NOT NULL DEFAULT '[]',
        speech_act_labels_json TEXT NOT NULL DEFAULT '[]',
        workflow_stage TEXT,
        compliance_conditions_json TEXT NOT NULL DEFAULT '[]',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        validation_status TEXT NOT NULL
            CHECK (validation_status IN (
                'CANDIDATE', 'HUMAN_REVIEWED', 'VALIDATED', 'REJECTED'
            )),
        rule_provenance_status TEXT NOT NULL
            CHECK (rule_provenance_status IN (
                'OBSERVED_PATTERN',
                'HUMAN_REVIEWED_PATTERN',
                'AUTHORITATIVE_POLICY_VERIFIED',
                'SYNTHETIC_BENCHMARK_ASSUMPTION',
                'SAFETY_STRESS_SCENARIO'
            )),
        version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (primary_domain_id) REFERENCES domain_taxonomy(domain_id)
    ) STRICT
    """,
    """
    CREATE TABLE scenario_reviews (
        review_id INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_id TEXT NOT NULL,
        review_status TEXT NOT NULL
            CHECK (review_status IN (
                'APPROVED', 'REJECTED', 'NEEDS_CHANGES', 'UNCERTAIN'
            )),
        reviewer TEXT NOT NULL,
        review_notes_safe TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL,
        FOREIGN KEY (scenario_id) REFERENCES scenario_patterns(scenario_id)
    ) STRICT
    """,
    "CREATE INDEX idx_records_payload_type ON transcript_records(payload_type)",
    "CREATE INDEX idx_records_event ON transcript_records(event)",
    "CREATE INDEX idx_records_channel ON transcript_records(channel_id)",
    "CREATE INDEX idx_records_role ON transcript_records(role)",
    "CREATE INDEX idx_records_start ON transcript_records(transcript_id, start_ms)",
    "CREATE INDEX idx_units_duration ON transcript_units(duration_ms)",
    "CREATE INDEX idx_units_record_count ON transcript_units(record_count)",
    "CREATE INDEX idx_quarantine_status ON quarantine_units(status)",
    "CREATE INDEX idx_assignments_domain ON domain_assignments(domain_id)",
    "CREATE INDEX idx_assignments_target ON domain_assignments(target_type, target_id)",
    "CREATE INDEX idx_assignments_review ON domain_assignments(review_status)",
    "CREATE INDEX idx_scenarios_domain ON scenario_patterns(primary_domain_id)",
    "CREATE INDEX idx_scenarios_status ON scenario_patterns(validation_status)",
    "CREATE INDEX idx_dq_code ON data_quality_issues(issue_code)",
)

#: Seed taxonomy. All records start as PROPOSED: a taxonomy that has not been
#: validated against corpus evidence must never claim to be ACTIVE.
DOMAIN_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "D01",
        "Product & Package",
        "PRIMARY_BUSINESS",
        "Mobile plans, data allowance, voice minutes, package contents, service "
        "benefits, plan pricing, package comparison and service availability.",
    ),
    (
        "D02",
        "Promotion & Offer",
        "PRIMARY_BUSINESS",
        "Promotions, gifts and bonus allowances, points redemption, eligibility, "
        "offer enrollment, promotional validity, effective dates, expiry, renewal "
        "and promotional restrictions.",
    ),
    (
        "D03",
        "Billing & Recharge",
        "PRIMARY_BUSINESS",
        "Monthly fees, charges, account balance, arrears, recharge, payment, "
        "discounts, billing explanations and billing disputes.",
    ),
    (
        "D04",
        "Subscription & Account Lifecycle",
        "PRIMARY_BUSINESS",
        "Upgrade, downgrade, plan change, cancellation, unsubscription, "
        "suspension, reactivation, account closure, number ownership transfer and "
        "subscription status.",
    ),
    (
        "D05",
        "Identity & Ownership",
        "CROSS_CUTTING",
        "Identity verification, real-name registration, phone-number ownership, "
        "account-holder relationships, authorised representatives and ownership "
        "transfer conditions.",
    ),
    (
        "D06",
        "Notification & Fulfillment",
        "CROSS_CUTTING",
        "SMS notification, enrollment confirmation, business notifications, "
        "delivery status, reminders, follow-up and order fulfilment.",
    ),
)


def create_schema(connection) -> None:  # noqa: ANN001 - sqlite3.Connection
    """Create every table and index in the given connection."""
    for statement in DDL:
        connection.execute(statement)
