"""Reproducibility: samples, imports, audit packages and statistical reports."""

from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_GUIDELINE_VERSION,
    ANSWERABILITY_SUFFICIENT,
    AUTHORITY_HUMAN_PRIMARY,
    AnnotationProvenance,
    AnnotationSubmission,
    DecisionReference,
)
from cm_outbound_bench.decisions.annotation_io import (
    export_annotation_packets,
    import_annotation_submissions,
)
from cm_outbound_bench.decisions.business_report import (
    build_report_data,
    render_report_markdown,
    report_digest,
)
from cm_outbound_bench.decisions.channel_audit import (
    audit_channel_semantics,
    write_channel_review_package,
)
from cm_outbound_bench.decisions.evaluator import (
    aggregate_evaluations,
    evaluate_invocation,
)
from cm_outbound_bench.decisions.identity_audit import audit_conversation_identity
from cm_outbound_bench.decisions.sampling import (
    SamplingSpec,
    build_sampling_plan,
    render_sampling_plan_markdown,
    save_sampling_plan,
    verify_sampling_plan,
)
from decision_support import (
    build_tasks,
    dialogue,
    distinct_label,
    load_calls,
    manifest_for,
    write_corpus,
)

CORPUS = "reproducibility_fixture"


def _records(index: int) -> list[dict]:
    label = distinct_label(index)
    return dialogue(
        [
            (
                1 if position % 2 == 0 else 2,
                f"通话{label}{'客服' if position % 2 == 0 else '客户'}语句之{position + 1}",
            )
            for position in range(8)
        ]
    )


def _corpus(tmp_path: Path, *, calls: int = 16) -> Path:
    return write_corpus(
        tmp_path / "corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": f"CALL_FIXTURE_{index:02d}",
                "duration_ms": 24_000,
                "records": _records(index),
            }
            for index in range(calls)
        ],
    )


def _store(tmp_path: Path, *, calls: int = 16):
    corpus = _corpus(tmp_path, calls=calls)
    calls_materialized = load_calls(corpus)
    manifest = manifest_for(calls_materialized, max_hamming=0)
    split_path = tmp_path / "split.json"
    split_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    store, _, _ = build_tasks(calls_materialized, manifest, tmp_path / "tasks")
    return corpus, store, split_path


def _split(store) -> str:
    counts = store.counts_by_split()
    total, name = max(
        (counts.get(candidate, 0), candidate)
        for candidate in ("dev", "train_f", "train_r")
    )
    assert total > 0
    return name


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_a_saved_plan_round_trips_and_verifies(tmp_path: Path) -> None:
    _, store, _ = _store(tmp_path)
    spec = SamplingSpec(
        purpose="dev_baseline_evaluation_sample",
        split=_split(store),
        target_size=5,
        seed=424242,
    )
    plan = build_sampling_plan(store, spec)
    path = save_sampling_plan(tmp_path / "plan.json", plan)

    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["selection_sha256"] == plan.selection_sha256
    assert restored["seed"] == 424242
    assert restored["target_size"] == 5
    assert restored["stratification"]["by_business_domain"] is False
    assert verify_sampling_plan(store, plan) is True


def test_the_same_seed_produces_an_identical_markdown_plan(tmp_path: Path) -> None:
    _, store, _ = _store(tmp_path)
    spec = SamplingSpec(
        purpose="train_r_annotation_pilot",
        split=_split(store),
        target_size=4,
        seed=11,
    )
    first = render_sampling_plan_markdown(build_sampling_plan(store, spec))
    second = render_sampling_plan_markdown(build_sampling_plan(store, spec))
    assert first == second


def test_a_different_seed_changes_the_sample_when_there_is_a_choice(
    tmp_path: Path,
) -> None:
    _, store, _ = _store(tmp_path)
    split = _split(store)
    first = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="dev_baseline_evaluation_sample", split=split, target_size=3, seed=1
        ),
    )
    second = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="dev_baseline_evaluation_sample", split=split, target_size=3, seed=2
        ),
    )
    # When the pool is no larger than the requested sample the selection is
    # exhaustive and the seed legitimately cannot change it. Only assert a
    # difference when a choice actually exists.
    if first.pool_tasks <= first.selected_size:
        assert first.selection_sha256 == second.selection_sha256
    else:
        assert first.selection_sha256 != second.selection_sha256


# ---------------------------------------------------------------------------
# Packets and imports
# ---------------------------------------------------------------------------


def test_exported_packets_are_byte_identical_on_regeneration(tmp_path: Path) -> None:
    _, store, _ = _store(tmp_path)
    plan = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="train_r_annotation_pilot", split=_split(store), target_size=4
        ),
    )
    first_manifest, first_path = export_annotation_packets(
        store, plan, tmp_path / "packets-a"
    )
    second_manifest, second_path = export_annotation_packets(
        store, plan, tmp_path / "packets-b"
    )
    assert first_manifest.packet_sha256 == second_manifest.packet_sha256
    assert first_path.read_bytes() == second_path.read_bytes()


def _submission(task_id: str, annotator: str) -> AnnotationSubmission:
    return AnnotationSubmission(
        task_id=task_id,
        annotator_id=annotator,
        guideline_version=ANNOTATION_GUIDELINE_VERSION,
        annotation_status="reviewed",
        task_answerability=ANSWERABILITY_SUFFICIENT,
        primary_expected_decision=DecisionReference(
            decision_id="D1", label="占位决策", match_keywords=["占位决策"]
        ),
        required_steps=["占位步骤"],
        evidence_refs=["visible_turn:2"],
        provenance=AnnotationProvenance(
            authority=AUTHORITY_HUMAN_PRIMARY,
            annotator_id=annotator,
            guideline_version=ANNOTATION_GUIDELINE_VERSION,
        ),
        reviewer_confidence=0.75,
    )


