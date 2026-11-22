"""rc4 live answer-assertion repair: D01 comparison scope and D05 real-name wording.

``D01 RUN-b6169061494f4d1b`` — ``d01_plan_comparison`` v1.1
    The agent delivered the complete comparison in the comparison turn
    ("每月便宜 40 元", "尊享贵 40 元/月"), then verified identity and closed with
    the customer's current-plan comparison ("换到畅享每月多 30 元…"). The
    ``final_answer`` scope inspected only that closing message, which legitimately
    did not repeat the PLAN_M-vs-PLAN_L difference, so ``fee_difference`` failed
    a task the customer had already been answered correctly. The assertion scope
    moves to ``any_agent_message`` (the comparison is the answer wherever it is
    delivered). The forbidden claim check becomes broader, not narrower.

``D05 RUN-ded576b3b2bb4fd9`` — ``d05_real_name_not_verified`` v1.1
    The agent described the tool's ``NOT_VERIFIED`` result as "实名登记未完成核验",
    "实名状态为『未核验』" and "实名信息未认证". The accepted patterns only knew
    ``NOT_VERIFIED`` / ``未实名``. The fact now also requires grounding in a
    successful ``get_identity_status`` result, and accepts the observed
    paraphrase family.

Versions: ``d01_plan_comparison`` 1.1 → **1.2**,
``d05_real_name_not_verified`` 1.1 → **1.2**.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.answer_facts import (
    agent_text_for_scope,
    evaluate_required_fact,
    normalise_text,
)
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.replay import evaluate_trace, resolve_task
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RC4 = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "rc4-system-v4-evaluator-v2.3-20261122"
)

D01 = "d01_plan_comparison"
D05 = "d05_real_name_not_verified"

CURRENT = {
    D01: (
        "1.3",
        "1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632",
    ),
    D05: (
        "1.3",
        "809041a3ebb4b31df32886e6a792020050c06d3eca487ec34085595050249240",
    ),
}

HISTORICAL = {
    D01: {
        "domain": "D01",
        "run": "RUN-b6169061494f4d1b",
        "task_version": "1.1",
        "task_hash": (
            "8a6157dc51c7d52ae279197a839a79d8d14a2d80379bfc98a7b946bc878626e9"
        ),
        "manifest_sha256": (
            "714b2c5063b3d6574f73c79d04b6476eefe1ff2367f46e1c4d7ba9ea5a8c92b0"
        ),
        "trace_sha256": (
            "5d3f7a4a11b33adfbc59040b02429a925f6076e79571f58c7a1a3c4c7f1f528e"
        ),
    },
    D05: {
        "domain": "D05",
        "run": "RUN-ded576b3b2bb4fd9",
        "task_version": "1.1",
        "task_hash": (
            "35b0c388057b907cdc5a0c0dca4376bdda0ce8f72218b6650f9e24cf0f8b9b3a"
        ),
        "manifest_sha256": (
            "72dfac8db8be43d29769a533f6d6f1112af3177b2cadefa9c8f4c2ad3975cca9"
        ),
        "trace_sha256": (
            "03a2ee807693ace0ce4d4e1514a5c5969e4f858e23c8a00ba6b67b3eefee4948"
        ),
    },
}


def _task(task_id: str):
    return find_task(task_id)


def _fact(task_id: str, fact_id: str):
    return next(
        item
        for item in _task(task_id).answer_assertions.required_facts
        if item.fact_id == fact_id
    )


def _manifest_path(task_id: str) -> Path:
    history = HISTORICAL[task_id]
    return RC4 / history["domain"] / f"{history['run']}.json"


def _trace_path(task_id: str) -> Path:
    history = HISTORICAL[task_id]
    return RC4 / history["domain"] / history["run"] / "trace.json"


# ---------------------------------------------------------------------------
# 1. Versions and semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (D01, D05))
def test_repaired_task_versions_and_hashes(task_id: str) -> None:
    task = _task(task_id)
    version, task_hash = CURRENT[task_id]
    assert task.task_version == version
    assert task.task_hash() == task_hash
    assert task.task_hash() != HISTORICAL[task_id]["task_hash"]


def test_d01_comparison_assertions_are_scoped_to_any_agent_message() -> None:
    task = _task(D01)
    assert task.answer_assertions.scope == "any_agent_message"
    assert {fact.fact_id for fact in task.answer_assertions.required_facts} == {
        "plan_m_fee",
        "plan_l_fee",
        "fee_difference",
    }
    # Forbidden claims are inspected across the same broad scope, so this is a
    # stricter, not a weaker, check for a false change claim.
    assert [fact.fact_id for fact in task.answer_assertions.forbidden_facts] == [
        "unrequested_change_claim"
    ]


def test_d05_real_name_fact_accepts_the_observed_paraphrases_and_is_grounded() -> None:
    fact = _fact(D05, "real_name_state")
    for pattern in ("NOT_VERIFIED", "未实名", "未核验", "未认证", "未完成核验"):
        assert pattern in fact.accepted_patterns
    assert fact.grounding_tools == ["get_identity_status"]
    assert fact.grounding_field == "real_name_status"
    assert fact.grounding_value == "NOT_VERIFIED"
    # The blocker is delivered in the substantive answer; later turns answer
    # follow-up questions and need not repeat it.
    assert fact.scope == "any_agent_message"
    assert find_task(D05).answer_assertions.scope == "final_answer"


def test_d05_real_name_recorded_answer_satisfies_the_fact() -> None:
    trace = load_trace(_trace_path(D05))
    run = trace.to_run_result()
    text = agent_text_for_scope(run, "any_agent_message")
    fact = _fact(D05, "real_name_state")
    outcome = evaluate_required_fact(fact, text, tool_results=run.tool_trace)
    assert outcome.passed is True, outcome.detail
    assert fact.fact_id == "real_name_state"
    assert fact.scope == "any_agent_message"


# ---------------------------------------------------------------------------
# 2. The recorded conversations now satisfy the facts
# ---------------------------------------------------------------------------


def test_d01_recorded_comparison_satisfies_the_fee_difference() -> None:
    trace = load_trace(_trace_path(D01))
    run = trace.to_run_result()
    text = agent_text_for_scope(run, "any_agent_message")
    outcome = evaluate_required_fact(_fact(D01, "fee_difference"), text)
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "40 元"
    # The closing message alone did not repeat the difference; that is exactly
    # why the previous final-answer scope was a false negative.
    final_answer = agent_text_for_scope(run, "final_answer")
    assert evaluate_required_fact(_fact(D01, "fee_difference"), final_answer).passed is False


def test_d05_recorded_real_name_paraphrase_passes_with_grounding() -> None:
    fact = _fact(D05, "real_name_state")
    statement = normalise_text("该号码的实名状态为「未核验」，需要先完成实名核验。")
    status = ToolTraceEntry(
        step=4,
        tool_name="get_identity_status",
        arguments={"phone_number_id": "PN502"},
        result=ToolResult.success(
            {"real_name_status": "NOT_VERIFIED", "registration_type": "PERSONAL"}
        ),
    )
    assert evaluate_required_fact(fact, statement).passed is False
    outcome = evaluate_required_fact(fact, statement, tool_results=[status])
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "未核验"

    verified = ToolTraceEntry(
        step=4,
        tool_name="get_identity_status",
        arguments={"phone_number_id": "PN502"},
        result=ToolResult.success(
            {"real_name_status": "VERIFIED", "registration_type": "PERSONAL"}
        ),
    )
    assert evaluate_required_fact(fact, statement, tool_results=[verified]).passed is False


# ---------------------------------------------------------------------------
# 3. Preserved evidence and read-only diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (D01, D05))
def test_the_preserved_run_is_byte_unchanged_and_still_a_failure(
    task_id: str,
) -> None:
    history = HISTORICAL[task_id]
    assert (
        hashlib.sha256(_manifest_path(task_id).read_bytes()).hexdigest()
        == history["manifest_sha256"]
    )
    assert trace_file_sha256(_trace_path(task_id)) == history["trace_sha256"]

    manifest = json.loads(_manifest_path(task_id).read_text(encoding="utf-8"))
    assert manifest["task_version"] == history["task_version"]
    assert manifest["task_hash"] == history["task_hash"]
    assert manifest["run_status"] == "evaluation_failure"

    trace = load_trace(_trace_path(task_id))
    assert trace.evaluation.success is False


@pytest.mark.parametrize("task_id", (D01, D05))
def test_the_preserved_trace_is_not_reinterpreted_implicitly(task_id: str) -> None:
    trace = load_trace(_trace_path(task_id))
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


@pytest.mark.parametrize("task_id", (D01, D05))
def test_read_only_reevaluation_against_the_repaired_spec_passes(
    task_id: str,
) -> None:
    trace = load_trace(_trace_path(task_id))
    before = trace_file_sha256(_trace_path(task_id))

    result = evaluate_trace(trace, task=_task(task_id))

    assert result.evaluation.success is True, result.evaluation.summary
    assert result.evaluation.policy_violations == []
    assert result.trace.task_version == HISTORICAL[task_id]["task_version"]
    assert trace_file_sha256(_trace_path(task_id)) == before
