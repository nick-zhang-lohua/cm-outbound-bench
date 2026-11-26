"""Milestone 1.1 command group: audits, annotation and the business baseline.

These commands are registered into the existing ``decision`` group so the operator
sees one pipeline. They are split into a separate module because they implement a
different concern (research governance and annotation) from the Milestone 1
construction pipeline in :mod:`cm_outbound_bench.decisions.cli`.

Output policy is the same as the rest of the pipeline: status lines contain
identifiers, counts and rates only. Conversation text is written exclusively to
git-ignored artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cm_outbound_bench.decisions import paths
from cm_outbound_bench.decisions.agreement import build_agreement_report
from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_GUIDELINE_VERSION,
    AnnotationError,
)
from cm_outbound_bench.decisions.annotation_io import (
    ADJUDICATIONS_FILENAME,
    APPLIED_REFERENCES_FILENAME,
    APPLY_REPORT_FILENAME,
    OUTPUT_REVIEWS_FILENAME,
    SUBMISSIONS_FILENAME,
    apply_annotations_to_references,
    build_adjudication_worklist,
    export_annotation_packets,
    export_output_review_packets,
    import_adjudications,
    import_annotation_submissions,
    load_output_reviews,
    load_submissions,
    save_adjudication_worklist,
    save_adjudications,
)
from cm_outbound_bench.decisions.business_report import (
    build_report_data,
    render_report_markdown,
)
from cm_outbound_bench.decisions.business_sources import (
    discover_business_sources,
    inventory_payload,
    save_inventory,
)
from cm_outbound_bench.decisions.channel_audit import (
    audit_channel_semantics,
    write_channel_review_package,
)
from cm_outbound_bench.decisions.config import ConfigError, load_dataset_config
from cm_outbound_bench.decisions.evaluator import aggregate_evaluations
from cm_outbound_bench.decisions.identity_audit import (
    audit_conversation_identity,
    render_identity_audit_markdown,
)
from cm_outbound_bench.decisions.sampling import (
    DEFAULT_SAMPLING_SEED,
    PURPOSE_DEV_EVALUATION,
    PURPOSE_PILOT,
    PURPOSE_TRAIN_R_FEEDBACK,
    PURPOSE_VALUES,
    RECOMMENDED_TARGETS,
    SamplingError,
    SamplingSpec,
    build_sampling_plan,
    load_sampling_plan,
    render_sampling_plan_markdown,
    save_sampling_plan,
    verify_sampling_plan,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask
from cm_outbound_bench.decisions.store import (
    PRIVATE_REFERENCES_FILENAME,
    PUBLIC_TASKS_FILENAME,
    TaskArtifactError,
    TaskStore,
    iter_public_tasks,
    iter_references,
)
from cm_outbound_bench.decisions.trace import TraceError, iter_traces
from cm_outbound_bench.transcripts.ingestion import (
    TranscriptCorpusError,
    dataset_version,
)
from cm_outbound_bench.transcripts.splitting import (
    SplitError,
    load_split_manifest,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

#: Default split per sampling purpose.
PURPOSE_DEFAULT_SPLIT: dict[str, str] = {
    PURPOSE_PILOT: "train_r",
    PURPOSE_TRAIN_R_FEEDBACK: "train_r",
    PURPOSE_DEV_EVALUATION: "dev",
}


def add_parsers(sub: Any) -> None:
    """Register the Milestone 1.1 subcommands into the ``decision`` group."""
    identity = sub.add_parser(
        "audit-identity",
        help="Re-audit conversation identity and dataset integrity.",
    )
    _add_corpus_option(identity)
    identity.add_argument("--split-manifest", type=Path, default=None)
    identity.add_argument("--out", type=Path, default=None, help="Markdown report.")
    identity.add_argument("--json", action="store_true")

    channels = sub.add_parser(
        "audit-channels",
        help="Audit channels 1-5 and export the unknown-channel review package.",
    )
    _add_corpus_option(channels)
    channels.add_argument(
        "--out-dir", type=Path, default=None, help="Review package directory."
    )
    channels.add_argument(
        "--samples-per-channel", type=int, default=None, help="Samples per channel."
    )
    channels.add_argument("--json", action="store_true")

    sources = sub.add_parser(
        "audit-business-sources",
        help="Inventory available business reference material.",
    )
    sources.add_argument("--workspace-root", type=Path, default=None)
    sources.add_argument("--out", type=Path, default=None)
    sources.add_argument("--json", action="store_true")

    sample = sub.add_parser(
        "sample", help="Build a reproducible annotation sampling plan."
    )
    sample.add_argument("--purpose", choices=list(PURPOSE_VALUES), required=True)
    sample.add_argument("--split", default=None, help="Defaults to the purpose split.")
    sample.add_argument("--size", type=int, default=None, help="Target sample size.")
    sample.add_argument("--seed", type=int, default=DEFAULT_SAMPLING_SEED)
    sample.add_argument("--tasks-dir", type=Path, default=None)
    sample.add_argument(
        "--exclude-plan",
        type=Path,
        action="append",
        default=[],
        help="Exclude conversations already used by another plan (repeatable).",
    )
    sample.add_argument("--out", type=Path, default=None, help="Plan JSON path.")
    sample.add_argument(
        "--verify", action="store_true", help="Re-derive the plan and compare."
    )
    sample.add_argument("--json", action="store_true")

    export = sub.add_parser(
        "export-annotations", help="Export review-ready annotation packets."
    )
    export.add_argument("--plan", type=Path, required=True)
    export.add_argument("--tasks-dir", type=Path, default=None)
    export.add_argument("--out-dir", type=Path, default=None)
    export.add_argument(
        "--allow-test",
        action="store_true",
        help="Explicit opt-in is refused: Test annotation is a separate locked pass.",
    )
    export.add_argument("--json", action="store_true")

    import_cmd = sub.add_parser(
        "import-annotations", help="Validate and record reviewer submissions."
    )
    import_cmd.add_argument("--file", type=Path, action="append", required=True)
    import_cmd.add_argument("--tasks-dir", type=Path, default=None)
    import_cmd.add_argument("--out-dir", type=Path, default=None)
    import_cmd.add_argument(
        "--acknowledge-guideline-mismatch", action="store_true"
    )
    import_cmd.add_argument("--json", action="store_true")

    worklist = sub.add_parser(
        "adjudication-worklist", help="Build the human adjudication worklist."
    )
    worklist.add_argument("--submissions", type=Path, default=None)
    worklist.add_argument("--out-dir", type=Path, default=None)
    worklist.add_argument("--json", action="store_true")

    adjudicate = sub.add_parser(
        "import-adjudications", help="Validate and record human adjudications."
    )
    adjudicate.add_argument("--file", type=Path, required=True)
    adjudicate.add_argument("--submissions", type=Path, default=None)
    adjudicate.add_argument("--out-dir", type=Path, default=None)
    adjudicate.add_argument("--json", action="store_true")

    agreement = sub.add_parser(
        "agreement", help="Report pre-adjudication inter-annotator agreement."
    )
    agreement.add_argument("--submissions", type=Path, default=None)
    agreement.add_argument("--out", type=Path, default=None)
    agreement.add_argument("--json", action="store_true")

    apply_cmd = sub.add_parser(
        "apply-annotations",
        help="Write an annotated private reference artifact from reviewed work.",
    )
    apply_cmd.add_argument("--submissions", type=Path, default=None)
    apply_cmd.add_argument("--adjudications", type=Path, default=None)
    apply_cmd.add_argument("--tasks-dir", type=Path, default=None)
    apply_cmd.add_argument("--out", type=Path, default=None)
    apply_cmd.add_argument("--report", type=Path, default=None)
    apply_cmd.add_argument("--json", action="store_true")

    export_outputs = sub.add_parser(
        "export-output-review",
        help="Build blind agent-output review packets from a run set.",
    )
    export_outputs.add_argument("--run-set", type=Path, required=True)
    export_outputs.add_argument("--tasks-dir", type=Path, default=None)
    export_outputs.add_argument("--out-dir", type=Path, default=None)
    export_outputs.add_argument(
        "--include-unscored",
        action="store_true",
        help="Also export outputs whose task has no adjudicated reference.",
    )
    export_outputs.add_argument("--json", action="store_true")

    import_reviews = sub.add_parser(
        "import-output-review", help="Validate human output-quality reviews."
    )
    import_reviews.add_argument("--file", type=Path, required=True)
    import_reviews.add_argument("--out", type=Path, default=None)
    import_reviews.add_argument("--json", action="store_true")

    baseline = sub.add_parser(
        "business-baseline",
        help="Build the Group A business baseline report from a run set.",
    )
    baseline.add_argument("--run-set", type=Path, required=True)
    baseline.add_argument("--tasks-dir", type=Path, default=None)
    baseline.add_argument(
        "--references",
        type=Path,
        default=None,
        help="Annotated references artifact; defaults to the task artifact.",
    )
    baseline.add_argument("--output-reviews", type=Path, default=None)
    baseline.add_argument("--out", type=Path, default=None)
    baseline.add_argument("--json", action="store_true")


def _add_corpus_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="De-identified corpus directory (defaults to CM_OUTBOUND_CORPUS_DIR).",
    )


def dispatch_extra(args: argparse.Namespace) -> int | None:
    """Dispatch one Milestone 1.1 subcommand, or return None when not ours."""
    handlers = {
        "audit-identity": _run_audit_identity,
        "audit-channels": _run_audit_channels,
        "audit-business-sources": _run_audit_business_sources,
        "sample": _run_sample,
        "export-annotations": _run_export_annotations,
        "import-annotations": _run_import_annotations,
        "adjudication-worklist": _run_adjudication_worklist,
        "import-adjudications": _run_import_adjudications,
        "agreement": _run_agreement,
        "apply-annotations": _run_apply_annotations,
        "export-output-review": _run_export_output_review,
        "import-output-review": _run_import_output_review,
        "business-baseline": _run_business_baseline,
    }
    handler = handlers.get(args.decision_command)
    if handler is None:
        return None
    try:
        return handler(args)
    except (
        AnnotationError,
        ConfigError,
        SamplingError,
        SplitError,
        TaskArtifactError,
        TraceError,
        TranscriptCorpusError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE


# ---------------------------------------------------------------------------
# audits
# ---------------------------------------------------------------------------


def _run_audit_identity(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    corpus_dir = paths.resolve_corpus_dir(config, args.corpus)
    split_path = args.split_manifest or paths.split_manifest_path(config)
    references_path = paths.tasks_dir(config) / PRIVATE_REFERENCES_FILENAME
    audit = audit_conversation_identity(
        corpus_dir,
        split_manifest_path=split_path if split_path.is_file() else None,
        references_path=references_path if references_path.is_file() else None,
    )
    output = args.out or (paths.reports_dir(config) / "conversation_identity_audit.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_identity_audit_markdown(audit), encoding="utf-8")
    payload = audit.model_dump(mode="json")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        dataset = audit.dataset
        print(f"corpus: {dataset.corpus}")
        print(f"manifest ok: {dataset.manifest_ok}")
        print(f"units: {dataset.units_read} records: {dataset.records}")
        print(f"channels: {dataset.channel_record_counts}")
        print(f"data status: {dataset.data_status_counts}")
        print(f"both speakers: {dataset.calls_with_both_speakers}")
        print(
            f"duplicate groups: {dataset.duplicate_groups} "
            f"({dataset.calls_in_duplicate_groups} units)"
        )
        print(f"timing inversions: {dataset.timing_inversions}")
        print(f"clock resets: {dataset.clock_resets}")
        print(f"overlaps: {dataset.overlapping_records}")
        print(f"splits: {audit.splits.calls_by_split}")
        print(f"split verification ok: {audit.splits.verification_all_true}")
        print(f"tasks: {audit.coverage.tasks_total}")
        print(f"tasks by split: {audit.coverage.tasks_by_split}")
        print(f"units with tasks: {audit.coverage.units_with_tasks}")
        print(f"baseline matches Milestone 1: {audit.baseline_matches}")
        if audit.mismatches:
            for item in audit.mismatches:
                print(f"  mismatch: {item}")
        print(f"report: {output}")
    return EXIT_OK if audit.dataset.manifest_ok else EXIT_FAILED


def _run_audit_channels(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    corpus_dir = paths.resolve_corpus_dir(config, args.corpus)
    split_path = paths.split_manifest_path(config)
    split_by_call = (
        load_split_manifest(split_path).split_by_call if split_path.is_file() else None
    )
    report, samples = audit_channel_semantics(
        corpus_dir,
        corpus=dataset_version(corpus_dir),
        samples_per_channel=args.samples_per_channel or 12,
        split_by_call=split_by_call,
    )
    output_dir = args.out_dir or paths.channel_review_dir(config)
    written = write_channel_review_package(report, samples, output_dir)
    payload = report.model_dump(mode="json")
    payload["written"] = written
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for statistic in report.channels:
            print(
                f"channel {statistic.channel_id}: role={statistic.role} "
                f"status={statistic.semantics_status} records={statistic.records} "
                f"units={statistic.units_containing} speech={statistic.speech_records} "
                f"events={statistic.event_records}"
            )
        print(f"units with unknown channel: {report.units_with_unknown_channel}")
        print(
            "units with unknown-channel speech: "
            f"{report.units_with_unknown_channel_speech}"
        )
        for item in report.benchmark_impact:
            print(
                f"impact {item.interpretation_id}: tasks={item.tasks} "
                f"delta={item.delta_tasks_vs_current:+d} "
                f"units={item.units_with_tasks} approved={item.approved}"
            )
        print(f"samples exported: {report.samples_exported}")
        print(f"review package: {output_dir}")
        print("production channel mapping: unchanged (channels 3/4/5 remain unknown)")
    return EXIT_OK


def _run_audit_business_sources(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    root = paths.project_root()
    workspace = args.workspace_root
    if workspace is None:
        candidate = root.parent
        workspace = candidate if candidate.is_dir() else None
    sources = discover_business_sources(root, workspace_root=workspace)
    output = args.out or (
        paths.artifact_root(config) / "business_sources" / "inventory.json"
    )
    save_inventory(output, sources)
    payload = inventory_payload(sources)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"sources: {len(sources)}")
        print(f"by authority: {payload['counts_by_authority']}")
        print(f"authoritative sources: {payload['authoritative_sources']}")
        print(f"policy verification supported: {payload['policy_verification_supported']}")
        print(payload["statement"])
        print(f"inventory: {output}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------


def _store(tasks_dir: Path | None, args: argparse.Namespace) -> TaskStore:
    config = load_dataset_config()
    return TaskStore.load(tasks_dir or paths.tasks_dir(config))


def _run_sample(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    store = _store(args.tasks_dir, args)
    split = args.split or PURPOSE_DEFAULT_SPLIT[args.purpose]
    size = args.size or RECOMMENDED_TARGETS[args.purpose]
    excluded: list[str] = []
    for plan_path in args.exclude_plan:
        excluded.extend(load_sampling_plan(plan_path).conversation_ids)
    spec = SamplingSpec(
        purpose=args.purpose,
        split=split,
        target_size=size,
        seed=args.seed,
        exclude_conversations=sorted(set(excluded)),
    )
    plan = build_sampling_plan(store, spec)
    output = args.out or (
        paths.samples_dir(config) / f"{args.purpose}.json"
    )
    save_sampling_plan(output, plan)
    markdown = output.with_suffix(".md")
    markdown.write_text(render_sampling_plan_markdown(plan), encoding="utf-8")
    verified = verify_sampling_plan(store, plan) if args.verify else None
    if args.json:
        payload = plan.model_dump(mode="json")
        payload["reproduced"] = verified
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"plan: {plan.plan_id}")
        print(f"purpose: {plan.purpose} split: {plan.split} seed: {plan.seed}")
        print(f"pool tasks: {plan.pool_tasks} conversations: {plan.pool_conversations}")
        print(f"selected: {plan.selected_size} conversations: {plan.selected_conversations}")
        print(f"strata: {len(plan.strata)}")
        print(f"selection sha256: {plan.selection_sha256}")
        if verified is not None:
            print(f"reproduced: {verified}")
        print(f"plan file: {output}")
    return EXIT_OK if (verified is None or verified) else EXIT_FAILED


# ---------------------------------------------------------------------------
# annotation export / import
# ---------------------------------------------------------------------------


def _run_export_annotations(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    plan = load_sampling_plan(args.plan)
    if plan.split == "test":
        print(
            "error: annotation packets for the isolated Test split are not exported "
            "by this command; Test annotation is a separate locked pass",
            file=sys.stderr,
        )
        return EXIT_USAGE
    store = _store(args.tasks_dir, args)
    out_dir = args.out_dir or (paths.annotations_dir(config) / plan.purpose)
    sources_inventory = paths.artifact_root(config) / "business_sources" / "inventory.json"
    reference_status = "no_approved_authoritative_material"
    if sources_inventory.is_file():
        payload = json.loads(sources_inventory.read_text(encoding="utf-8"))
        if not payload.get("policy_verification_supported"):
            reference_status = "no_approved_authoritative_material"
        else:  # pragma: no cover - no authoritative source exists today
            reference_status = "authoritative_material_present"
    manifest, target = export_annotation_packets(
        store, plan, out_dir, reference_material_status=reference_status
    )
    if args.json:
        print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"packets: {manifest.packets}")
        print(f"guideline version: {manifest.guideline_version}")
        print(f"reference material status: {manifest.reference_material_status}")
        print(f"hidden continuation exported: {manifest.hidden_continuation_exported}")
        print(f"packet sha256: {manifest.packet_sha256}")
        print(f"packets file: {target}")
    return EXIT_OK


def _run_import_annotations(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    store = _store(args.tasks_dir, args)
    out_dir = args.out_dir or (paths.annotations_dir(config) / "current")
    report, _ = import_annotation_submissions(
        args.file,
        store,
        output_dir=out_dir,
        acknowledge_guideline_mismatch=args.acknowledge_guideline_mismatch,
    )
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"files read: {report.files_read}")
        print(f"submissions: {report.submissions_in_file}")
        print(f"accepted: {report.accepted} rejected: {report.rejected}")
        print(f"llm_proposed: {report.llm_proposed}")
        print(f"duplicate submissions: {report.duplicate_submissions}")
        print(f"tasks covered: {report.tasks_covered}")
        print(f"tasks with two human ratings: {report.tasks_with_two_human_ratings}")
        print(f"tasks with one human rating: {report.tasks_with_one_human_rating}")
        if report.possible_continuation_leaks:
            print(
                "possible continuation overlaps: "
                f"{len(report.possible_continuation_leaks)}"
            )
        for note in report.notes:
            print(f"note: {note}")
        print(f"artifacts: {out_dir}")
    return EXIT_OK if report.rejected == 0 else EXIT_FAILED


def _run_adjudication_worklist(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    submissions_path = args.submissions or (
        paths.annotations_dir(config) / "current" / SUBMISSIONS_FILENAME
    )
    submissions = load_submissions(submissions_path)
    rows = build_adjudication_worklist(submissions)
    out_dir = args.out_dir or (paths.annotations_dir(config) / "current")
    target = save_adjudication_worklist(out_dir, rows)
    needing = sum(1 for row in rows if row.status == "pending")
    agreeing = sum(1 for row in rows if row.agreement)
    if args.json:
        print(
            json.dumps(
                {
                    "tasks": len(rows),
                    "pending_adjudication": needing,
                    "agreeing_pairs": agreeing,
                    "worklist": [row.model_dump(mode="json") for row in rows],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"tasks in worklist: {len(rows)}")
        print(f"pending human adjudication: {needing}")
        print(f"independent pairs that agree: {agreeing}")
        print(f"worklist: {target}")
    return EXIT_OK


def _run_import_adjudications(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    submissions_path = args.submissions or (
        paths.annotations_dir(config) / "current" / SUBMISSIONS_FILENAME
    )
    submissions = load_submissions(submissions_path)
    records = import_adjudications(args.file, submissions)
    out_dir = args.out_dir or (paths.annotations_dir(config) / "current")
    target = save_adjudications(out_dir, records)
    resolved = sum(1 for record in records if record.status == "adjudicated")
    if args.json:
        print(
            json.dumps(
                {
                    "records": len(records),
                    "resolved": resolved,
                    "file": str(target),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"adjudication records: {len(records)}")
        print(f"resolved: {resolved}")
        print(f"file: {target}")
    return EXIT_OK


def _run_agreement(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    submissions_path = args.submissions or (
        paths.annotations_dir(config) / "current" / SUBMISSIONS_FILENAME
    )
    submissions = load_submissions(submissions_path)
    report = build_agreement_report(
        submissions, guideline_version=ANNOTATION_GUIDELINE_VERSION
    )
    payload = report.model_dump(mode="json")
    output = args.out or (
        paths.reports_dir(config) / "annotation_agreement.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"tasks considered: {report.tasks_considered}")
        print(f"tasks with >=2 ratings: {report.tasks_with_two_or_more_ratings}")
        print(f"tasks with 1 rating: {report.tasks_with_one_rating}")
        print(f"tasks with disagreement: {report.tasks_with_disagreement}")
        for statistic in report.statistics:
            print(
                f"{statistic.name}: {statistic.value} "
                f"({statistic.interpretation}; units={statistic.units})"
            )
        for set_statistic in report.set_statistics:
            print(
                f"{set_statistic.field_name} set agreement: "
                f"precision={set_statistic.mean_precision} "
                f"recall={set_statistic.mean_recall} "
                f"jaccard={set_statistic.mean_jaccard}"
            )
        print(
            "guideline revision recommended: "
            f"{report.guideline_revision_recommended}"
        )
        print(f"report: {output}")
    return EXIT_OK


def _run_apply_annotations(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    annotations_root = paths.annotations_dir(config) / "current"
    submissions_path = args.submissions or (annotations_root / SUBMISSIONS_FILENAME)
    adjudications_path = args.adjudications or (annotations_root / ADJUDICATIONS_FILENAME)
    store = _store(args.tasks_dir, args)
    submissions = load_submissions(submissions_path)
    adjudications = (
        _load_adjudications(adjudications_path) if adjudications_path.is_file() else []
    )
    output = args.out or (annotations_root / APPLIED_REFERENCES_FILENAME)
    report_path = args.report or (annotations_root / APPLY_REPORT_FILENAME)
    report, target = apply_annotations_to_references(
        store,
        submissions,
        adjudications,
        output_path=output,
        report_path=report_path,
    )
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"references read: {report.references_read}")
        print(f"scored: {report.scored} candidate: {report.candidate}")
        print(f"left unscored: {report.left_unscored}")
        print(f"skipped llm-only: {report.skipped_llm_only}")
        print(f"skipped without adjudication: {report.skipped_no_adjudication}")
        print(f"skipped inconsistent: {report.skipped_inconsistent}")
        print(f"annotated references: {target}")
    return EXIT_OK


def _load_adjudications(path: Path) -> list[Any]:
    from pydantic import ValidationError

    from cm_outbound_bench.decisions.annotation import AdjudicationRecord

    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(AdjudicationRecord.model_validate_json(line))
            except ValidationError as error:
                raise AnnotationError(
                    f"invalid adjudication on line {line_number}"
                ) from error
    return records


# ---------------------------------------------------------------------------
# output review
# ---------------------------------------------------------------------------


def _run_export_output_review(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    store = _store(args.tasks_dir, args)
    out_dir = args.out_dir or (paths.annotations_dir(config) / "output_review")
    written, target = export_output_review_packets(
        store,
        args.run_set,
        out_dir,
        only_scored=not args.include_unscored,
    )
    if args.json:
        print(json.dumps({"packets": written, "file": str(target)}, indent=2))
    else:
        print(f"blind output packets: {written}")
        print(f"file: {target}")
        if written == 0:
            print(
                "note: no packet was written because no task in this run set has an "
                "adjudicated reference; pass --include-unscored for a diagnostic "
                "review set that cannot support a quality metric"
            )
    return EXIT_OK


def _run_import_output_review(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    scores = load_output_reviews(args.file)
    output = args.out or (paths.annotations_dir(config) / "output_review" / OUTPUT_REVIEWS_FILENAME)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(scores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps({"reviews": len(scores), "file": str(output)}, indent=2))
    else:
        print(f"reviews accepted: {len(scores)}")
        print(f"scores file: {output}")
    return EXIT_OK if scores else EXIT_FAILED


# ---------------------------------------------------------------------------
# business baseline report
# ---------------------------------------------------------------------------


def _run_business_baseline(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    tasks_dir = args.tasks_dir or paths.tasks_dir(config)
    references_path = args.references or (tasks_dir / PRIVATE_REFERENCES_FILENAME)
    store = _load_store_with_references(tasks_dir, references_path)
    traces = iter_traces(args.run_set)
    if not traces:
        print("error: no traces found", file=sys.stderr)
        return EXIT_FAILED
    references = {task_id: store.require_reference(task_id) for task_id in store.task_ids}
    evaluations = [trace.evaluation for trace in traces]
    reviews = (
        load_output_reviews(args.output_reviews) if args.output_reviews else {}
    )
    aggregate = aggregate_evaluations(
        evaluations,
        output_reviews=reviews,
        independent_sampling_unit="conversation_unit",
    )
    first = traces[0]
    report = build_report_data(
        run_set=args.run_set.name,
        condition=first.condition,
        benchmark_version=first.benchmark_version,
        dataset_version=first.dataset_version,
        split=first.split,
        model_provider=first.model_provider,
        model_name=first.model_name,
        model_parameters=first.model_parameters,
        prompt_version=first.prompt_version,
        evaluator_version=first.evaluator_version,
        agent_version=first.agent_version,
        config_hash=first.config_hash,
        evaluations=evaluations,
        references=references,
        aggregate=aggregate,
        output_reviews=reviews,
        visible_turn_counts={
            task_id: len(store.load_public_task(task_id).visible_context)
            for task_id in store.task_ids
        },
        customer_turn_counts={
            task_id: sum(
                1
                for turn in store.load_public_task(task_id).visible_context
                if turn.role == "customer"
            )
            for task_id in store.task_ids
        },
        agent_outputs={
            trace.task_id: trace.agent_output
            for trace in traces
            if trace.agent_output is not None
        },
    )
    output = args.out or (
        paths.reports_dir(config) / "group_a_business_baseline_report.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report_markdown(report), encoding="utf-8")
    json_output = output.with_suffix(".json")
    json_output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"run set: {report.run_set}")
        print(f"tasks: {aggregate.tasks_total} scored: {aggregate.scored_tasks}")
        print(
            "expert_adjudicated_decision_success_rate: "
            f"{aggregate.business_metrics.get('expert_adjudicated_decision_success_rate')}"
        )
        print(
            "policy_verified_decision_success_rate: "
            f"{aggregate.business_metrics.get('policy_verified_decision_success_rate')}"
        )
        print(f"report: {output}")
        print(f"report json: {json_output}")
    return EXIT_OK


def _load_store_with_references(tasks_dir: Path, references_path: Path) -> TaskStore:
    """Load a task store whose references may come from an annotated artifact."""
    public_path = Path(tasks_dir) / PUBLIC_TASKS_FILENAME
    if not public_path.is_file():
        raise TaskArtifactError(f"task artifact not found: {public_path}")
    if not Path(references_path).is_file():
        raise TaskArtifactError(f"reference artifact not found: {references_path}")
    tasks: dict[str, PublicDecisionTask] = {
        task.task_id: task for task in iter_public_tasks(public_path)
    }
    references: dict[str, PrivateTaskReference] = {
        reference.task_id: reference for reference in iter_references(references_path)
    }
    missing = set(tasks) - set(references)
    if missing:
        raise TaskArtifactError(
            f"{len(missing)} public task(s) have no reference in "
            f"{Path(references_path).name}"
        )
    return TaskStore(tasks, references)


__all__ = ["add_parsers", "dispatch_extra"]
