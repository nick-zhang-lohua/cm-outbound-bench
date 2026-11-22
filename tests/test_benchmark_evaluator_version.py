"""Regression coverage for the canonical evaluator version metadata.

The evaluator implementation is the source of truth for the version recorded in
an evaluation, a run manifest, a serialized trace and an aggregate metric. A
task specification may not pin a stale override.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from cm_outbound_bench import evaluator as evaluator_module
from cm_outbound_bench.benchmark import tasks as benchmark_tasks
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION, execute_task
from cm_outbound_bench.benchmark.metrics import (
    RunFailure,
    RunManifest,
    aggregate,
    build_failure_manifest,
    build_manifest,
    load_manifests,
    render_markdown,
    write_manifest,
)
from cm_outbound_bench.benchmark.tasks import BenchmarkTask
from cm_outbound_bench.benchmark.trace import (
    TraceAgent,
    TraceEvaluationSnapshot,
    build_trace,
    load_trace,
    write_trace,
)
from cm_outbound_bench.benchmark.versions import EVALUATOR_VERSION
from cm_outbound_bench.evaluator import (
    EvaluationResult,
    EvaluationTarget,
    evaluator_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_ID = "d01_plan_comparison"

#: The evaluator hash that belongs to the current EVALUATOR_VERSION. It is a
#: content hash over the version label and the evaluator implementation modules;
#: a metadata-only change must not move it. It moves when the evaluation contract
#: itself changes, as it did for the post-conversation ``final_answer`` fix, for
#: 2.3 (pre-verification disclosure resolves the protected plan from the initial
#: state), and for 2.4 (a plan id in a public offer/plan list is no longer read as
#: an account disclosure; only an account-framed mention is).
CURRENT_EVALUATOR_HASH = (
    "a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2"
)


def _outcome():
    return execute_task(find_task(TASK_ID), agent_factory=BenchmarkDemoAgent)


def _manifest():
    return build_manifest(_outcome(), agent_type="benchmark_demo")


def _trace(outcome):
    return build_trace(
        run_id=outcome.run_id,
        episode_id=outcome.episode_id,
        task=outcome.task,
        run=outcome.run,
        evaluation=outcome.evaluation,
        benchmark_version=BENCHMARK_VERSION,
        agent=TraceAgent(agent_type="benchmark_demo", prompt_version="scripted-v1"),
    )


# ---------------------------------------------------------------------------
# 1. One canonical version
# ---------------------------------------------------------------------------


def test_canonical_evaluator_version_is_single_sourced() -> None:
    assert EVALUATOR_VERSION == "2.4"
    assert evaluator_module.EVALUATOR_VERSION == EVALUATOR_VERSION
    assert benchmark_tasks.EVALUATOR_VERSION == EVALUATOR_VERSION


def test_default_task_spec_uses_canonical_evaluator_version() -> None:
    payload = find_task(TASK_ID).model_dump(mode="json")
    payload.pop("evaluator_version", None)

    task = BenchmarkTask.model_validate(payload)

    assert task.evaluator_version == EVALUATOR_VERSION
    assert task.evaluator_version == "2.4"


def test_every_packaged_task_uses_canonical_evaluator_version() -> None:
    tasks = all_tasks()
    assert len(tasks) == 14
    for task in tasks:
        assert task.evaluator_version == EVALUATOR_VERSION, task.task_id
        assert task.evaluator_version == "2.4", task.task_id


def test_packaged_task_files_do_not_pin_a_stale_evaluator_version() -> None:
    """A task-level override is how a stale value survives a version bump."""
    for path in sorted((PROJECT_ROOT / "data" / "tasks").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for task in payload["tasks"]:
            assert (
                task.get("evaluator_version", EVALUATOR_VERSION) == EVALUATOR_VERSION
            ), path.name


@pytest.mark.parametrize("stale", ("1.0", "2.1", "2.2", "2.3"))
def test_stale_task_level_evaluator_version_is_rejected(stale: str) -> None:
    payload = find_task(TASK_ID).model_dump(mode="json")
    payload["evaluator_version"] = stale

    with pytest.raises(ValidationError) as error:
        BenchmarkTask.model_validate(payload)

    assert "evaluator_version" in str(error.value)


def test_no_stale_evaluator_version_literal_in_production_sources() -> None:
    pattern = re.compile(
        r"(EVALUATOR_VERSION\s*=\s*[\"']1\.0[\"']"
        r"|evaluator_version\s*[:=]\s*[\"']1\.0[\"'])"
    )
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in sorted(
            (PROJECT_ROOT / "src" / "cm_outbound_bench").rglob("*.py")
        )
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# ---------------------------------------------------------------------------
# 2. Evaluation, manifest, trace and metrics carry the version
# ---------------------------------------------------------------------------


def test_evaluation_target_and_result_default_to_canonical_version() -> None:
    target = EvaluationTarget(
        task_id=TASK_ID,
        customer_id="C201",
        phone_number_id="PN201",
    )
    result = EvaluationResult(
        task_id=TASK_ID,
        success=False,
        checks=[],
        policy_violations=[],
        tool_call_count=0,
        conversation_turn_count=0,
        summary="constructed",
    )

    assert target.evaluator_version == EVALUATOR_VERSION
    assert result.evaluator_version == EVALUATOR_VERSION


def test_new_evaluation_records_canonical_version() -> None:
    outcome = _outcome()

    assert outcome.evaluation.evaluator_version == EVALUATOR_VERSION
    assert outcome.evaluation.evaluator_version == "2.4"


def test_manifest_records_canonical_version() -> None:
    manifest = _manifest()

    assert manifest.evaluator_version == EVALUATOR_VERSION
    assert manifest.evaluator_version == "2.4"
    assert manifest.evaluator_hash == evaluator_hash()


def test_failure_manifest_records_canonical_version() -> None:
    failure = RunFailure(
        error_code="unexpected_runtime_error",
        task_id=TASK_ID,
        domain_id="D01",
    )
    manifest = build_failure_manifest(
        task=find_task(TASK_ID), failure=failure, agent_type="benchmark_demo"
    )

    assert manifest.run_status == "runtime_failure"
    assert manifest.evaluator_version == EVALUATOR_VERSION
    assert manifest.evaluator_version == "2.4"
    assert manifest.evaluator_hash == evaluator_hash()


def test_serialized_trace_records_canonical_version(tmp_path: Path) -> None:
    outcome = _outcome()
    trace = _trace(outcome)

    assert trace.evaluation.evaluator_version == EVALUATOR_VERSION
    assert trace.evaluation.evaluator_version == "2.4"
    payload = json.loads(trace.to_json())
    assert payload["evaluation"]["evaluator_version"] == "2.4"

    path, _file_hash = write_trace(trace, tmp_path)
    reloaded = load_trace(path)
    assert reloaded.evaluation.evaluator_version == "2.4"


def test_benchmark_metrics_preserve_canonical_version(tmp_path: Path) -> None:
    manifest = _manifest()
    write_manifest(manifest, tmp_path)
    loaded = load_manifests(tmp_path)[0]

    assert loaded.evaluator_version == EVALUATOR_VERSION
    assert loaded.evaluator_version == "2.4"
    report = aggregate([loaded])
    assert report.evaluator_versions == ["2.4"]
    assert "Evaluator version: 2.4" in render_markdown(report)


# ---------------------------------------------------------------------------
# 3. Version and hash identify the same implementation
# ---------------------------------------------------------------------------


def test_recorded_version_and_hash_describe_the_current_implementation() -> None:
    manifest = _manifest()

    assert EVALUATOR_VERSION == "2.4"
    assert evaluator_hash() == CURRENT_EVALUATOR_HASH
    assert manifest.evaluator_version == EVALUATOR_VERSION
    assert manifest.evaluator_hash == CURRENT_EVALUATOR_HASH

    digest = hashlib.sha256()
    digest.update(EVALUATOR_VERSION.encode("ascii"))
    digest.update(
        (PROJECT_ROOT / "src" / "cm_outbound_bench" / "evaluator.py").read_bytes()
    )
    digest.update(
        (
            PROJECT_ROOT
            / "src"
            / "cm_outbound_bench"
            / "benchmark"
            / "checks.py"
        ).read_bytes()
    )
    assert digest.hexdigest() == manifest.evaluator_hash


# ---------------------------------------------------------------------------
# 4. Historical artifacts keep their recorded version
# ---------------------------------------------------------------------------


def test_historical_manifest_and_trace_versions_are_not_rewritten() -> None:
    historical = RunManifest(
        run_id="RUN-historical",
        domain_id="D03",
        task_id="d03_arrears_recharge_with_consent",
        task_version="1.0",
        scenario_id="D03-BILL-002",
        initial_state_id="STATE-D03-002",
        initial_state_hash="0" * 64,
        agent_type="llm",
        task_success=False,
        evaluator_version="1.0",
        evaluator_hash=(
            "4d79e92c624421204a72e4ff52ebeb7202c60b90001dae69991db45f0376d8ff"
        ),
    )
    snapshot = TraceEvaluationSnapshot(
        success=False, evaluator_version="1.0", evaluator_hash="historical"
    )

    loaded_manifest = RunManifest.model_validate_json(historical.model_dump_json())
    loaded_snapshot = TraceEvaluationSnapshot.model_validate_json(
        snapshot.model_dump_json()
    )

    assert loaded_manifest.evaluator_version == "1.0"
    assert loaded_snapshot.evaluator_version == "1.0"
    # Historical values are preserved; the current implementation is 2.4.
    assert EVALUATOR_VERSION == "2.4"
