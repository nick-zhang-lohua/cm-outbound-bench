"""Corpus research layer: database import, schema and read-only queries."""

from __future__ import annotations

from cm_outbound_bench.corpus.database import (
    CorpusDatabase,
    CorpusStatistics,
    DataQualityIssue,
    DomainAssignmentView,
    DomainView,
    IntegrityReport,
    QuarantineSummary,
    QuarantineUnitView,
    RecordView,
    ScenarioView,
    UnitView,
)
from cm_outbound_bench.corpus.importer import (
    CorpusImportError,
    CountSummary,
    ImportReport,
    ImportStatus,
    import_corpus,
)
from cm_outbound_bench.corpus.manifest import (
    CorpusInputError,
    ManifestValidation,
    validate_inputs,
)
from cm_outbound_bench.corpus.paths import (
    default_corpus_dir,
    default_database_path,
    resolve_corpus_dir,
    resolve_database_path,
)
from cm_outbound_bench.corpus.schema import (
    CORPUS_VERSION,
    IMPORTER_VERSION,
    SCHEMA_VERSION,
    TAXONOMY_VERSION,
)

__all__ = [
    "CORPUS_VERSION",
    "IMPORTER_VERSION",
    "SCHEMA_VERSION",
    "TAXONOMY_VERSION",
    "CorpusDatabase",
    "CorpusImportError",
    "CorpusInputError",
    "CorpusStatistics",
    "CountSummary",
    "DataQualityIssue",
    "DomainAssignmentView",
    "DomainView",
    "ImportReport",
    "ImportStatus",
    "IntegrityReport",
    "ManifestValidation",
    "QuarantineSummary",
    "QuarantineUnitView",
    "RecordView",
    "ScenarioView",
    "UnitView",
    "default_corpus_dir",
    "default_database_path",
    "import_corpus",
    "resolve_corpus_dir",
    "resolve_database_path",
    "validate_inputs",
]
