"""Annotation schema, export/import, adjudication and agreement behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.decisions.agreement import (
    build_agreement_report,
    cohen_kappa,
    krippendorff_alpha_nominal,
    raw_agreement,
    set_agreement,
)
from cm_outbound_bench.decisions.annotation import (
    ADJUDICATION_RESOLVED,
    ANNOTATION_GUIDELINE_VERSION,
    ANSWERABILITY_AMBIGUOUS,
    ANSWERABILITY_MORE_INFORMATION,
    ANSWERABILITY_SUFFICIENT,
    AUTHORITY_HUMAN_ADJUDICATED,
    AUTHORITY_HUMAN_PRIMARY,
    AUTHORITY_LLM_PROPOSED,
    AdjudicationRecord,
    AnnotationProvenance,
    AnnotationSubmission,
    DecisionReference,
    validate_annotation_consistency,
)
from cm_outbound_bench.decisions.annotation_io import (
    FORBIDDEN_PACKET_KEYS,
    AnnotationError,
    apply_annotations_to_references,
    build_adjudication_worklist,
    export_annotation_packets,
    import_adjudications,
    import_annotation_submissions,
    iter_packets,
)
from cm_outbound_bench.decisions.sampling import (
    SamplingSpec,
    build_sampling_plan,
    verify_sampling_plan,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference
from decision_support import (
    build_tasks,
    dialogue,
    distinct_label,
    load_calls,
    manifest_for,
    write_corpus,
)

GUIDELINE = ANNOTATION_GUIDELINE_VERSION


def _records(index: int) -> list[dict]:
    label = distinct_label(index)
    return dialogue(
        [
            (
                1 if position % 2 == 0 else 2,
                f"通话{label}{'客服' if position % 2 == 0 else '客户'}语句之{position + 1}",
            )
            for position in range(10)
        ]
    )


def _fixture_split(store) -> str:
    """Return the development split with the most tasks, for a stable fixture.

    A small fixture corpus can allocate very few units to ``dev`` by chance, so the
    tests pick the largest development split instead of hard-coding one.
    """
    counts = store.counts_by_split()
    total, name = max(
        (counts.get(candidate, 0), candidate)
        for candidate in ("dev", "train_f", "train_r")
    )
    assert total > 0, "fixture store has no development tasks"
    return name


def _store_and_plan(tmp_path: Path, *, tasks: int = 24):
    corpus = write_corpus(
        tmp_path / "corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": f"CALL_FIXTURE_{index:02d}",
                "duration_ms": 30_000,
                "records": _records(index),
            }
            for index in range(tasks)
        ],
    )
    calls = load_calls(corpus)
    manifest = manifest_for(calls, max_hamming=0)
    store, _, _ = build_tasks(calls, manifest, tmp_path / "tasks")
    split = _fixture_split(store)
    plan = build_sampling_plan(
        store, SamplingSpec(purpose="train_r_annotation_pilot", split=split, target_size=3)
    )
    return store, plan


def _provenance(authority: str, annotator: str) -> AnnotationProvenance:
    return AnnotationProvenance(
        authority=authority,
        method="fixture",
        annotator_id=annotator,
        guideline_version=GUIDELINE,
        model_name="fixture-model" if authority == AUTHORITY_LLM_PROPOSED else "",
    )


def _submission(
    task_id: str,
    annotator: str,
    *,
    authority: str = AUTHORITY_HUMAN_PRIMARY,
    label: str = "占位决策甲",
    steps: list[str] | None = None,
) -> AnnotationSubmission:
    return AnnotationSubmission(
        task_id=task_id,
        annotator_id=annotator,
        guideline_version=GUIDELINE,
        annotation_status="reviewed",
        task_answerability=ANSWERABILITY_SUFFICIENT,
        primary_expected_decision=DecisionReference(
            decision_id="D1",
            label=label,
            decision_family="占位动作族",
            description="占位说明",
        ),
        required_steps=list(steps if steps is not None else ["占位必需步骤"]),
        evidence_refs=["visible_turn:2"],
        provenance=_provenance(authority, annotator),
        reviewer_confidence=0.8,
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_ambiguous_task_must_not_define_a_primary_decision() -> None:
    submission = AnnotationSubmission(
        task_id="TASK_1",
        annotator_id="A",
        task_answerability=ANSWERABILITY_AMBIGUOUS,
        primary_expected_decision=DecisionReference(decision_id="D1", label="占位"),
        evidence_refs=["visible_turn:1"],
        provenance=_provenance(AUTHORITY_HUMAN_PRIMARY, "A"),
        reviewer_confidence=0.5,
    )
    issues = validate_annotation_consistency(submission)
    assert "ambiguous_or_unscorable_task_must_not_define_a_primary_decision" in issues


def test_clarification_task_must_name_the_missing_information() -> None:
    with pytest.raises(ValueError):
        AnnotationSubmission(
            task_id="TASK_1",
            annotator_id="A",
            task_answerability=ANSWERABILITY_MORE_INFORMATION,
            evidence_refs=["visible_turn:1"],
            provenance=_provenance(AUTHORITY_HUMAN_PRIMARY, "A"),
            reviewer_confidence=0.6,
        )


def test_prohibited_steps_without_a_rule_are_rejected() -> None:
    with pytest.raises(ValueError):
        AnnotationSubmission(
            task_id="TASK_1",
            annotator_id="A",
            task_answerability=ANSWERABILITY_SUFFICIENT,
            prohibited_steps=["占位禁止动作"],
            evidence_refs=["visible_turn:1"],
            provenance=_provenance(AUTHORITY_HUMAN_PRIMARY, "A"),
            reviewer_confidence=0.6,
        )


def test_llm_provenance_requires_a_model_identity() -> None:
    with pytest.raises(ValueError):
        AnnotationProvenance(authority=AUTHORITY_LLM_PROPOSED)


# ---------------------------------------------------------------------------
# Export: reviewer input contains no future dialogue
# ---------------------------------------------------------------------------


def test_packets_contain_no_historical_continuation(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    manifest, path = export_annotation_packets(store, plan, tmp_path / "packets")
    assert manifest.packets == len(plan.selected)
    assert manifest.hidden_continuation_exported is False

    reference = store.require_reference(plan.selected[0].task_id)
    hidden = reference.historical_next_action_reference or ""
    serialized = path.read_text(encoding="utf-8")
    assert hidden and hidden not in serialized

    for packet in iter_packets(path):
        payload = packet.model_dump(mode="json")
        # The blank form names the fields a reviewer must fill in; it is a schema
        # description, not data, so the private-key scan covers everything else.
        scanned = {
            key: value for key, value in payload.items() if key != "annotation_form"
        }
        assert set(scanned) & FORBIDDEN_PACKET_KEYS == set()
        scanned_text = json.dumps(scanned, ensure_ascii=False)
        for key in FORBIDDEN_PACKET_KEYS:
            assert f'"{key}"' not in scanned_text
        assert packet.visible_context
        assert packet.guideline_version == GUIDELINE
        assert packet.annotation_form["primary_expected_decision"]["label"] == ""


def test_export_refuses_the_test_split(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    plan.split = "test"
    with pytest.raises(AnnotationError) as caught:
        export_annotation_packets(store, plan, tmp_path / "packets")
    assert "Test" in str(caught.value) or "test" in str(caught.value)


# ---------------------------------------------------------------------------
# Import: independence, duplicates and guideline versions
# ---------------------------------------------------------------------------


def _write_submissions(path: Path, submissions: list[AnnotationSubmission]) -> Path:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for submission in submissions:
            handle.write(
                json.dumps(submission.model_dump(mode="json"), ensure_ascii=False) + "\n"
            )
    return path


def test_two_independent_reviews_can_coexist(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    path = _write_submissions(
        tmp_path / "subs.jsonl",
        [
            _submission(task_id, "reviewer-a"),
            _submission(task_id, "reviewer-b"),
        ],
    )
    report, accepted = import_annotation_submissions([path], store, output_dir=tmp_path / "out")
    assert report.accepted == 2
    assert report.rejected == 0
    assert report.tasks_covered == 1
    assert report.tasks_with_two_human_ratings == 1
    assert {item.annotator_id for item in accepted} == {"reviewer-a", "reviewer-b"}


def test_duplicate_submission_from_the_same_annotator_is_rejected(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    path = _write_submissions(
        tmp_path / "subs.jsonl",
        [_submission(task_id, "reviewer-a"), _submission(task_id, "reviewer-a")],
    )
    report, accepted = import_annotation_submissions([path], store, output_dir=tmp_path / "out")
    assert report.accepted == 1
    assert report.rejected == 1
    assert report.duplicate_submissions == 1
    assert len(accepted) == 1


def test_guideline_mismatch_is_rejected_unless_acknowledged(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submission = _submission(task_id, "reviewer-a")
    submission.guideline_version = "annotation-guidelines-v0"
    path = _write_submissions(tmp_path / "subs.jsonl", [submission])

    report, _ = import_annotation_submissions([path], store, output_dir=tmp_path / "out")
    assert report.rejected == 1

    report, accepted = import_annotation_submissions(
        [path],
        store,
        output_dir=tmp_path / "out",
        acknowledge_guideline_mismatch=True,
    )
    assert report.accepted == 1
    assert accepted[0].guideline_version == "annotation-guidelines-v0"


def test_unknown_task_id_is_rejected(tmp_path: Path) -> None:
    store, _ = _store_and_plan(tmp_path)
    path = _write_submissions(tmp_path / "subs.jsonl", [_submission("TASK_NOT_REAL", "a")])
    report, _ = import_annotation_submissions([path], store, output_dir=tmp_path / "out")
    assert report.rejected == 1
    assert any("unknown_task_id" in decision.reasons for decision in report.decisions)


def test_import_is_deterministic_and_auditable(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    path = _write_submissions(
        tmp_path / "subs.jsonl",
        [_submission(plan.selected[0].task_id, "reviewer-a")],
    )
    first, _ = import_annotation_submissions([path], store, output_dir=tmp_path / "out1")
    second, _ = import_annotation_submissions([path], store, output_dir=tmp_path / "out2")
    assert first.model_dump() == second.model_dump()
    assert (tmp_path / "out1" / "annotation_submissions.jsonl").read_bytes() == (
        tmp_path / "out2" / "annotation_submissions.jsonl"
    ).read_bytes()
    assert list((tmp_path / "out1" / "history").glob("import-*.json"))


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


def test_disagreement_is_detected_and_needs_a_human(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submissions = [
        _submission(task_id, "reviewer-a", label="占位决策甲"),
        _submission(task_id, "reviewer-b", label="占位决策乙"),
    ]
    rows = build_adjudication_worklist(submissions)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].agreement is False
    assert rows[0].reason == "human_annotations_disagree"
    assert set(rows[0].labels) == {"reviewer-a", "reviewer-b"}


def test_agreement_marks_adjudication_not_required(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submissions = [
        _submission(task_id, "reviewer-a"),
        _submission(task_id, "reviewer-b"),
    ]
    rows = build_adjudication_worklist(submissions)
    assert rows[0].status == "not_required"
    assert rows[0].agreement is True


def test_adjudicator_cannot_be_one_of_the_annotators(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submissions = [
        _submission(task_id, "reviewer-a"),
        _submission(task_id, "reviewer-b", label="占位决策乙"),
    ]
    final = _submission(task_id, "reviewer-a", authority=AUTHORITY_HUMAN_ADJUDICATED)
    record = AdjudicationRecord(
        task_id=task_id,
        status=ADJUDICATION_RESOLVED,
        adjudicator_id="reviewer-a",
        final_annotation=final,
    )
    path = tmp_path / "adjudications.jsonl"
    path.write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AnnotationError) as caught:
        import_adjudications(path, submissions)
    assert "must not be one of the annotators" in str(caught.value)


def test_adjudication_requires_two_human_annotations(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submissions = [_submission(task_id, "reviewer-a")]
    final = _submission(task_id, "adjudicator", authority=AUTHORITY_HUMAN_ADJUDICATED)
    record = AdjudicationRecord(
        task_id=task_id,
        status=ADJUDICATION_RESOLVED,
        adjudicator_id="adjudicator",
        final_annotation=final,
    )
    path = tmp_path / "adjudications.jsonl"
    path.write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AnnotationError):
        import_adjudications(path, submissions)


# ---------------------------------------------------------------------------
# Applying annotations
# ---------------------------------------------------------------------------


def _apply(tmp_path: Path, store, submissions, adjudications=()):
    return apply_annotations_to_references(
        store,
        list(submissions),
        list(adjudications),
        output_path=tmp_path / "references.annotated.jsonl",
        report_path=tmp_path / "apply.json",
    )


def _load_annotated(path: Path) -> dict[str, PrivateTaskReference]:
    references: dict[str, PrivateTaskReference] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        reference = PrivateTaskReference.model_validate_json(line)
        references[reference.task_id] = reference
    return references


def test_an_llm_suggestion_can_never_become_a_gold_label(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submission = _submission(task_id, "model", authority=AUTHORITY_LLM_PROPOSED)
    report, path = _apply(tmp_path, store, [submission])
    annotated = _load_annotated(path)

    assert report.skipped_llm_only == 1
    assert report.scored == 0
    assert annotated[task_id].scoring_status == "candidate"
    assert annotated[task_id].is_scored is False
    assert report.candidate == 1


def test_a_single_human_annotation_is_not_enough(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    report, path = _apply(tmp_path, store, [_submission(task_id, "reviewer-a")])
    annotated = _load_annotated(path)
    assert report.scored == 0
    assert report.skipped_no_adjudication == 1
    assert annotated[task_id].is_scored is False


def test_two_agreeing_human_annotations_score_the_task(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    report, path = _apply(
        tmp_path,
        store,
        [
            _submission(task_id, "reviewer-a"),
            _submission(task_id, "reviewer-b"),
        ],
    )
    annotated = _load_annotated(path)
    reference = annotated[task_id]
    assert report.scored == 1
    assert reference.scoring_status == "scored"
    assert reference.annotation_status == "reviewed"
    assert reference.is_scored is True
    assert reference.effective_verification_tier == "expert_adjudicated"
    assert reference.annotator_ids == ["reviewer-a", "reviewer-b"]


def test_a_human_adjudication_scores_a_disagreement(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    submissions = [
        _submission(task_id, "reviewer-a", label="占位决策甲"),
        _submission(task_id, "reviewer-b", label="占位决策乙"),
    ]
    final = _submission(task_id, "adjudicator", authority=AUTHORITY_HUMAN_ADJUDICATED)
    record = AdjudicationRecord(
        task_id=task_id,
        status=ADJUDICATION_RESOLVED,
        adjudicator_id="adjudicator",
        disagreement_category="decision_family",
        final_annotation=final,
    )
    report, path = _apply(tmp_path, store, submissions, [record])
    annotated = _load_annotated(path)
    reference = annotated[task_id]
    assert report.scored == 1
    assert reference.annotation_status == "adjudicated"
    assert reference.adjudication_status == "adjudicated"
    assert reference.primary_expected_decision is not None
    assert reference.annotation_guideline_version == GUIDELINE


def test_unannotated_tasks_stay_unscored(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    _, path = _apply(tmp_path, store, [_submission(task_id, "reviewer-a")])
    annotated = _load_annotated(path)
    for reference in annotated.values():
        if reference.task_id != task_id:
            assert reference.scoring_status == "unscored"
            assert reference.is_scored is False


# ---------------------------------------------------------------------------
# Agreement statistics
# ---------------------------------------------------------------------------


def test_agreement_statistics_are_computed_and_undefined_values_are_none() -> None:
    assert raw_agreement([["a", "a"], ["b", "b"], ["a", "b"]]) == pytest.approx(2 / 3)
    assert raw_agreement([]) is None
    assert cohen_kappa([("a", "a")]) is None
    assert cohen_kappa([("a", "a"), ("a", "a")]) is None
    assert cohen_kappa([("a", "a"), ("b", "b")]) == pytest.approx(1.0)
    assert krippendorff_alpha_nominal([["a", "a"], ["a", "a"]]) is None
    assert set_agreement([], []) is None
    precision, recall, jaccard = set_agreement(["a", "b"], ["b", "c"])
    assert (precision, recall, jaccard) == (0.5, 0.5, 1 / 3)


def test_agreement_report_flags_poor_agreement(tmp_path: Path) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_a = plan.selected[0].task_id
    task_b = plan.selected[1].task_id
    submissions = [
        _submission(task_a, "reviewer-a", label="甲"),
        _submission(task_a, "reviewer-b", label="乙"),
        _submission(task_b, "reviewer-a", label="甲"),
        _submission(task_b, "reviewer-b", label="乙"),
    ]
    report = build_agreement_report(submissions, guideline_version=GUIDELINE)
    assert report.tasks_with_two_or_more_ratings == 2
    assert report.tasks_with_disagreement == 2
    assert report.value_of("raw_agreement") == 0.0
    assert report.guideline_revision_recommended is True
    assert report.disagreements


def test_agreement_report_withholds_statistics_for_single_rated_tasks(
    tmp_path: Path,
) -> None:
    store, plan = _store_and_plan(tmp_path)
    task_id = plan.selected[0].task_id
    report = build_agreement_report(
        [_submission(task_id, "reviewer-a")], guideline_version=GUIDELINE
    )
    assert report.tasks_with_one_rating == 1
    assert report.value_of("raw_agreement") is None
    assert any("single rating" in note for note in report.notes)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_sampling_plan_regenerates_from_the_same_seed(tmp_path: Path) -> None:
    store, _ = _store_and_plan(tmp_path)
    split = _fixture_split(store)
    spec = SamplingSpec(
        purpose="dev_baseline_evaluation_sample", split=split, target_size=2, seed=7
    )
    first = build_sampling_plan(store, spec)
    second = build_sampling_plan(store, spec)
    assert first.selection_sha256 == second.selection_sha256
    assert first.task_ids == second.task_ids
    assert verify_sampling_plan(store, first) is True

    changed = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="dev_baseline_evaluation_sample",
            split=split,
            target_size=2,
            seed=8,
        ),
    )
    assert changed.selection_sha256 != first.selection_sha256


def test_one_task_per_conversation_is_enforced(tmp_path: Path) -> None:
    store, _ = _store_and_plan(tmp_path, tasks=24)
    plan = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="dev_baseline_evaluation_sample",
            split=_fixture_split(store),
            target_size=10,
        ),
    )
    assert plan.selected_conversations == len(plan.task_ids)
    assert len(set(plan.conversation_ids)) == len(plan.conversation_ids)


def test_excluded_conversations_are_honoured(tmp_path: Path) -> None:
    store, _ = _store_and_plan(tmp_path, tasks=24)
    split = _fixture_split(store)
    first = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="train_r_annotation_pilot", split=split, target_size=2
        ),
    )
    second = build_sampling_plan(
        store,
        SamplingSpec(
            purpose="train_r_feedback_sample",
            split=split,
            target_size=2,
            exclude_conversations=first.conversation_ids,
        ),
    )
    assert set(first.conversation_ids) & set(second.conversation_ids) == set()
    assert second.excluded_conversations == 2
