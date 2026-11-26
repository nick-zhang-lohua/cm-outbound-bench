"""Real-transcript data layer for the decision-point benchmark.

The layer turns the published de-identified corpus into complete conversations,
validates them, and splits them at call level. It contains no benchmark prompt,
no evaluator and no model call, so it can be audited on its own.
"""

from __future__ import annotations

from cm_outbound_bench.transcripts.ingestion import (
    CALLS_FILENAME,
    MANIFEST_FILENAME,
    QUARANTINE_FILENAME,
    SourceRecord,
    TranscriptCorpusError,
    TranscriptUnit,
    dataset_version,
    iter_units,
    read_dataset_manifest,
    resolve_calls_path,
)
from cm_outbound_bench.transcripts.models import (
    CHANNEL_CUSTOMER,
    CHANNEL_HISTORICAL_AGENT,
    CHANNEL_ROLES,
    NormalizedCall,
    SourceRef,
    Turn,
    format_offset_timestamp,
    role_for_channel,
)
from cm_outbound_bench.transcripts.reconstruction import (
    RECONSTRUCTION_VERSION,
    reconstruct_call,
    reconstruct_calls,
)
from cm_outbound_bench.transcripts.splitting import (
    ALL_SPLITS,
    DEVELOPMENT_SPLITS,
    SPLIT_DEV,
    SPLIT_TEST,
    SPLIT_TRAIN_F,
    SPLIT_TRAIN_R,
    SplitError,
    SplitManifest,
    SplitTargets,
    assign_splits,
    load_split_manifest,
    save_split_manifest,
    verify_split_manifest,
)
from cm_outbound_bench.transcripts.store import (
    CallArtifactError,
    iter_calls,
    iter_fingerprints,
    sha256_file,
    write_calls,
)
from cm_outbound_bench.transcripts.validation import (
    CallFingerprint,
    CorpusValidationReport,
    CorpusValidator,
    QualityIssue,
    group_duplicate_calls,
    validate_calls,
)

__all__ = [
    "ALL_SPLITS",
    "CALLS_FILENAME",
    "CHANNEL_CUSTOMER",
    "CHANNEL_HISTORICAL_AGENT",
    "CHANNEL_ROLES",
    "DEVELOPMENT_SPLITS",
    "MANIFEST_FILENAME",
    "QUARANTINE_FILENAME",
    "RECONSTRUCTION_VERSION",
    "SPLIT_DEV",
    "SPLIT_TEST",
    "SPLIT_TRAIN_F",
    "SPLIT_TRAIN_R",
    "CallArtifactError",
    "CallFingerprint",
    "CorpusValidationReport",
    "CorpusValidator",
    "NormalizedCall",
    "QualityIssue",
    "SourceRecord",
    "SourceRef",
    "SplitError",
    "SplitManifest",
    "SplitTargets",
    "TranscriptCorpusError",
    "TranscriptUnit",
    "Turn",
    "assign_splits",
    "dataset_version",
    "format_offset_timestamp",
    "group_duplicate_calls",
    "iter_calls",
    "iter_fingerprints",
    "iter_units",
    "load_split_manifest",
    "read_dataset_manifest",
    "reconstruct_call",
    "reconstruct_calls",
    "resolve_calls_path",
    "role_for_channel",
    "save_split_manifest",
    "sha256_file",
    "validate_calls",
    "verify_split_manifest",
    "write_calls",
]
