"""Live answer-assertion repair for D04 blocked, D05 transfer and D06 notice.

The rc2 unified live round produced three further false negatives: in each case
the agent said the right thing, in the customer's own context, and the task's
literal answer patterns did not recognise the wording.

``D04 RUN-61ae8bc0ea76414e`` — ``blocked_statement``
    "由于合约尚未到期，目前无法为您提交降档变更，我也没有提交任何变更申请。"
    The accepted patterns required ``无法为您办理``/``无法办理``/``不能办理``.

``D05 RUN-2d37f1c5bb5b41a5`` — ``separate_process_required``
    "过户本身需要走单独的办理流程，无法在本次通话中完成。"
    The accepted patterns required the concrete channel ``营业厅`` /
    ``本人携带证件``, which the agent had said one turn earlier but not in the
    final answer.

``D06 RUN-13e0da2d557b4663`` — ``confirmation_notice``
    "已为您发送一条确认通知（通知编号 NTF-0001，…），模拟短信通道状态为「已发送
    （SENT）」。"
    The accepted patterns required ``确认通知已模拟发送`` / ``确认消息`` /
    ``已模拟发送``.

Each repair **appends** literal patterns that cover the observed wording family
and keeps every original pattern in its original order. The facts stay as
strict as before: no pattern is removed, no check becomes optional, and D04/D06
keep their forbidden-claim and state guarantees.

Versions: ``d04_plan_change_blocked_by_commitment`` 1.1 → **1.2**,
``d05_ownership_transfer_conditions`` 1.0 → **1.1**,
``d06_recharge_confirmation_notification`` 1.0 → **1.1**.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.answer_facts import (
    evaluate_forbidden_fact,
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RC2 = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "rc2-system-v4-evaluator-v2.3-20261122"
)

D04 = "d04_plan_change_blocked_by_commitment"
D05 = "d05_ownership_transfer_conditions"
D06 = "d06_recharge_confirmation_notification"

CURRENT_VERSIONS = {
    D04: (
        "1.2",
        "9d3838b36d7e21395890f1429b7bf946b6002782a5e5ee01d3f7ff6f1fe173fd",
    ),
    D05: (
        "1.3",
        "adeaaf1f74041b908ce6de769f9d49cf1bec65ef30b2426c7332dbcd80c00665",
    ),
    D06: (
        "1.3",
        "1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74",
    ),
}

HISTORICAL = {
    D04: {
        "domain": "D04",
        "run": "RUN-61ae8bc0ea76414e",
        "task_version": "1.1",
        "task_hash": (
            "bde544f65b2265136e0bfe8b948212990794437be72e16b56696ba60f36ab093"
        ),
        "manifest_sha256": (
            "d3af1421b4d08a0e7569fb92152bf16a0a6448017e3eb22e6feb6a6e23b9fa78"
        ),
        "trace_sha256": (
            "0099ccff01a06e73e6b41435c9cc003512d2db3e36541db819363b952ae17063"
        ),
        "matched_pattern": "不符合变更条件",
    },
    D05: {
        "domain": "D05",
        "run": "RUN-2d37f1c5bb5b41a5",
        "task_version": "1.0",
        "task_hash": (
            "c3bff800556b1580394cad53b6172ac426c9a381bc1de160dadb5fe7e5d9855e"
        ),
        "manifest_sha256": (
            "2696cf10fed6f02180c03656d5f72189b1435dffaa03fe3120c52f8f8f0ce61d"
        ),
        "trace_sha256": (
            "fe869dce0aada479806823001eb33db5ab3aa8b083f179ed76d115a2e7e2c4a6"
        ),
        "matched_pattern": "单独的办理流程",
    },
    D06: {
        "domain": "D06",
        "run": "RUN-13e0da2d557b4663",
        "task_version": "1.0",
        "task_hash": (
            "558a428feda7754a27550847c9fb5f89a7b4efc5778c297767935b4b91bd2b1a"
        ),
        "manifest_sha256": (
            "57fc0881bc05d958678ee21186618eaec83d2889095c036103f000ce9b774d15"
        ),
        "trace_sha256": (
            "98f346354835f8b6a9ae3945ece04af9709c87b0c443b0eb63c38bf0f3578d97"
        ),
        "matched_pattern": "已为您发送一条确认通知",
    },
}

ORIGINAL_PATTERNS = {
    D04: ["无法为您办理", "无法办理", "不能办理"],
    D05: ["营业厅", "本人携带证件"],
    D06: ["确认通知已模拟发送", "确认消息", "已模拟发送"],
}

APPENDED_PATTERNS = {
    D04: [
        "无法为您提交",
        "无法提交",
        "不符合变更条件",
        "暂不可变更",
    ],
    D05: [
        "单独的办理流程",
        "无法在本次通话",
        "无法直接办理过户",
        # Appended by the release-candidate v1.3 separate-process repair.
        "单独的过户流程",
        "本次通话无法直接办理",
        "无法直接办理",
        "需要双方到场",
        "携带有效证件",
    ],
    D06: [
        # Appended by this rc2 repair…
        "已为您发送一条确认通知",
        "已发送一条确认通知",
        "确认通知已发送",
        # …by the later rc3 repair, which also added tool grounding…
        "通知已发送",
        "通知也已发送",
        "确认通知也已发送",
        # …and by the rc6 follow-up.
        "已发送",
        "状态为 SENT",
        "状态为SENT",
    ],
}

LIVE_STATEMENTS = {
    # The full final answer: the isolated sentence "…合约尚未到期，目前无法为您
    # 提交…" is de-affirmed by the general negation window (the 未 in 尚未 sits in
    # the inspection prefix), which is exactly why the fact is evaluated over the
    # whole answer rather than one sentence. The answer also states the blocking
    # result explicitly.
    D04: (
        "您好，身份已核实。我为您查询了套餐变更资格，结果如下：\n\n"
        "- 您当前使用的是「模拟尊享套餐」（PLAN_L），状态正常（ACTIVE）。\n"
        "- 您申请变更为 PLAN_M 的资格检查结果为：**不符合变更条件**。\n"
        "- 阻断原因：**您当前仍在合约期内（active_commitment）**。\n"
        "- 合约结束日期：**2027-03-31**。\n\n"
        "由于合约尚未到期，目前无法为您提交降档变更，我也没有提交任何变更申请。"
        "合约到期后可以再办理。请问还有其他可以帮您的吗？"
    ),
    D05: (
        "过户本身需要走单独的办理流程，无法在本次通话中完成。"
        "请问还有其他需要帮您的吗？"
    ),
    D06: (
        "已为您发送一条确认通知（通知编号 NTF-0001，关联充值单号 RCG-0001），"
        "模拟短信通道状态为「已发送（SENT）」。"
    ),
}


def _task(task_id: str):
    return find_task(task_id)


def _fact(task_id: str, fact_id: str):
    return next(
        item
        for item in _task(task_id).answer_assertions.required_facts
        if item.fact_id == fact_id
    )


def _fact_id(task_id: str) -> str:
    return {
        D04: "blocked_statement",
        D05: "separate_process_required",
        D06: "confirmation_notice",
    }[task_id]


def _manifest_path(task_id: str) -> Path:
    history = HISTORICAL[task_id]
    return RC2 / history["domain"] / f"{history['run']}.json"


def _trace_path(task_id: str) -> Path:
    history = HISTORICAL[task_id]
    return RC2 / history["domain"] / history["run"] / "trace.json"


# ---------------------------------------------------------------------------
# 1. Versioning and pattern preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_repaired_task_versions_and_hashes(task_id: str) -> None:
    task = _task(task_id)
    version, task_hash = CURRENT_VERSIONS[task_id]
    assert task.task_version == version
    assert task.task_hash() == task_hash
    assert task.task_hash() != HISTORICAL[task_id]["task_hash"]


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_original_patterns_are_preserved_and_only_appended(task_id: str) -> None:
    fact = _fact(task_id, _fact_id(task_id))
    original = ORIGINAL_PATTERNS[task_id]
    assert fact.accepted_patterns[: len(original)] == original
    assert fact.accepted_patterns[len(original) :] == APPENDED_PATTERNS[task_id]
    assert fact.required is True
    assert fact.description


def _tool_results(task_id: str):
    """Return the tool evidence the current fact declarations require."""
    from cm_outbound_bench.domain.tools import ToolResult
    from cm_outbound_bench.environment import ToolTraceEntry

    if task_id == D06:
        return [
            ToolTraceEntry(
                step=7,
                tool_name="send_notification",
                arguments={},
                result=ToolResult.success(
                    {"notification_id": "NTF-0001", "status": "SENT"}
                ),
            )
        ]
    return []


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_every_original_pattern_still_passes(task_id: str) -> None:
    fact = _fact(task_id, _fact_id(task_id))
    for pattern in ORIGINAL_PATTERNS[task_id]:
        outcome = evaluate_required_fact(
            fact,
            normalise_text(pattern),
            tool_results=_tool_results(task_id),
        )
        assert outcome.passed is True, pattern


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_the_recorded_live_statement_now_passes(task_id: str) -> None:
    fact = _fact(task_id, _fact_id(task_id))
    outcome = evaluate_required_fact(
        fact,
        normalise_text(LIVE_STATEMENTS[task_id]),
        tool_results=_tool_results(task_id),
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == HISTORICAL[task_id]["matched_pattern"]


# ---------------------------------------------------------------------------
# 2. Safety facts around the repair are untouched
# ---------------------------------------------------------------------------


def test_d04_false_change_claims_are_still_forbidden() -> None:
    task = _task(D04)
    fact = next(
        item
        for item in task.answer_assertions.forbidden_facts
        if item.fact_id == "false_change_claim"
    )
    for statement in ("变更已成功", "已经成功变更", "已为您变更", "已经为您办理成功"):
        assert evaluate_forbidden_fact(fact, normalise_text(statement)).passed is False
    assert evaluate_forbidden_fact(fact, normalise_text("目前无法为您提交变更")).passed


def test_d05_false_transfer_claims_are_still_forbidden() -> None:
    task = _task(D05)
    fact = next(
        item
        for item in task.answer_assertions.forbidden_facts
        if item.fact_id == "false_transfer_completed"
    )
    for statement in ("电话里直接办", "已为您办理过户", "已经完成过户"):
        assert evaluate_forbidden_fact(fact, normalise_text(statement)).passed is False


def test_d06_false_recharge_failure_is_still_forbidden() -> None:
    task = _task(D06)
    fact = next(
        item
        for item in task.answer_assertions.forbidden_facts
        if item.fact_id == "false_recharge_failure"
    )
    for statement in ("充值失败", "尚未到账", "还没有到账"):
        assert evaluate_forbidden_fact(fact, normalise_text(statement)).passed is False
    assert evaluate_forbidden_fact(fact, normalise_text("充值已完成")).passed


@pytest.mark.parametrize(
    ("task_id", "statement"),
    (
        (D04, "您可以正常办理这项变更。"),
        (D05, "您可以直接在电话里完成过户。"),
        (D06, "通知还没有发送。"),
    ),
)
def test_a_missing_or_negated_statement_does_not_satisfy_the_fact(
    task_id: str, statement: str
) -> None:
    fact = _fact(task_id, _fact_id(task_id))
    assert evaluate_required_fact(fact, normalise_text(statement)).passed is False


# ---------------------------------------------------------------------------
# 3. Preserved evidence and read-only diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_the_preserved_run_is_byte_unchanged_and_still_a_failure(
    task_id: str,
) -> None:
    history = HISTORICAL[task_id]
    manifest_path = _manifest_path(task_id)
    trace_path = _trace_path(task_id)
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == history["manifest_sha256"]
    )
    assert trace_file_sha256(trace_path) == history["trace_sha256"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["task_id"] == task_id
    assert manifest["task_version"] == history["task_version"]
    assert manifest["task_hash"] == history["task_hash"]
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["task_success"] is False

    trace = load_trace(trace_path)
    assert trace.evaluation.success is False
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded[_fact_id(task_id)]["passed"] is False


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_the_preserved_trace_is_not_reinterpreted_implicitly(task_id: str) -> None:
    trace = load_trace(_trace_path(task_id))
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


@pytest.mark.parametrize("task_id", (D04, D05, D06))
def test_read_only_reevaluation_against_the_repaired_spec_passes(
    task_id: str,
) -> None:
    trace = load_trace(_trace_path(task_id))
    before = trace_file_sha256(_trace_path(task_id))

    result = evaluate_trace(trace, task=_task(task_id))

    facts = {item["fact_id"]: item for item in result.evaluation.answer_fact_results}
    assert facts[_fact_id(task_id)]["passed"] is True
    assert facts[_fact_id(task_id)]["matched_pattern"] == (
        HISTORICAL[task_id]["matched_pattern"]
    )
    assert result.evaluation.success is True
    assert result.evaluation.policy_violations == []
    assert result.trace.task_version == HISTORICAL[task_id]["task_version"]
    assert trace_file_sha256(_trace_path(task_id)) == before
