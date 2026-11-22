"""Command-line surface for the corpus database.

No command prints transcript text. Machine-readable output is available with
``--json`` for every subcommand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cm_outbound_bench.corpus.classification import (
    ClassificationError,
    classify_corpus,
)
from cm_outbound_bench.corpus.database import CorpusDatabase
from cm_outbound_bench.corpus.importer import (
    CorpusImportError,
    import_corpus,
)
from cm_outbound_bench.corpus.manifest import CorpusInputError
from cm_outbound_bench.corpus.paths import (
    default_corpus_dir,
    default_database_path,
    resolve_corpus_dir,
    resolve_database_path,
)
from cm_outbound_bench.corpus.report import (
    build_coverage_report,
    render_markdown,
    write_report,
)
from cm_outbound_bench.corpus.review import apply_review, export_review_sample


def add_parser(subparsers: Any) -> None:
    """Register the ``corpus`` command group on an argparse subparser set."""
    corpus_parser = subparsers.add_parser(
        "corpus",
        help="Corpus database operations (import, verify, statistics).",
    )
    corpus_subparsers = corpus_parser.add_subparsers(
        dest="corpus_command", required=True
    )

    import_parser = corpus_subparsers.add_parser(
        "import",
        help="Import the de-identified corpus into SQLite.",
    )
    import_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help=f"Corpus database path (default: {default_database_path()}).",
    )
    import_parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help=f"De-identified corpus directory (default: {default_corpus_dir()}).",
    )
    import_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Replace an existing database after verifying the inputs.",
    )
    import_parser.add_argument("--json", action="store_true", help="JSON output.")

    verify_parser = corpus_subparsers.add_parser(
        "verify",
        help="Validate database integrity and declared counts.",
    )
    _add_database_option(verify_parser)
    verify_parser.add_argument("--json", action="store_true", help="JSON output.")

    stats_parser = corpus_subparsers.add_parser(
        "stats",
        help="Print corpus statistics.",
    )
    _add_database_option(stats_parser)
    stats_parser.add_argument("--json", action="store_true", help="JSON output.")

    quarantine_parser = corpus_subparsers.add_parser(
        "quarantine-summary",
        help="Print the quarantine summary.",
    )
    _add_database_option(quarantine_parser)
    quarantine_parser.add_argument(
        "--json", action="store_true", help="JSON output."
    )

    domains_parser = corpus_subparsers.add_parser(
        "list-domains",
        help="List the seeded domain taxonomy.",
    )
    _add_database_option(domains_parser)
    domains_parser.add_argument("--json", action="store_true", help="JSON output.")

    classify_parser = corpus_subparsers.add_parser(
        "classify",
        help="Run the deterministic candidate domain classifier.",
    )
    _add_database_option(classify_parser)
    classify_parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Refuse to replace existing candidate assignments.",
    )
    classify_parser.add_argument("--json", action="store_true", help="JSON output.")

    coverage_parser = corpus_subparsers.add_parser(
        "coverage-report",
        help="Build the aggregate domain coverage report.",
    )
    _add_database_option(coverage_parser)
    coverage_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the Markdown report to this path.",
    )
    coverage_parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write the JSON report to this path.",
    )
    coverage_parser.add_argument("--json", action="store_true", help="JSON output.")

    review_export_parser = corpus_subparsers.add_parser(
        "export-review-sample",
        help="Export a local-only stratified review sample.",
    )
    _add_database_option(review_export_parser)
    review_export_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Local destination for the review JSON file.",
    )
    review_export_parser.add_argument(
        "--per-stratum",
        type=int,
        default=10,
        help="Items to sample per stratum (default: 10).",
    )
    review_export_parser.add_argument(
        "--seed",
        type=int,
        default=20261120,
        help="Sampling seed for reproducible strata.",
    )
    review_export_parser.add_argument(
        "--include-text",
        action="store_true",
        help=(
            "Include masked transcript text in the sample. The file is written "
            "owner-only and must stay in the authorised local environment."
        ),
    )

    review_apply_parser = corpus_subparsers.add_parser(
        "apply-review",
        help="Apply a completed local review file.",
    )
    _add_database_option(review_apply_parser)
    review_apply_parser.add_argument(
        "--review",
        type=Path,
        required=True,
        help="Path to the completed review JSON file.",
    )
    review_apply_parser.add_argument(
        "--reviewer",
        required=True,
        help="Reviewer identifier recorded on adjudicated assignments.",
    )
    review_apply_parser.add_argument(
        "--json", action="store_true", help="JSON output."
    )


def _add_database_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help=f"Corpus database path (default: {default_database_path()}).",
    )


def dispatch(args: argparse.Namespace) -> int:
    """Run one corpus subcommand and return a process exit code."""
    handlers = {
        "import": _run_import,
        "verify": _run_verify,
        "stats": _run_stats,
        "quarantine-summary": _run_quarantine_summary,
        "list-domains": _run_list_domains,
        "classify": _run_classify,
        "coverage-report": _run_coverage_report,
        "export-review-sample": _run_export_review_sample,
        "apply-review": _run_apply_review,
    }
    handler = handlers.get(args.corpus_command)
    if handler is None:  # pragma: no cover - argparse prevents this
        print(f"error: unknown corpus command {args.corpus_command!r}", file=sys.stderr)
        return 2
    return handler(args)


def _run_import(args: argparse.Namespace) -> int:
    try:
        report = import_corpus(
            database_path=resolve_database_path(args.database),
            corpus_dir=resolve_corpus_dir(args.corpus_dir),
            rebuild=args.rebuild,
        )
    except (CorpusImportError, CorpusInputError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        print(f"Status: {report.status.value}")
        print(f"Database: {report.database_path}")
        print(f"Database SHA-256: {report.database_sha256}")
        print(f"Schema version: {report.schema_version}")
        print(f"Corpus version: {report.corpus_version}")
        print(f"Import run: {report.run_id or '-'}")
        print(
            "Units: "
            f"expected {report.expected.transcript_units}, "
            f"imported {report.imported.transcript_units}"
        )
        print(
            "Records: "
            f"expected {report.expected.records}, "
            f"imported {report.imported.records}"
        )
        print(
            "Speech records: "
            f"expected {report.expected.speech_records}, "
            f"imported {report.imported.speech_records}"
        )
        print(
            "Event records: "
            f"expected {report.expected.event_records}, "
            f"imported {report.imported.event_records}"
        )
        print(
            "Quarantine units: "
            f"expected {report.expected.quarantine_units}, "
            f"imported {report.imported.quarantine_units}"
        )
        print(f"Validation: {report.validation_status}")
        if report.notes:
            print("Notes: " + ", ".join(report.notes))
    return 0 if report.validation_status == "ok" else 1


def _run_verify(args: argparse.Namespace) -> int:
    try:
        with CorpusDatabase.open_read_only(args.database) as corpus:
            report = corpus.validate_integrity()
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        print(f"Database: {report.database_path}")
        print(f"Integrity check: {report.integrity_check}")
        print(f"Foreign key errors: {report.foreign_key_errors}")
        print(
            "Units: "
            f"declared {report.declared_units}, actual {report.actual_units}"
        )
        print(
            "Records: "
            f"declared {report.declared_records}, actual {report.actual_records}"
        )
        print(
            "Quarantine units: "
            f"declared {report.declared_quarantine_units}, "
            f"actual {report.actual_quarantine_units}"
        )
        print(
            "Unit record-count mismatches: "
            f"{report.unit_record_count_mismatches}"
        )
        print(f"Result: {'OK' if report.ok else 'FAILED'}")
        if report.errors:
            print("Errors: " + ", ".join(report.errors))
    return 0 if report.ok else 1


def _run_stats(args: argparse.Namespace) -> int:
    try:
        with CorpusDatabase.open_read_only(args.database) as corpus:
            statistics = corpus.get_statistics()
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(statistics.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0

    print(f"Schema version: {statistics.schema_version}")
    print(f"Corpus version: {statistics.corpus_version}")
    print(f"Imported at: {statistics.imported_at}")
    print(f"Transcript units: {statistics.transcript_units}")
    print(f"Records: {statistics.records}")
    print(f"  speech: {statistics.speech_records}")
    print(f"  event: {statistics.event_records}")
    print(f"Quarantine units: {statistics.quarantine_units}")
    print(f"Domains: {statistics.domains}")
    print(f"Domain assignments: {statistics.domain_assignments}")
    print(f"Scenario patterns: {statistics.scenario_patterns}")
    print(f"Data quality issues: {statistics.data_quality_issues}")
    print("Payload types: " + _format_counts(statistics.payload_types))
    print("Channels: " + _format_counts(statistics.channels))
    print("Events: " + _format_counts(statistics.events))
    print("Roles: " + _format_counts(statistics.roles))
    print("Assignment review statuses: " + _format_counts(statistics.review_statuses))
    print("Issue severities: " + _format_counts(statistics.severity_counts))
    return 0


def _run_quarantine_summary(args: argparse.Namespace) -> int:
    try:
        with CorpusDatabase.open_read_only(args.database) as corpus:
            summary = corpus.get_quarantine_summary()
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0

    print(f"Quarantine units: {summary.total_units}")
    print(f"Affected record positions: {summary.affected_seq_total}")
    print("Statuses: " + _format_counts(summary.statuses))
    print("Reasons: " + _format_counts(summary.reason_counts))
    print(f"Note: {summary.note}")
    return 0


def _run_list_domains(args: argparse.Namespace) -> int:
    try:
        with CorpusDatabase.open_read_only(args.database) as corpus:
            domains = corpus.list_domains()
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                [domain.model_dump(mode="json") for domain in domains],
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    for domain in domains:
        print(
            f"{domain.domain_id}\t{domain.domain_class}\t{domain.status}\t"
            f"{domain.name}\t(taxonomy {domain.taxonomy_version})"
        )
    return 0


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _run_classify(args: argparse.Namespace) -> int:
    try:
        report = classify_corpus(
            database_path=args.database,
            replace=not args.no_replace,
        )
    except (ClassificationError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0

    print(f"Database: {report.database_path}")
    print(f"Assignment version: {report.assignment_version}")
    print(f"Label method: {report.label_method} ({report.review_status})")
    print(f"Engine: {report.model_name}")
    print(f"Eligible speech records: {report.eligible_records}")
    print(f"Skipped speech records: {report.skipped_records}")
    if report.skipped_reasons:
        print("  reasons: " + _format_counts(report.skipped_reasons))
    print(f"Unclassified eligible records: {report.unclassified_records}")
    print(f"Record assignments: {report.record_assignments}")
    print(f"Unit assignments: {report.unit_assignments}")
    print(f"Multi-label records: {report.multi_label_records}")
    print(f"Ambiguous records: {report.ambiguous_records}")
    print(f"Unclassified units: {report.unclassified_units}")
    print("Record labels by domain: " + _format_counts(report.records_by_domain))
    print("Unit labels by domain: " + _format_counts(report.units_by_domain))
    print(
        "Reminder: all labels are RULE_CANDIDATE candidates pending human review."
    )
    return 0


def _run_coverage_report(args: argparse.Namespace) -> int:
    try:
        report = build_coverage_report(database_path=args.database)
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.out is not None or args.json_out is not None:
        write_report(report, markdown_path=args.out, json_path=args.json_out)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0

    print(render_markdown(report), end="")
    if args.out is not None:
        print(f"\nMarkdown written to {args.out}")
    if args.json_out is not None:
        print(f"JSON written to {args.json_out}")
    return 0


def _run_export_review_sample(args: argparse.Namespace) -> int:
    try:
        sample = export_review_sample(
            output_path=args.out,
            database_path=args.database,
            per_stratum=args.per_stratum,
            seed=args.seed,
            include_text=args.include_text,
        )
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"Review sample written to {args.out}")
    print(f"Items: {len(sample.items)}")
    print(f"Seed: {sample.seed}")
    print(f"Strata: {', '.join(sample.strata)}")
    print(
        "Transcript text included: "
        + ("yes - LOCAL ONLY, do not transmit" if sample.include_text else "no")
    )
    return 0


def _run_apply_review(args: argparse.Namespace) -> int:
    try:
        report = apply_review(
            review_path=args.review,
            reviewer=args.reviewer,
            database_path=args.database,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0

    print(f"Review file: {report.review_path}")
    print(f"Reviewer: {report.reviewer}")
    print(f"Items read: {report.items_read}")
    print(f"Approved: {report.approved}")
    print(f"Rejected: {report.rejected}")
    print(f"Changed: {report.changed}")
    print(f"Skipped: {report.skipped}")
    if report.errors:
        print("Errors: " + "; ".join(report.errors))
    return 0 if not report.errors else 1
