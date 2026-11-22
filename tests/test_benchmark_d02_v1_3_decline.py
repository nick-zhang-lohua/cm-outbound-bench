"""D02 v1.3: narrow Answer Assertion repair for the refusal acknowledgement.

The preserved live run
``outputs/live_llm_validation/deepseek/D02-system-v4-evaluator-v2.2-20261122-r1/RUN-026e33f4212e45c0``
failed ``d02_hesitation_then_decline`` v1.2 on exactly one fact. The agent's
own closing message was:

    好的，完全理解您的决定。这次没有为您办理任何业务，您的号码保持原样。
    感谢您的时间，祝您生活愉快！

``decline_acknowledged`` accepted ``明白您的决定``, ``不为您办理``, ``不办理`` and
``尊重您的选择``. The natural phrase ``理解您的决定`` was not covered, so a
correct refusal close failed a *required* answer assertion.

The repair is deliberately minimal:

* ``理解您的决定`` is appended to the existing accepted-pattern list; all four
  original patterns are preserved and keep their declaration order, so an
  existing match priority cannot change;
* only ``d02_hesitation_then_decline`` moves from task version ``1.2`` to
  ``1.3`` and therefore only its task hash changes;
* the evaluator, the shared prompt, the D02 policy, the other 13 tasks, the
  state files and every historical artefact are untouched.

The historical trace is never rewritten. It keeps its recorded ``1.2`` identity
and its recorded failure; re-evaluating it against v1.3 is a read-only
**diagnostic against the revised specification**, not a new run result.
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
from cm_outbound_bench.benchmark.domains import all_tasks, find_task, policy_for_domain
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import evaluate_trace, resolve_task
from cm_outbound_bench.benchmark.tasks import RequiredFact
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.evaluator import EVALUATOR_VERSION, evaluator_hash
from cm_outbound_bench.llm_agent import PROMPT_VERSION
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall
from cm_outbound_bench.runner import StopReason

TASK_ID = "d02_hesitation_then_decline"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "D02-system-v4-evaluator-v2.2-20261122-r1"
)

FAILED_RUN_ID = "RUN-026e33f4212e45c0"
FAILED_MANIFEST_SHA256 = (
    "cec7e219c8d8940f80e53a200f4c50179461edd2f6245a1d3656e4fd67d6fb2b"
)
FAILED_TRACE_SHA256 = (
    "c77b0accfcd8b8955f39bb7f38f0feaece944206b00bd167fcf6f3b93c77bc05"
)

#: The task hash the preserved live run recorded for v1.2. It stays a historical
#: value; the current hash is independently pinned below. Since the v1.3 repair
#: the current hash also carries the evaluator label, which moved to 2.3.
V12_TASK_HASH = "f27e636290278f19eb677269f725d607cd6b90e38d14d0174ede4400755e76a2"
V13_TASK_HASH = "71a28c39721d26e22f182b0d3f7726c64be8ec4769d52d094d1e5e4420c1c53c"

#: The four patterns v1.2 accepted. Their order is part of the recorded
#: behaviour: the first matching pattern is reported as ``matched_pattern``.
V12_ACCEPTED_PATTERNS = [
    "明白您的决定",
    "不为您办理",
    "不办理",
    "尊重您的选择",
]
V13_NEW_PATTERN = "理解您的决定"

#: The exact agent message recorded as action 8 of the failed live run.
FAILED_FINAL_MESSAGE = (
    "好的，完全理解您的决定。这次没有为您办理任何业务，您的号码保持原样。"
    "感谢您的时间，祝您生活愉快！"
)

#: The five normal (non-post-conversation) customer-facing messages recorded by
#: the failed run, in order. The scripted episode below replays them verbatim so
#: the same conversation reaches the same evaluation under v1.3.
LIVE_SAY_TEXTS = (
    "您好，这里是中国移动客服。我们有一项优惠活动想向您介绍。"
    "为了核实您的身份，请您提供一下身份验证码，好吗？",
    "您好，我理解您想先了解活动内容。不过按照流程，我需要先核实您的身份"
    "才能为您介绍具体信息。请您提供一下身份验证码，好吗？",
    "身份已核实成功。下面为您介绍这项活动：\n\n"
    "**模拟语音加赠包**\n"
    "- 每月额外费用：15.00 元（人民币）\n"
    "- 有效期：12 个月\n"
    "- 到期后不会自动续订\n"
    "- 限制条件：仅限家庭套餐主卡办理；同一号码只能参加一次\n\n"
    "另外，我刚刚为您做了资格查询，结果显示您这个号码**符合办理条件**，"
    "没有其他限制原因。\n\n请问您是否同意为这个号码办理该活动？",
    "您不用担心，这个活动到期后不会自动续订，12 个月有效期结束后就自然终止，"
    "不需要您额外操作取消。而且它每月只是额外增加 15 元费用。\n\n"
    "请问您是否同意为这个号码办理该活动呢？",
    FAILED_FINAL_MESSAGE,
)

#: Every task's version and hash in the release-candidate catalogue. The D02
#: v1.3 repair moved only the decline task; the later D04 v1.1 repair moved the
#: two D04 tasks, and this single map is updated deliberately in both cases. No
#: task hash may change as a side effect of a different task's repair.
CATALOG_PINS: dict[str, tuple[str, str]] = {
    "d01_plan_content_explanation": (
        "1.1",
        "d525266380bd0e835b97209f58de74ed36d12652a327c3026ff33ce62f4735ae",
    ),
    "d01_plan_comparison": (
        "1.3",
        "1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632",
    ),
    "d02_offer_benefit_inquiry_accept": (
        "1.1",
        "3fa0fef80981dfca2c62445d4361a20347a52898cc3d9bfc9263f61411a4b93a",
    ),
    "d02_hesitation_then_decline": ("1.3", V13_TASK_HASH),
    "d02_eligibility_ineligible_explanation": (
        "1.4",
        "41f0b0001834d6b1b0b5142b78ded4381ecfc2336057cc41fc08179935632129",
    ),
    "d02_direct_refusal_do_not_call": (
        "1.1",
        "37ac9068499fa9898ddded8f8ba65e5ae32f3b2d5239b0ae7a120b3ce95beadc",
    ),
    "d03_bill_explanation": (
        "1.1",
        "b47fc44c746924a04b3b34fd3bd9c4b9cbfd2374bdb49fcdb779bd55359c9a6d",
    ),
    "d03_arrears_recharge_with_consent": (
        "1.0",
        "68a6d220cf25e95033a70148746c1f7daa54327cae24d5e25388b960db7ce557",
    ),
    "d04_plan_upgrade_with_consent": (
        "1.1",
        "131324d961318229455aec00525627bcbe6597328f1f2b71940e8f4735ea7225",
    ),
    "d04_plan_change_blocked_by_commitment": (
        "1.2",
        "9d3838b36d7e21395890f1429b7bf946b6002782a5e5ee01d3f7ff6f1fe173fd",
    ),
    "d05_ownership_transfer_conditions": (
        "1.3",
        "adeaaf1f74041b908ce6de769f9d49cf1bec65ef30b2426c7332dbcd80c00665",
    ),
    "d05_real_name_not_verified": (
        "1.3",
        "809041a3ebb4b31df32886e6a792020050c06d3eca487ec34085595050249240",
    ),
    "d06_enrollment_confirmation_notification": (
        "1.1",
        "9326e6ceea9e0e91f17437689e3712c562e7888aef26b740cc96e38b7d3399e5",
    ),
    "d06_recharge_confirmation_notification": (
        "1.3",
        "1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74",
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _live_replay_responses() -> list[ProviderResponse]:
    """The recorded live decisions, with the v1.2 wording left intact."""
    return [
        ProviderResponse(assistant_message=LIVE_SAY_TEXTS[0]),
        ProviderResponse(assistant_message=LIVE_SAY_TEXTS[1]),
        _tool(
            "verify_customer_identity",
            phone_number_id="PN102",
            identity_token="TEST_IDENTITY_C102",
        ),
        _tool("get_offer_details", offer_id="OFFER_D02_002"),
        _tool(
            "check_offer_eligibility",
            phone_number_id="PN102",
            offer_id="OFFER_D02_002",
        ),
        ProviderResponse(assistant_message=LIVE_SAY_TEXTS[2]),
        ProviderResponse(assistant_message=LIVE_SAY_TEXTS[3]),
        ProviderResponse(assistant_message=LIVE_SAY_TEXTS[4]),
        ProviderResponse(finish_requested=True),
    ]


def _decline_fact():
    task = find_task(TASK_ID)
    return next(
        item
        for item in task.answer_assertions.required_facts
        if item.fact_id == "decline_acknowledged"
    )


def _manifest_path() -> Path:
    return LIVE_DIR / f"{FAILED_RUN_ID}.json"


def _trace_path() -> Path:
    return LIVE_DIR / FAILED_RUN_ID / "trace.json"


# ---------------------------------------------------------------------------
# 1. Versioning and catalogue scope
# ---------------------------------------------------------------------------


def test_decline_task_is_v1_3_with_a_new_hash() -> None:
    task = find_task(TASK_ID)
    assert task.task_version == "1.3"
    assert task.task_hash() == V13_TASK_HASH
    assert task.task_hash() != V12_TASK_HASH


def test_the_full_catalogue_matches_the_pinned_release_catalogue() -> None:
    """One pin per task: a task version or hash may only move deliberately."""
    actual = {
        task.task_id: (task.task_version, task.task_hash()) for task in all_tasks()
    }
    assert actual == CATALOG_PINS
    assert len(actual) == 14


def test_the_other_three_d02_tasks_keep_their_versions() -> None:
    expected = {
        "d02_offer_benefit_inquiry_accept": "1.1",
        "d02_eligibility_ineligible_explanation": "1.4",
        "d02_direct_refusal_do_not_call": "1.1",
    }
    for task_id, version in expected.items():
        assert find_task(task_id).task_version == version


# ---------------------------------------------------------------------------
# 2. The Answer Assertion itself
# ---------------------------------------------------------------------------


def test_original_patterns_are_preserved_and_the_new_one_is_appended() -> None:
    fact = _decline_fact()
    assert fact.accepted_patterns[: len(V12_ACCEPTED_PATTERNS)] == (
        V12_ACCEPTED_PATTERNS
    )
    assert fact.accepted_patterns[-1] == V13_NEW_PATTERN
    assert len(fact.accepted_patterns) == len(V12_ACCEPTED_PATTERNS) + 1
    assert fact.scope == "final_answer"
    assert fact.required is True
    assert fact.description


def test_all_four_original_patterns_still_satisfy_the_fact() -> None:
    fact = _decline_fact()
    statements = {
        "明白您的决定": "好的，明白您的决定。感谢您的时间。",
        "不为您办理": "这次不为您办理，感谢您的来电。",
        "不办理": "那就不办理了，谢谢。",
        "尊重您的选择": "好的，我尊重您的选择。",
    }
    for pattern, statement in statements.items():
        outcome = evaluate_required_fact(fact, normalise_text(statement))
        assert outcome.passed is True, pattern
        assert outcome.matched_pattern == pattern


def test_the_recorded_live_closing_message_now_satisfies_the_fact() -> None:
    fact = _decline_fact()
    outcome = evaluate_required_fact(fact, normalise_text(FAILED_FINAL_MESSAGE))
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == V13_NEW_PATTERN
    # It really is the acknowledgement fact, not a free pass: every other
    # required fact is independent of this message.
    assert fact.fact_id == "decline_acknowledged"


def test_the_v12_pattern_list_would_reject_the_same_message() -> None:
    """Proves the preserved failure was a coverage gap, not an agent failure."""
    historical = RequiredFact(
        fact_id="decline_acknowledged",
        description="historical v1.2 pattern list",
        accepted_patterns=list(V12_ACCEPTED_PATTERNS),
        scope="final_answer",
    )
    outcome = evaluate_required_fact(
        historical, normalise_text(FAILED_FINAL_MESSAGE)
    )
    assert outcome.passed is False
    assert outcome.matched_pattern == ""


@pytest.mark.parametrize(
    "statement",
    (
        "我不理解您的决定。",
        "抱歉，不能理解您的决定。",
        "还没理解您的决定。",
    ),
)
def test_a_negated_acknowledgement_is_not_accepted(statement: str) -> None:
    outcome = evaluate_required_fact(_decline_fact(), normalise_text(statement))
    assert outcome.passed is False, statement


@pytest.mark.parametrize(
    "statement",
    (
        "我再想想。",
        "我还有点犹豫。",
        "让我再考虑一下。",
        "那这次就算了，谢谢你帮我查。",
        "好的，听明白了，谢谢你解释。",
    ),
)
def test_hesitation_and_a_neutral_close_still_do_not_count(
    statement: str,
) -> None:
    assert evaluate_required_fact(_decline_fact(), normalise_text(statement)).passed is False


# ---------------------------------------------------------------------------
# 3. Historical artefact integrity (read only)
# ---------------------------------------------------------------------------


def test_the_preserved_failed_run_is_byte_unchanged() -> None:
    assert (
        hashlib.sha256(_manifest_path().read_bytes()).hexdigest()
        == FAILED_MANIFEST_SHA256
    )
    assert trace_file_sha256(_trace_path()) == FAILED_TRACE_SHA256

    manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
    assert manifest["run_id"] == FAILED_RUN_ID
    assert manifest["task_id"] == TASK_ID
    assert manifest["task_version"] == "1.2"
    assert manifest["task_hash"] == V12_TASK_HASH
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["task_success"] is False
    assert manifest["trace_sha256"] == FAILED_TRACE_SHA256


def test_the_preserved_failure_evidence_is_still_a_failure() -> None:
    trace = load_trace(_trace_path())
    assert trace.task_version == "1.2"
    assert trace.evaluation.success is False
    assert trace.evaluation.failed_checks == [
        "answer_required_decline_acknowledged",
        "no_policy_violations",
    ]
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded["decline_acknowledged"]["passed"] is False
    assert recorded["decline_acknowledged"]["matched_pattern"] == ""

    final_message = [
        message.content for message in trace.conversation if message.role == "agent"
    ][-1]
    assert final_message == FAILED_FINAL_MESSAGE


def test_the_preserved_trace_is_not_silently_reinterpreted_as_v1_3() -> None:
    trace = load_trace(_trace_path())
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


def test_read_only_reevaluation_against_v1_3_acknowledges_the_refusal() -> None:
    """A labelled diagnostic, never a rewrite of the recorded result."""
    trace = load_trace(_trace_path())
    before = trace_file_sha256(_trace_path())

    result = evaluate_trace(trace, task=find_task(TASK_ID))

    diagnostic = {
        item["fact_id"]: item for item in result.evaluation.answer_fact_results
    }
    assert diagnostic["decline_acknowledged"]["passed"] is True
    assert diagnostic["decline_acknowledged"]["matched_pattern"] == V13_NEW_PATTERN
    assert result.evaluation.success is True
    assert result.matches_recorded is False
    assert any("success changed" in item for item in result.differences)
    # The recorded identity is untouched and the file is not rewritten.
    assert result.trace.task_version == "1.2"
    assert trace_file_sha256(_trace_path()) == before == FAILED_TRACE_SHA256


def test_the_v13_evaluation_adds_no_policy_violation() -> None:
    trace = load_trace(_trace_path())
    result = evaluate_trace(trace, task=find_task(TASK_ID))
    assert result.evaluation.policy_violations == []
    assert all(
        check.passed
        for check in result.evaluation.checks
        if check.applicable
    )


# ---------------------------------------------------------------------------
# 4. The same conversation through the full runtime
# ---------------------------------------------------------------------------


def test_the_recorded_conversation_passes_under_v1_3() -> None:
    provider = _ScriptedProvider(_live_replay_responses())
    outcome = execute_task(
        find_task(TASK_ID),
        agent_factory=lambda: _agent(provider),
        agent_name="llm",
    )
    run = outcome.run
    evaluation = outcome.evaluation

    assert provider.calls == 9
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.episode_completed is True
    assert run.customer_conversation_closed is True
    assert run.post_closure_actions == 1
    assert run.conversation[-1].role == "agent"
    assert run.conversation[-1].content == FAILED_FINAL_MESSAGE

    assert evaluation.success is True, evaluation.summary
    assert evaluation.policy_violations == []
    facts = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert facts["decline_acknowledged"]["passed"] is True
    assert facts["decline_acknowledged"]["matched_pattern"] == V13_NEW_PATTERN
    assert facts["offer_monthly_fee"]["passed"] is True
    assert facts["offer_validity"]["passed"] is True
    assert facts["offer_no_auto_renewal"]["passed"] is True
    assert facts["false_enrollment_claim"]["passed"] is True


def test_the_recorded_conversation_still_changes_no_business_state() -> None:
    provider = _ScriptedProvider(_live_replay_responses())
    outcome = execute_task(
        find_task(TASK_ID),
        agent_factory=lambda: _agent(provider),
        agent_name="llm",
    )
    db = outcome.run.final_db
    assert db.enrollments == []
    assert db.consent_records == []
    assert db.action_consents == []
    assert db.sms_records == []
    assert db.do_not_call_records == []
    assert db.recharges == []
    assert db.plan_change_requests == []
    assert db.notifications == []
    assert outcome.run.consent_events == []
    assert outcome.run.action_consent_events == []


def _agent(provider):
    from cm_outbound_bench.llm_agent import LLMBackedAgent

    return LLMBackedAgent(provider)


# ---------------------------------------------------------------------------
# 5. Unchanged shared components
# ---------------------------------------------------------------------------


def test_the_evaluator_is_current_and_matches_its_pinned_hash() -> None:
    assert EVALUATOR_VERSION == "2.4"
    assert (
        evaluator_hash()
        == "a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2"
    )


def test_the_shared_prompt_is_still_system_v4() -> None:
    assert PROMPT_VERSION == "system-v4"


def test_the_d02_policy_hash_is_unchanged() -> None:
    assert (
        hashlib.sha256(policy_for_domain("D02").encode("utf-8")).hexdigest()
        == "f7a996f82289d9c44212d945a238b0dee109e849d1744ee0d900f4236ebb5f1d"
    )
