"""rc3 live answer-assertion repair: D05 arrears scope/grounding and D06 wording.

The rc3 unified round passed 12 of 14 tasks. The two failures (and one repeat of
the eligibility gap under an earlier-paraphrase watch) were again false negatives.

``D05 RUN-9aa11777f1e24f98`` — ``d05_real_name_not_verified`` v1.0
    The agent reported both blockers in its substantive answer:
    "过户条件检查结果：当前不可过户，存在两项阻碍条件：1. 实名信息未认证；
    2. 存在未结清的欠费。" The later turns answered the customer's follow-up
    questions, and the *last* message no longer repeated 欠费, so the
    ``final_answer`` scope missed a fact the customer had already been told.
    Fix: ``arrears_state`` uses a per-fact ``any_agent_message`` scope and is now
    grounded in ``check_ownership_transfer_conditions`` returning
    ``arrears_outstanding`` in ``blocking_conditions``. Task v1.0 → **1.1**.

``D06 RUN-97b1a2625c9d4edb`` — ``d06_enrollment_confirmation_notification`` v1.0
    The agent reported "系统返回的状态为 **SENT**". The English patterns
    required ``status: SENT`` / ``status=SENT`` / ``status is SENT`` and did not
    cover the Chinese "状态为 SENT" form. Task v1.0 → **1.1**.

``D06 RUN-fee7e55a889642de`` — ``d06_recharge_confirmation_notification`` v1.1
    The agent reported "确认通知也已发送（通知编号 NTF-0001…）". The v1.1
    patterns did not cover the ``也已`` form. Task v1.1 → **1.2**, and the fact
    is now grounded in a successful ``send_notification`` /
    ``get_notification_status`` result with ``status=SENT``.

No pattern is removed, no forbidden fact is relaxed, and the evaluator, prompt,
policies and the other 11 tasks are untouched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.answer_facts import (
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
RC3 = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "rc3-system-v4-evaluator-v2.3-20261122"
)

D05_REAL_NAME = "d05_real_name_not_verified"
D06_ENROLLMENT = "d06_enrollment_confirmation_notification"
D06_RECHARGE = "d06_recharge_confirmation_notification"

CURRENT = {
    D05_REAL_NAME: (
        "1.3",
        "809041a3ebb4b31df32886e6a792020050c06d3eca487ec34085595050249240",
    ),
    D06_ENROLLMENT: (
        "1.1",
        "9326e6ceea9e0e91f17437689e3712c562e7888aef26b740cc96e38b7d3399e5",
    ),
    D06_RECHARGE: (
        "1.3",
        "1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74",
    ),
}

HISTORICAL = {
    D05_REAL_NAME: {
        "domain": "D05",
        "run": "RUN-9aa11777f1e24f98",
        "task_version": "1.0",
        "task_hash": (
            "eb1bc44eef8b1a0889cafed67dc991cefe8ef1e386229d88d478af4e2cb69f64"
        ),
        "manifest_sha256": (
            "04c7778394dc8de0a43ce7929eab389d328d8b558a79b8ffb8b1a768e8f2fca6"
        ),
        "trace_sha256": (
            "98d8bbd7bc99b5ebccaad895d96beef28b89bee06ad802a20358f5592ea95b83"
        ),
        "fact_id": "arrears_state",
    },
    D06_ENROLLMENT: {
        "domain": "D06",
        "run": "RUN-97b1a2625c9d4edb",
        "task_version": "1.0",
        "task_hash": (
            "78b2397f8c203c1c37c49ac1a9f65dd8b456b918411ebade9dd7c7ac54dc40b9"
        ),
        "manifest_sha256": (
            "6b5681163b7fc5ca3115e1f1d71a761166a4585345f6f1c70401e2c28baa26bb"
        ),
        "trace_sha256": (
            "85d57d36bd4cb528ff497b185030ee6dfe532a536b074989386a159f50b64ae4"
        ),
        "fact_id": "notification_status",
    },
    D06_RECHARGE: {
        "domain": "D06",
        "run": "RUN-fee7e55a889642de",
        "task_version": "1.1",
        "task_hash": (
            "64743ff5db12f9b0bda2bc6cebb91a1f5fea70272194b769dc807b9705166df4"
        ),
        "manifest_sha256": (
            "a8e415345281b14bc3b8e2a3e993c253177bcdbf3628b5ea1915dfc993cd63d4"
        ),
        "trace_sha256": (
            "ce7457b1c20c1db8448d3a1d498a9c8ffda44e83d3d4ed8452f195b7db5af3b4"
        ),
        "fact_id": "confirmation_notice",
    },
}

LIVE_TEXT = {
    D05_REAL_NAME: (
        "您好，已为您核实身份。查询结果如下：\n\n"
        "- 该号码的实名登记状态为**未实名认证**（个人登记类型）。\n"
        "- 过户条件检查结果：**当前不可过户**，存在两项阻碍条件：\n"
        "  1. 实名信息未认证；\n  2. 存在未结清的欠费。\n\n"
        "因此目前无法办理过户。需要先完成实名认证并结清欠费后，才具备过户条件。"
    ),
    D06_ENROLLMENT: (
        "已为您办理完成，情况如下：\n\n"
        "- 办理结果：号码 PN601 已成功开通「模拟流量加赠包」。\n"
        "- 确认通知：已按您的要求发送一条模拟确认短信（通知编号 NTF-0001），"
        "系统返回的状态为 **SENT**。"
    ),
    D06_RECHARGE: (
        "充值已成功办理，情况如下：\n\n"
        "- 充值金额：20.00 元\n- 充值单号：RCG-0001\n\n"
        "确认通知也已发送（通知编号 NTF-0001，关联充值单号 RCG-0001），"
        "模拟短信通道返回的状态为「已发送（SENT）」。"
    ),
}


def _fact(task_id: str):
    history = HISTORICAL[task_id]
    return next(
        item
        for item in find_task(task_id).answer_assertions.required_facts
        if item.fact_id == history["fact_id"]
    )


def _entry(
    tool_name: str,
    data: dict,
    *,
    ok: bool = True,
) -> ToolTraceEntry:
    return ToolTraceEntry(
        step=5,
        tool_name=tool_name,
        arguments={},
        result=(
            ToolResult.success(data)
            if ok
            else ToolResult.failure("synthetic_error", "synthetic failure")
        ),
    )


def _manifest_path(task_id: str) -> Path:
    history = HISTORICAL[task_id]
    return RC3 / history["domain"] / f"{history['run']}.json"


def _trace_path(task_id: str) -> Path:
    history = HISTORICAL[task_id]
    return RC3 / history["domain"] / history["run"] / "trace.json"


# ---------------------------------------------------------------------------
# 1. Versions and the new fact semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (D05_REAL_NAME, D06_ENROLLMENT, D06_RECHARGE))
def test_repaired_task_versions_and_hashes(task_id: str) -> None:
    task = find_task(task_id)
    version, task_hash = CURRENT[task_id]
    assert task.task_version == version
    assert task.task_hash() == task_hash
    assert task.task_hash() != HISTORICAL[task_id]["task_hash"]


def test_d05_arrears_fact_is_scoped_to_any_agent_message_and_grounded() -> None:
    fact = _fact(D05_REAL_NAME)
    assert fact.scope == "any_agent_message"
    assert fact.grounding_tools == ["check_ownership_transfer_conditions"]
    assert fact.grounding_field == "blocking_conditions"
    assert fact.grounding_value == "arrears_outstanding"
    assert fact.accepted_patterns == ["欠费", "arrears_outstanding"]


def test_d06_enrollment_status_patterns_are_appended_not_replaced() -> None:
    fact = _fact(D06_ENROLLMENT)
    original = (
        "The tool reports the status as SENT",
        "The notification status is SENT",
        "The status was reported as SENT",
        "status: SENT",
        "status=SENT",
        "status is SENT",
        "已模拟发送",
        "已发送",
        "发送成功",
    )
    assert fact.accepted_patterns[: len(original)] == list(original)
    assert fact.accepted_patterns[len(original) :] == [
        "状态为 SENT",
        "状态为SENT",
        "返回的状态为 SENT",
        "系统返回的状态为 SENT",
    ]
    assert fact.scope == "final_report"


def test_d06_recharge_notice_fact_is_now_grounded() -> None:
    fact = _fact(D06_RECHARGE)
    assert fact.grounding_tools == ["send_notification", "get_notification_status"]
    assert fact.grounding_field == "status"
    assert fact.grounding_value == "SENT"
    for pattern in ("通知已发送", "通知也已发送", "确认通知也已发送"):
        assert pattern in fact.accepted_patterns


# ---------------------------------------------------------------------------
# 2. The recorded live statements now pass, and only with evidence
# ---------------------------------------------------------------------------


def test_d05_recorded_arrears_statement_passes_only_with_the_blocking_reason() -> None:
    fact = _fact(D05_REAL_NAME)
    text = normalise_text(LIVE_TEXT[D05_REAL_NAME])
    blocked = _entry(
        "check_ownership_transfer_conditions",
        {
            "phone_number_id": "PN502",
            "transferable": False,
            "blocking_conditions": ["real_name_not_verified", "arrears_outstanding"],
        },
    )
    assert evaluate_required_fact(fact, text).passed is False
    outcome = evaluate_required_fact(fact, text, tool_results=[blocked])
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "欠费"

    no_arrears = _entry(
        "check_ownership_transfer_conditions",
        {"blocking_conditions": ["real_name_not_verified"]},
    )
    assert evaluate_required_fact(fact, text, tool_results=[no_arrears]).passed is False


def test_d06_recorded_notification_status_passes() -> None:
    fact = _fact(D06_ENROLLMENT)
    text = normalise_text(LIVE_TEXT[D06_ENROLLMENT])
    outcome = evaluate_required_fact(
        fact,
        text,
        tool_results=[
            _entry(
                "get_notification_status",
                {"notification_id": "NTF-0001", "status": "SENT"},
            )
        ],
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "状态为 SENT"
    # Still grounded: a failed or non-SENT result cannot back the claim.
    assert (
        evaluate_required_fact(
            fact,
            text,
            tool_results=[
                _entry(
                    "get_notification_status",
                    {"notification_id": "NTF-0001", "status": "FAILED"},
                )
            ],
        ).passed
        is False
    )


def test_d06_recharge_recorded_notice_passes_only_with_a_sent_result() -> None:
    fact = _fact(D06_RECHARGE)
    text = normalise_text(LIVE_TEXT[D06_RECHARGE])
    sent = _entry(
        "send_notification",
        {"notification_id": "NTF-0001", "status": "SENT", "kind": "CONFIRMATION"},
    )
    assert evaluate_required_fact(fact, text).passed is False
    outcome = evaluate_required_fact(fact, text, tool_results=[sent])
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "通知也已发送"
    failed = _entry(
        "send_notification",
        {"notification_id": "NTF-0001", "status": "FAILED"},
    )
    assert evaluate_required_fact(fact, text, tool_results=[failed]).passed is False


def test_d06_recharge_colon_form_passes_only_with_sent_grounding() -> None:
    fact = _fact(D06_RECHARGE)
    text = normalise_text(
        "确认通知：已发送（通知编号 NTF-0001），模拟发送状态为 SENT。"
    )
    sent = _entry("send_notification", {"status": "SENT"})
    assert evaluate_required_fact(fact, text).passed is False
    outcome = evaluate_required_fact(fact, text, tool_results=[sent])
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "已发送"


def test_old_patterns_still_pass() -> None:
    d05 = _fact(D05_REAL_NAME)
    blocked = _entry(
        "check_ownership_transfer_conditions",
        {"blocking_conditions": ["arrears_outstanding"]},
    )
    assert evaluate_required_fact(d05, normalise_text("欠费"), tool_results=[blocked]).passed
    d06_enroll = _fact(D06_ENROLLMENT)
    status = _entry("get_notification_status", {"status": "SENT"})
    assert evaluate_required_fact(
        d06_enroll, normalise_text("已模拟发送"), tool_results=[status]
    ).passed
    d06_recharge = _fact(D06_RECHARGE)
    sent = _entry("send_notification", {"status": "SENT"})
    assert evaluate_required_fact(
        d06_recharge, normalise_text("确认通知已模拟发送"), tool_results=[sent]
    ).passed


# ---------------------------------------------------------------------------
# 3. Preserved evidence and read-only diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (D05_REAL_NAME, D06_ENROLLMENT, D06_RECHARGE))
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
    assert manifest["task_id"] == task_id
    assert manifest["task_version"] == history["task_version"]
    assert manifest["task_hash"] == history["task_hash"]
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["task_success"] is False

    trace = load_trace(_trace_path(task_id))
    assert trace.evaluation.success is False
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded[history["fact_id"]]["passed"] is False


@pytest.mark.parametrize("task_id", (D05_REAL_NAME, D06_ENROLLMENT, D06_RECHARGE))
def test_the_preserved_trace_is_not_reinterpreted_implicitly(task_id: str) -> None:
    trace = load_trace(_trace_path(task_id))
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


@pytest.mark.parametrize("task_id", (D05_REAL_NAME, D06_ENROLLMENT, D06_RECHARGE))
def test_read_only_reevaluation_against_the_repaired_spec_passes(
    task_id: str,
) -> None:
    trace = load_trace(_trace_path(task_id))
    before = trace_file_sha256(_trace_path(task_id))

    result = evaluate_trace(trace, task=find_task(task_id))

    facts = {item["fact_id"]: item for item in result.evaluation.answer_fact_results}
    assert facts[HISTORICAL[task_id]["fact_id"]]["passed"] is True
    assert result.evaluation.success is True
    assert result.evaluation.policy_violations == []
    assert result.trace.task_version == HISTORICAL[task_id]["task_version"]
    assert trace_file_sha256(_trace_path(task_id)) == before
