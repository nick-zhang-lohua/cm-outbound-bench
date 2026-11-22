"""D02 v1.3/v1.4: the eligibility-reason Answer Assertion coverage repair.

Two live runs failed the same required fact on two legitimate paraphrases:

``rc1 / RUN-6adfdb6fb7c240da`` (task v1.2)
    资格检查结果：不符合办理条件
    - 原因：您当前使用的套餐不在该活动适用的套餐范围内（该活动仅适用于特定套餐）。

``rc2 / RUN-f0616dae7a094d20`` (task v1.3)
    - 原因：您当前的套餐不在该优惠适用的套餐范围内（仅适用于 PLAN_S、PLAN_M 套餐）。

In both cases the agent stated the tool's ``plan_not_eligible`` reason
accurately. The accepted-pattern list only knew ``该活动`` and ``指定套餐``
wordings, so v1.3 appended three patterns and v1.4 appended four more noun
variants (活动/优惠/业务/产品). The fact remains *grounded*: it never passes unless
a successful ``check_offer_eligibility`` result returned ``plan_not_eligible``.

Only ``d02_eligibility_ineligible_explanation`` moves (``1.2`` → ``1.3`` →
``1.4``); the original ten patterns keep their order and the other tasks are
untouched.
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
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import evaluate_trace, resolve_task
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall
from cm_outbound_bench.runner import StopReason

TASK_ID = "d02_eligibility_ineligible_explanation"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "rc1-system-v4-evaluator-v2.3-20261122"
    / "D02"
)
FAILED_RUN = "RUN-6adfdb6fb7c240da"

FAILED_MANIFEST_SHA256 = (
    "d786927774dc53cf8f821fe608775d5053be07b3c500fc07aa227dcf53a9f315"
)
FAILED_TRACE_SHA256 = (
    "144eb9732cef31130ab1d66a4dd1bf7d9b35860c89a93ce0397fb8df45c75832"
)
FAILED_TRACE_CONTENT_SHA256 = (
    "08c5069bebb4f05336a44a24d7f93732df6c9172d745907bc659bd6c828b609f"
)

V12_TASK_HASH = "756dec390752fb6bf2db1ffde500364a76ae4394cd35773b8bd67b2b3af5fffa"
V13_TASK_HASH = "1096930a48b39dbb604788da1ec33b98ad945cfd63596c0fdef42291a1083498"
V14_TASK_HASH = "41f0b0001834d6b1b0b5142b78ded4381ecfc2336057cc41fc08179935632129"

V12_ACCEPTED_PATTERNS = [
    "plan_not_eligible",
    "当前套餐不在该活动适用范围",
    "您当前的套餐不在该活动适用的套餐范围内",
    "您当前的套餐不在该活动的适用套餐范围内",
    "套餐不属于该活动的适用套餐",
    "该套餐不在活动指定的套餐范围内",
    "套餐不符合活动要求",
    "仅适用于指定套餐",
    "套餐不符",
    "不在参加范围",
]
V13_APPENDED_PATTERNS = [
    "当前使用的套餐不在该活动适用",
    "仅适用于特定套餐",
    "不在该活动的适用套餐范围内",
]
V14_APPENDED_PATTERNS = [
    "不在该活动适用",
    "不在该优惠适用",
    "不在该业务适用",
    "不在该产品适用",
]

LIVE_FINAL_ANSWER = (
    "身份已核实。我为您查询了该活动并做了资格检查，结果如下：\n\n"
    "**活动信息（模拟流量加赠包）**\n"
    "- 每月额外费用：10.00 元（CNY）\n"
    "- 有效期：6 个月\n"
    "- 到期后自动续订\n"
    "- 使用限制：仅限主卡使用；优惠期内可取消，取消后按标准资费执行\n\n"
    "**资格检查结果：不符合办理条件**\n"
    "- 原因：您当前使用的套餐不在该活动适用的套餐范围内"
    "（该活动仅适用于特定套餐）。\n\n"
    "因此本次无法为您办理该活动，我也不会进行任何开通操作。"
    "如您希望，我可以为您留意其他适合您套餐的活动。"
)

#: The second observed paraphrase, from the rc2 run. It says 该优惠 instead of
#: 该活动 and names the eligible plans instead of saying "指定套餐".
LIVE_FINAL_ANSWER_V13 = (
    "您好，身份已核实。该优惠为「模拟流量加赠包」：每月额外费用 10.00 元（CNY），"
    "有效期 6 个月，到期后自动续订；限制条件为仅限主卡使用，优惠期内可取消，"
    "取消后按标准资费执行。\n\n"
    "我为您做了资格查询，结果是：您的号码 PN103 目前不符合办理条件，"
    "原因是您当前的套餐不在该优惠适用的套餐范围内（仅适用于 PLAN_S、PLAN_M 套餐）。"
    "因此本次无法为您办理，也没有做任何开通操作。感谢您的理解。"
)


def _fact():
    task = find_task(TASK_ID)
    return next(
        item
        for item in task.answer_assertions.required_facts
        if item.fact_id == "ineligible_reason"
    )


def _entry(reasons: list[str], *, ok: bool = True, tool: str = "check_offer_eligibility"):
    return ToolTraceEntry(
        step=5,
        tool_name=tool,
        arguments={"phone_number_id": "PN103", "offer_id": "OFFER_D02_001"},
        result=(
            ToolResult.success({"eligible": not reasons, "reasons": reasons})
            if ok
            else ToolResult.failure("tool_error", "synthetic failure")
        ),
    )


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, *, messages, tools) -> ProviderResponse:
        del messages, tools
        response = self._responses[self.calls]
        self.calls += 1
        return response


def _tool(tool_name: str, **arguments) -> ProviderResponse:
    return ProviderResponse(
        tool_calls=[
            ProviderToolCall(
                call_id=f"call-{tool_name}",
                tool_name=tool_name,
                arguments=arguments,
            )
        ]
    )


# ---------------------------------------------------------------------------
# 1. Versioning
# ---------------------------------------------------------------------------


def test_task_is_v1_4_with_a_new_hash() -> None:
    task = find_task(TASK_ID)
    assert task.task_version == "1.4"
    assert task.task_hash() == V14_TASK_HASH
    assert task.task_hash() != V13_TASK_HASH
    assert task.task_hash() != V12_TASK_HASH


def test_original_patterns_are_preserved_and_only_the_repairs_are_appended() -> None:
    fact = _fact()
    assert fact.accepted_patterns[: len(V12_ACCEPTED_PATTERNS)] == (
        V12_ACCEPTED_PATTERNS
    )
    offset = len(V12_ACCEPTED_PATTERNS)
    assert fact.accepted_patterns[offset : offset + len(V13_APPENDED_PATTERNS)] == (
        V13_APPENDED_PATTERNS
    )
    offset += len(V13_APPENDED_PATTERNS)
    assert fact.accepted_patterns[offset:] == V14_APPENDED_PATTERNS
    assert fact.grounding_tools == ["check_offer_eligibility"]
    assert fact.grounding_field == "reasons"
    assert fact.grounding_value == "plan_not_eligible"
    # The fact inherits the assertion-level ``final_answer`` scope.
    assert fact.scope is None
    assert find_task(TASK_ID).answer_assertions.scope == "final_answer"
    assert fact.required is True


# ---------------------------------------------------------------------------
# 2. The observed answer now satisfies the fact, and only with grounding
# ---------------------------------------------------------------------------


def test_the_recorded_live_answer_satisfies_the_reason_fact() -> None:
    outcome = evaluate_required_fact(
        _fact(),
        normalise_text(LIVE_FINAL_ANSWER),
        tool_results=[_entry(["plan_not_eligible"])],
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "当前使用的套餐不在该活动适用"


def test_the_second_recorded_wording_also_satisfies_the_reason_fact() -> None:
    outcome = evaluate_required_fact(
        _fact(),
        normalise_text(LIVE_FINAL_ANSWER_V13),
        tool_results=[_entry(["plan_not_eligible"])],
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "不在该优惠适用"


@pytest.mark.parametrize(
    "statement",
    (
        "您的套餐不在该活动适用的套餐范围内。",
        "您的套餐不在该优惠适用的套餐范围内。",
        "您的套餐不在该业务适用的套餐范围内。",
        "您的套餐不在该产品适用的套餐范围内。",
    ),
)
def test_every_noun_variant_requires_and_accepts_grounding(
    statement: str,
) -> None:
    fact = _fact()
    assert (
        evaluate_required_fact(fact, normalise_text(statement), tool_results=[])
        .passed
        is False
    )
    outcome = evaluate_required_fact(
        fact,
        normalise_text(statement),
        tool_results=[_entry(["plan_not_eligible"])],
    )
    assert outcome.passed is True, outcome.detail


def test_the_same_answer_without_grounding_still_fails() -> None:
    outcome = evaluate_required_fact(
        _fact(), normalise_text(LIVE_FINAL_ANSWER), tool_results=[]
    )
    assert outcome.passed is False
    assert "not grounded" in outcome.detail


def test_the_same_answer_with_the_wrong_tool_reason_still_fails() -> None:
    outcome = evaluate_required_fact(
        _fact(),
        normalise_text(LIVE_FINAL_ANSWER),
        tool_results=[_entry(["line_not_active"])],
    )
    assert outcome.passed is False


def test_a_failed_eligibility_tool_result_cannot_ground_the_fact() -> None:
    outcome = evaluate_required_fact(
        _fact(),
        normalise_text(LIVE_FINAL_ANSWER),
        tool_results=[_entry(["plan_not_eligible"], ok=False)],
    )
    assert outcome.passed is False


def test_an_unrelated_tool_result_cannot_ground_the_fact() -> None:
    outcome = evaluate_required_fact(
        _fact(),
        normalise_text(LIVE_FINAL_ANSWER),
        tool_results=[_entry(["plan_not_eligible"], tool="get_offer_details")],
    )
    assert outcome.passed is False


@pytest.mark.parametrize(
    "statement",
    (
        "您当前使用的套餐不在该活动适用的套餐范围内。",
        "您的套餐不在该活动的适用套餐范围内。",
        "该活动仅适用于特定套餐。",
    ),
)
def test_each_appended_pattern_is_grounded_and_accepted(statement: str) -> None:
    fact = _fact()
    assert (
        evaluate_required_fact(fact, normalise_text(statement), tool_results=[])
        .passed
        is False
    )
    outcome = evaluate_required_fact(
        fact,
        normalise_text(statement),
        tool_results=[_entry(["plan_not_eligible"])],
    )
    assert outcome.passed is True, outcome.detail


@pytest.mark.parametrize("pattern", V12_ACCEPTED_PATTERNS)
def test_every_original_pattern_still_passes_with_grounding(pattern: str) -> None:
    outcome = evaluate_required_fact(
        _fact(),
        normalise_text(pattern),
        tool_results=[_entry(["plan_not_eligible"])],
    )
    assert outcome.passed is True, pattern
    assert outcome.matched_pattern == pattern


@pytest.mark.parametrize(
    "statement",
    (
        "您的套餐完全符合该活动的条件。",
        "您可以直接办理这个活动。",
        "您的套餐没有问题。",
        "系统暂时无法查询。",
    ),
)
def test_an_incorrect_or_generic_statement_is_not_a_reason(statement: str) -> None:
    assert (
        evaluate_required_fact(
            _fact(),
            normalise_text(statement),
            tool_results=[_entry(["plan_not_eligible"])],
        ).passed
        is False
    )


# ---------------------------------------------------------------------------
# 3. Preserved evidence and a read-only diagnostic
# ---------------------------------------------------------------------------


def test_the_preserved_run_is_byte_unchanged_and_still_a_failure() -> None:
    manifest_path = LIVE_DIR / f"{FAILED_RUN}.json"
    trace_path = LIVE_DIR / FAILED_RUN / "trace.json"
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == FAILED_MANIFEST_SHA256
    )
    assert trace_file_sha256(trace_path) == FAILED_TRACE_SHA256

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["task_id"] == TASK_ID
    assert manifest["task_version"] == "1.2"
    assert manifest["task_hash"] == V12_TASK_HASH
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["task_success"] is False
    assert manifest["trace_sha256"] == FAILED_TRACE_SHA256

    trace = load_trace(trace_path)
    assert trace.integrity.trace_hash == FAILED_TRACE_CONTENT_SHA256
    assert trace.task_version == "1.2"
    assert trace.evaluation.success is False
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded["ineligible_reason"]["passed"] is False
    final_answer = [
        message.content for message in trace.conversation if message.role == "agent"
    ][-1]
    assert final_answer == LIVE_FINAL_ANSWER


def test_the_preserved_trace_is_not_reinterpreted_as_v1_3() -> None:
    trace = load_trace(LIVE_DIR / FAILED_RUN / "trace.json")
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


RC2_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "rc2-system-v4-evaluator-v2.3-20261122"
    / "D02"
)
RC2_RUN = "RUN-f0616dae7a094d20"
RC2_MANIFEST_SHA256 = (
    "dab571da8a902ea1e2e59e9bd754ef3bf6d8401fe548eebb9af561032043e16b"
)
RC2_TRACE_SHA256 = (
    "2b62126b1241487ad91e54c673913d1fd82eb0cc9db366a0950de62330d102ec"
)


def test_the_second_preserved_run_is_byte_unchanged_and_still_a_failure() -> None:
    manifest_path = RC2_DIR / f"{RC2_RUN}.json"
    trace_path = RC2_DIR / RC2_RUN / "trace.json"
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == RC2_MANIFEST_SHA256
    )
    assert trace_file_sha256(trace_path) == RC2_TRACE_SHA256

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["task_id"] == TASK_ID
    assert manifest["task_version"] == "1.3"
    assert manifest["task_hash"] == V13_TASK_HASH
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["task_success"] is False

    trace = load_trace(trace_path)
    assert trace.task_version == "1.3"
    assert trace.evaluation.success is False
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


def test_reevaluating_the_rc2_trace_against_v1_4_accepts_the_reason() -> None:
    trace = load_trace(RC2_DIR / RC2_RUN / "trace.json")
    before = trace_file_sha256(RC2_DIR / RC2_RUN / "trace.json")

    result = evaluate_trace(trace, task=find_task(TASK_ID))

    facts = {item["fact_id"]: item for item in result.evaluation.answer_fact_results}
    assert facts["ineligible_reason"]["passed"] is True
    assert facts["ineligible_reason"]["matched_pattern"] == "不在该优惠适用"
    assert result.evaluation.success is True
    assert result.evaluation.policy_violations == []
    assert result.trace.task_version == "1.3"
    assert trace_file_sha256(RC2_DIR / RC2_RUN / "trace.json") == before


def test_read_only_reevaluation_against_v1_3_accepts_the_reason() -> None:
    trace = load_trace(LIVE_DIR / FAILED_RUN / "trace.json")
    before = trace_file_sha256(LIVE_DIR / FAILED_RUN / "trace.json")

    result = evaluate_trace(trace, task=find_task(TASK_ID))

    facts = {item["fact_id"]: item for item in result.evaluation.answer_fact_results}
    assert facts["ineligible_reason"]["passed"] is True
    assert facts["ineligible_reason"]["matched_pattern"] == (
        "当前使用的套餐不在该活动适用"
    )
    assert result.evaluation.success is True
    assert result.evaluation.policy_violations == []
    assert result.matches_recorded is False
    assert result.trace.task_version == "1.2"
    assert trace_file_sha256(LIVE_DIR / FAILED_RUN / "trace.json") == before


# ---------------------------------------------------------------------------
# 4. The same conversation through the full runtime
# ---------------------------------------------------------------------------


def test_the_recorded_conversation_passes_under_v1_3() -> None:
    responses = [
        ProviderResponse(
            assistant_message=(
                "您好，我是中国移动客服代表。关于您这条线路的一个优惠活动，"
                "我需要先核实您的身份。请您提供身份验证令牌，好吗？"
            )
        ),
        ProviderResponse(
            assistant_message=(
                "您好，具体能否办理需要先完成身份核实。请您提供身份验证令牌，"
                "我核实后为您查询该活动的详情和资格。"
            )
        ),
        _tool(
            "verify_customer_identity",
            phone_number_id="PN103",
            identity_token="TEST_IDENTITY_C103",
        ),
        _tool("get_offer_details", offer_id="OFFER_D02_001"),
        _tool(
            "check_offer_eligibility",
            phone_number_id="PN103",
            offer_id="OFFER_D02_001",
        ),
        ProviderResponse(assistant_message=LIVE_FINAL_ANSWER),
        ProviderResponse(finish_requested=True),
    ]
    provider = _ScriptedProvider(responses)
    outcome = execute_task(
        find_task(TASK_ID),
        agent_factory=lambda: LLMBackedAgent(provider),
        agent_name="llm",
    )
    run = outcome.run
    evaluation = outcome.evaluation

    assert provider.calls == 7
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert evaluation.success, evaluation.summary
    assert evaluation.policy_violations == []
    facts = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert facts["ineligible_statement"]["passed"] is True
    assert facts["ineligible_reason"]["passed"] is True
    assert facts["false_eligible_claim"]["passed"] is True

    # The ineligible branch must not create any state.
    db = run.final_db
    assert db.enrollments == []
    assert db.consent_records == []
    assert db.sms_records == []
    assert db.do_not_call_records == []
    assert run.consent_events == []