def test_import_output_is_byte_identical_and_keeps_history(tmp_path: Path) -> None:
    _, store, _ = _store(tmp_path)
    plan = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="train_r_annotation_pilot", split=_split(store), target_size=2
        ),
    )
    task_id = plan.selected[0].task_id
    path = tmp_path / "subs.jsonl"
    path.write_text(
        json.dumps(_submission(task_id, "a").model_dump(mode="json"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    first, _ = import_annotation_submissions([path], store, output_dir=tmp_path / "one")
    second, _ = import_annotation_submissions([path], store, output_dir=tmp_path / "two")
    assert first.model_dump() == second.model_dump()
    assert (tmp_path / "one" / "annotation_submissions.jsonl").read_bytes() == (
        tmp_path / "two" / "annotation_submissions.jsonl"
    ).read_bytes()
    history = sorted((tmp_path / "one" / "history").glob("import-*.json"))
    assert history


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------


def test_the_identity_audit_matches_itself_and_reports_a_baseline_comparison(
    tmp_path: Path,
) -> None:
    corpus, _, split_path = _store(tmp_path, calls=6)
    first = audit_conversation_identity(
        corpus,
        corpus=CORPUS,
        split_manifest_path=split_path,
        compare_baseline=True,
    )
    second = audit_conversation_identity(
        corpus,
        corpus=CORPUS,
        split_manifest_path=split_path,
        compare_baseline=True,
    )
    assert first.dataset.model_dump() == second.dataset.model_dump()
    assert first.splits.model_dump() == second.splits.model_dump()
    assert first.coverage.tasks_total == second.coverage.tasks_total
    # A small fixture corpus must not pretend to match the real milestone baseline.
    assert first.baseline_matches is False
    assert first.mismatches
    assert first.identity.source_has_call_id is False
    assert first.identity.terminology["conversation_id"]


def test_the_channel_review_package_is_byte_identical_on_regeneration(
    tmp_path: Path,
) -> None:
    corpus = write_corpus(
        tmp_path / "channel-corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": "CALL_FIXTURE_CHANNELS",
                "duration_ms": 30_000,
                "records": dialogue(
                    [
                        (1, "占位客服一"),
                        (2, "占位客户一"),
                        (3, "占位第三方一"),
                        (1, "占位客服二"),
                        (4, "占位第三方二"),
                        (2, "占位客户二"),
                        (1, "占位客服三"),
                    ]
                ),
            }
        ],
    )
    first_report, first_samples = audit_channel_semantics(corpus, corpus=CORPUS)
    second_report, second_samples = audit_channel_semantics(corpus, corpus=CORPUS)
    assert first_report.model_dump() == second_report.model_dump()

    write_channel_review_package(first_report, first_samples, tmp_path / "a")
    write_channel_review_package(second_report, second_samples, tmp_path / "b")
    for name in (
        "channel_3_samples.jsonl",
        "channel_4_samples.jsonl",
        "channel_5_samples.jsonl",
        "channel_review_summary.csv",
    ):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


# ---------------------------------------------------------------------------
# Statistical report
# ---------------------------------------------------------------------------


def _report(tmp_path: Path):
    _, store, _ = _store(tmp_path)
    split = _split(store)
    task_ids = store.task_ids_for_split(split)[:4]
    references = {task_id: store.require_reference(task_id) for task_id in task_ids}
    evaluations = []
    for task_id in task_ids:
        task = store.load_public_task(task_id)
        invocation = __import__(
            "cm_outbound_bench.decisions.agent", fromlist=["ScriptedDecisionAgent"]
        ).ScriptedDecisionAgent().decide(
            __import__(
                "cm_outbound_bench.decisions.agent_prompt",
                fromlist=["build_agent_request"],
            ).build_agent_request(task)
        )
        evaluations.append(evaluate_invocation(task, references[task_id], invocation))
    aggregate = aggregate_evaluations(evaluations)
    return build_report_data(
        run_set="fixture-run",
        condition="A",
        benchmark_version="0.2.0",
        dataset_version=CORPUS,
        split=split,
        model_provider="scripted",
        model_name="none",
        model_parameters={},
        prompt_version="baseline-a-v1",
        evaluator_version="0.2.0",
        agent_version="0.1.0",
        config_hash="cfg",
        evaluations=evaluations,
        references=references,
        aggregate=aggregate,
        visible_turn_counts={
            task_id: len(store.load_public_task(task_id).visible_context)
            for task_id in task_ids
        },
        customer_turn_counts={
            task_id: sum(
                1
                for turn in store.load_public_task(task_id).visible_context
                if turn.role == "customer"
            )
            for task_id in task_ids
        },
    )


def test_the_business_report_regenerates_from_locked_results(tmp_path: Path) -> None:
    first = _report(tmp_path)
    second = _report(tmp_path)
    assert first.aggregate.model_dump() == second.aggregate.model_dump()
    assert report_digest(first) == report_digest(second)
    assert render_report_markdown(first) == render_report_markdown(second)


def test_the_business_report_contains_no_conversation_text(tmp_path: Path) -> None:
    markdown = render_report_markdown(_report(tmp_path))
    assert "占位客服" not in markdown
    assert "占位客户" not in markdown
    assert "占位第三方" not in markdown
    assert "<PHONE>" not in markdown
