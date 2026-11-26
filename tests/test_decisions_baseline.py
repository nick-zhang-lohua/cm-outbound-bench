"""Group A baseline integrity: no flowchart, independent sessions, hashes, isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.decisions.agent import (
    AGENT_VERSION,
    AgentConfigError,
    ResolvedAgentConfig,
    ScriptedDecisionAgent,
    load_agent_config,
)
from cm_outbound_bench.decisions.agent_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_agent_request,
    prompt_sha256,
)
from cm_outbound_bench.decisions.annotation import (
    ANSWERABILITY_SUFFICIENT,
    DecisionReference,
)
from cm_outbound_bench.decisions.runner import (
    AgentRunInfo,
    BenchmarkRunner,
    RunnerConfig,
    RunnerError,
)
from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    AgentInvocation,
    PrivateTaskReference,
)
from cm_outbound_bench.decisions.store import TaskStore
from cm_outbound_bench.decisions.trace import (
    TraceManager,
    TraceSecretError,
    iter_traces,
)
from decision_support import (
    build_tasks,
    dialogue,
    distinct_label,
    load_calls,
    manifest_for,
    write_corpus,
)

SECRET = "sk-baseline-fixture-secret-value"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def _store(tmp_path: Path, *, calls: int = 5) -> TaskStore:
    corpus = write_corpus(
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
    materialized = load_calls(corpus)
    manifest = manifest_for(materialized, max_hamming=0)
    store, _, _ = build_tasks(materialized, manifest, tmp_path / "tasks")
    return store


def _split(store: TaskStore) -> str:
    counts = store.counts_by_split()
    total, name = max(
        (counts.get(candidate, 0), candidate)
        for candidate in ("dev", "train_f", "train_r")
    )
    assert total > 0
    return name


def _runner(store: TaskStore, tmp_path: Path, factory, **kwargs):
    manager = TraceManager(
        tmp_path / "traces", "RS-BASELINE", secrets=kwargs.pop("secrets", ())
    )
    runner = BenchmarkRunner(
        store,
        factory,
        manager,
        agent_info=kwargs.pop(
            "agent_info", AgentRunInfo(label="scripted", model_name="none")
        ),
        dataset_version="neutral_test_fixture",
        **kwargs,
    )
    return runner, manager


# ---------------------------------------------------------------------------
# Group A has no flowchart and no workflow
# ---------------------------------------------------------------------------


def test_the_group_a_prompt_contains_no_flowchart_or_workflow() -> None:
    lowered = SYSTEM_PROMPT.casefold()
    for fragment in ("flowchart", "流程图", "workflow", "工作流", "sop", "流程节点"):
        assert fragment not in lowered, fragment
    assert PROMPT_VERSION == "baseline-a-v1"


def test_the_agent_request_is_built_only_from_the_public_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task_id = store.task_ids_for_split(_split(store))[0]
    payload = store.agent_visible_payload(task_id)
    request = build_agent_request(
        store.load_public_task(task_id)
    )
    serialized = request.system_prompt + request.user_prompt
    for turn in payload["visible_context"]:
        assert turn["content"] in serialized
    reference = store.require_reference(task_id)
    assert reference.historical_next_action_reference not in serialized
    assert task_id not in serialized or task_id in request.system_prompt


def test_a_flowchart_injection_would_change_the_prompt_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task_id = store.task_ids_for_split(_split(store))[0]
    task = store.load_public_task(task_id)
    baseline = build_agent_request(task)
    assert prompt_sha256(baseline) != prompt_sha256(
        baseline.model_copy(
            update={"system_prompt": baseline.system_prompt + "\n【流程图】占位"}
        )
    )


# ---------------------------------------------------------------------------
# Fresh sessions and record keeping
# ---------------------------------------------------------------------------


def test_each_task_gets_an_independent_agent_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created: list[ScriptedDecisionAgent] = []

    def factory() -> ScriptedDecisionAgent:
        agent = ScriptedDecisionAgent()
        created.append(agent)
        return agent

    runner, _ = _runner(store, tmp_path, factory)
    split = _split(store)
    expected = min(3, len(store.task_ids_for_split(split)))
    runner.run(RunnerConfig(split=split, task_limit=expected))
    assert len(created) == expected
    assert all(agent.sessions == 1 for agent in created)


def test_no_prompt_text_crosses_a_task_boundary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[str] = []

    class RecordingAgent:
        def decide(self, request):  # noqa: ANN001, ANN201
            seen.append(request.user_prompt)
            return ScriptedDecisionAgent().decide(request)

    runner, _ = _runner(store, tmp_path, RecordingAgent)
    split = _split(store)
    runner.run(RunnerConfig(split=split, task_limit=3))
    assert len(seen) >= 2
    assert len(seen) == len(set(seen)), "two tasks received an identical prompt"
    for left in seen:
        for right in seen:
            if left is right:
                continue
            assert not right.startswith(left), "one prompt contains another"
            assert not left.startswith(right), "one prompt contains another"


def test_configuration_and_prompt_hashes_are_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = ResolvedAgentConfig(
        label="fixture-provider",
        base_url="https://example.invalid/v1",
        model="fixture-model",
        timeout=30.0,
        max_retries=1,
        temperature=0.2,
        max_output_tokens=256,
        api_key=__import__("pydantic").SecretStr("fixture-key"),
    )
    info = AgentRunInfo(
        label=config.label,
        model_name=config.model,
        model_parameters=config.public_parameters,
        config_hash=config.config_hash,
        agent_version=AGENT_VERSION,
        base_url_host=config.base_url_host,
        secrets=tuple(config.secret_values()),
    )
    runner, manager = _runner(store, tmp_path, ScriptedDecisionAgent, agent_info=info)
    split = _split(store)
    runner.run(RunnerConfig(split=split, task_limit=2))

    for trace in iter_traces(manager.directory):
        assert len(trace.input_hash) == 64
        assert trace.config_hash == config.config_hash
        assert trace.prompt_version == PROMPT_VERSION
        assert trace.model_name == "fixture-model"
        assert trace.model_parameters["temperature"] == 0.2
        assert trace.condition == "A"
        assert trace.dataset_version == "neutral_test_fixture"

    manifest = json.loads(
        (manager.directory / "run_set_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["config_hash"] == config.config_hash
    assert manifest["prompt_version"] == PROMPT_VERSION
    assert manifest["condition"] == "A"


def test_the_config_hash_does_not_depend_on_the_secret(tmp_path: Path) -> None:
    common = dict(
        label="fixture-provider",
        base_url="https://example.invalid/v1",
        model="fixture-model",
        timeout=30.0,
        max_retries=1,
        temperature=0.2,
        max_output_tokens=256,
    )
    from pydantic import SecretStr

    first = ResolvedAgentConfig(api_key=SecretStr("key-a"), **common)
    second = ResolvedAgentConfig(api_key=SecretStr("key-b"), **common)
    assert first.config_hash == second.config_hash


# ---------------------------------------------------------------------------
# Test-split isolation
# ---------------------------------------------------------------------------


def test_the_test_split_cannot_be_run_without_an_explicit_opt_in(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, _ = _runner(store, tmp_path, ScriptedDecisionAgent)
    with pytest.raises(RunnerError) as caught:
        runner.run(RunnerConfig(split="test", task_limit=1))
    assert "isolated" in str(caught.value)


def test_a_test_task_cannot_be_smuggled_into_a_development_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, _ = _runner(store, tmp_path, ScriptedDecisionAgent)
    split = _split(store)
    test_ids = store.task_ids_for_split("test")
    if not test_ids:
        pytest.skip("fixture split produced no Test task")
    with pytest.raises(RunnerError):
        runner.run(RunnerConfig(split=split, task_ids=[test_ids[0]]))


def test_annotation_export_refuses_the_test_split(tmp_path: Path) -> None:
    from cm_outbound_bench.decisions.annotation_io import AnnotationError
    from cm_outbound_bench.decisions.sampling import SamplingSpec, build_sampling_plan

    store = _store(tmp_path, calls=12)
    if not store.task_ids_for_split("test"):
        pytest.skip("fixture split produced no Test task")
    plan = build_sampling_plan(
        store, SamplingSpec(purpose="train_r_annotation_pilot", split="test", target_size=1)
    )
    from cm_outbound_bench.decisions.annotation_io import export_annotation_packets

    with pytest.raises(AnnotationError):
        export_annotation_packets(store, plan, tmp_path / "packets")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_a_configured_secret_is_never_written_to_a_trace(tmp_path: Path) -> None:
    store = _store(tmp_path)

    class LeakyAgent:
        def decide(self, request):  # noqa: ANN001, ANN201
            return AgentInvocation(
                status="provider_error",
                error_code="transport_error",
                error_message=f"failed with {SECRET}",
                latency_ms=1,
                attempts=1,
            )

    runner, manager = _runner(
        store,
        tmp_path,
        LeakyAgent,
        secrets=(SECRET,),
        agent_info=AgentRunInfo(
            label="leaky", model_name="none", secrets=(SECRET,)
        ),
    )
    split = _split(store)
    runner.run(RunnerConfig(split=split, task_limit=2))
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in manager.directory.glob("*.json")
    )
    assert SECRET not in serialized
    assert "[REDACTED]" in serialized


def test_the_trace_manager_refuses_to_persist_a_secret(tmp_path: Path) -> None:
    manager = TraceManager(tmp_path / "traces", "RS-SECRET", secrets=(SECRET,))
    from cm_outbound_bench.decisions.evaluator import evaluate_invocation
    from cm_outbound_bench.decisions.schema import PublicDecisionTask, VisibleTurn
    from cm_outbound_bench.decisions.trace import TraceRecord

    task = PublicDecisionTask(
        task_id="TASK_FIXTURE_1",
        visible_context=[VisibleTurn(role="customer", content="占位客户")],
    )
    reference = PrivateTaskReference(
        task_id="TASK_FIXTURE_1",
        call_id="CALL_FIXTURE_1",
        split="dev",
        cutoff_record_index=0,
        cutoff_seq=0,
        cutoff_segment_id="CALL_FIXTURE_1:0",
        cutoff_start_ms=0,
        cutoff_visible_turn_count=1,
        hidden_speech_turn_count=0,
    )
    invocation = AgentInvocation(
        status="provider_error", error_message=SECRET, latency_ms=1, attempts=1
    )
    evaluation = evaluate_invocation(task, reference, invocation)
    trace = TraceRecord(
        run_id="RUN-FIXTURE",
        run_set_id="RS-SECRET",
        task_id="TASK_FIXTURE_1",
        evaluation=evaluation,
        error={"message": SECRET},
        execution_status="provider_error",
    )
    with pytest.raises(TraceSecretError):
        manager.write_trace(trace)


def test_the_agent_config_refuses_to_run_without_a_credential(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "agent.toml"
    config.write_text(
        "\n".join(
            [
                "[provider]",
                'label = "fixture"',
                'base_url = "https://example.invalid/v1"',
                'api_key_env = "FIXTURE_ABSENT_KEY"',
                'model = "fixture-model"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("CM_BASELINE_API_KEY", raising=False)
    monkeypatch.delenv("FIXTURE_ABSENT_KEY", raising=False)
    with pytest.raises(AgentConfigError):
        load_agent_config(config, environ={})


# ---------------------------------------------------------------------------
# The baseline report never fabricates a business number
# ---------------------------------------------------------------------------


def test_a_baseline_report_without_annotations_states_the_dependency(
    tmp_path: Path,
) -> None:
    from cm_outbound_bench.decisions.business_report import (
        build_report_data,
        render_report_markdown,
    )
    from cm_outbound_bench.decisions.evaluator import (
        aggregate_evaluations,
        evaluate_invocation,
    )

    store = _store(tmp_path)
    split = _split(store)
    task_ids = store.task_ids_for_split(split)[:3]
    scripted = ScriptedDecisionAgent()
    evaluations = []
    for task_id in task_ids:
        task = store.load_public_task(task_id)
        reference = store.require_reference(task_id)
        evaluations.append(
            evaluate_invocation(task, reference, scripted.decide(build_agent_request(task)))
        )
    aggregate = aggregate_evaluations(evaluations)
    report = build_report_data(
        run_set="fixture-run",
        condition="A",
        benchmark_version="0.2.0",
        dataset_version="neutral_test_fixture",
        split=split,
        model_provider="scripted",
        model_name="none",
        model_parameters={},
        prompt_version=PROMPT_VERSION,
        evaluator_version="0.2.0",
        agent_version=AGENT_VERSION,
        config_hash="cfg",
        evaluations=evaluations,
        references={task_id: store.require_reference(task_id) for task_id in task_ids},
        aggregate=aggregate,
    )
    assert report.pending_dependencies
    markdown = render_report_markdown(report)
    assert "not available" in markdown
    assert "expert_adjudicated_decision_success_rate" in markdown
    assert "policy_verified_decision_success_rate" in markdown
    # No fabricated zero: the business metric must not read as a measured 0.0.
    assert "| `expert_adjudicated_decision_success_rate` | 0" not in markdown


def test_the_scripted_agent_is_clearly_not_a_baseline_result() -> None:
    agent = ScriptedDecisionAgent()
    request = build_agent_request(
        __import__(
            "cm_outbound_bench.decisions.schema", fromlist=["PublicDecisionTask"]
        ).PublicDecisionTask(
            task_id="TASK_FIXTURE_1",
            visible_context=[
                __import__(
                    "cm_outbound_bench.decisions.schema", fromlist=["VisibleTurn"]
                ).VisibleTurn(role="customer", content="占位客户")
            ],
        )
    )
    invocation = agent.decide(request)
    assert invocation.output is not None
    assert "占位" in invocation.output.customer_response
    assert invocation.output.next_action == "scripted_smoke_test_only"


def test_a_scored_reference_must_come_from_a_human_annotation() -> None:
    reference = PrivateTaskReference(
        task_id="TASK_FIXTURE_1",
        call_id="CALL_FIXTURE_1",
        split="dev",
        cutoff_record_index=0,
        cutoff_seq=0,
        cutoff_segment_id="CALL_FIXTURE_1:0",
        cutoff_start_ms=0,
        cutoff_visible_turn_count=1,
        hidden_speech_turn_count=0,
        scoring_status="scored",
        annotation_status="reviewed",
        task_answerability=ANSWERABILITY_SUFFICIENT,
        primary_expected_decision=DecisionReference(decision_id="D1", label="占位"),
    )
    # No provenance at all: a reviewed status alone is enough for the legacy path,
    # but the applier always records provenance, so assert the applier's contract in
    # the annotation tests and here only that an explicit llm provenance blocks it.
    assert reference.is_scored is True
    reference.annotation_provenance = __import__(
        "cm_outbound_bench.decisions.annotation", fromlist=["AnnotationProvenance"]
    ).AnnotationProvenance(
        authority="llm_proposed", model_name="fixture-model"
    )
    assert reference.is_scored is False


def test_the_agent_output_contract_forbids_a_tool_intent() -> None:
    from cm_outbound_bench.decisions.agent import _parse_output

    result = _parse_output(
        json.dumps(
            {
                "customer_response": "占位回应",
                "next_action": "占位动作",
                "required_information": [],
                "tool_intent": {"name": "some_tool"},
                "uncertainty": None,
                "evidence_summary": "占位摘要",
            },
            ensure_ascii=False,
        ),
        latency_ms=1,
        attempts=1,
        usage={},
    )
    assert result.status == "invalid_output"
    assert result.error_code == "tool_intent_not_supported"


def test_an_agent_output_with_a_valid_contract_parses() -> None:
    from cm_outbound_bench.decisions.agent import _parse_output

    output = AgentDecisionOutput(
        customer_response="占位回应",
        next_action="占位动作",
        required_information=["占位信息"],
        tool_intent=None,
        uncertainty=None,
        evidence_summary="占位摘要",
    )
    result = _parse_output(
        output.model_dump_json(), latency_ms=1, attempts=1, usage={}
    )
    assert result.status == "success"
    assert result.output == output
