"""D04 live-validation repair: objective leakage and consent-question coverage.

Two live failures under the frozen system-v4 / evaluator-2.2 configuration:

``RUN-0c1a699e2ea64dd8`` ``d04_plan_upgrade_with_consent`` v1.0
    The agent asked a clear consent question —
    ``请问您是否确认将 PN401 的套餐变更为 PLAN_L（月费 99 元）？您确认后我再提交。``
    — and the scripted customer returned ``fallback`` instead of the scripted
    ACCEPT turn. The episode then looped through ``ask_effect`` and two more
    fallbacks into the recovery turn, so no consent was recorded, no plan change
    was submitted and the required-tool/state checks failed. Root cause: the
    Chinese consent-question frame allows at most 8 closed-vocabulary filler
    tokens between the decision word and the action, and ``将 PN401 的套餐`` was
    not expressible in that vocabulary, so a legitimate consent question was not
    recognised. This is a shared-Simulator coverage defect, not an agent failure.

``RUN-2c154e3bea444647`` ``d04_plan_change_blocked_by_commitment`` v1.0
    The only failed check was ``protected_disclosure``: the agent's opening
    message said ``关于您想将号码 PN402 的套餐从 PLAN_L 调整为 PLAN_M``, naming
    the customer's *current* plan (PLAN_L) before identity verification. The
    evaluator is correct — the core policy forbids revealing the plan identifier
    before verification — but the agent-visible task objective itself named
    ``PLAN_L`` (and the commitment end date), so the task invited the violation.

The repair keeps both safety properties and touches only what is defective:

* the Simulator's closed filler vocabulary gains the object markers and plan
  nouns needed to parse ``将 <line> 的套餐变更为 <plan>``; the token bound stays
  at 8 and every non-consent lookalike still fails;
* the two D04 objectives no longer name the current plan or the commitment date,
  and both tasks move to version ``1.1`` (the blocked task later moves to
  ``1.2`` in the live answer-assertion repair for its ``blocked_statement``
  wording);
* the evaluator, the prompt, the policies and the other 12 tasks are untouched.

The preserved traces are read-only. They keep their recorded ``1.0`` identity and
their recorded failures; re-evaluating them against ``1.1`` is a labelled
diagnostic, never a rewrite.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import evaluate_trace, resolve_task
from cm_outbound_bench.benchmark.simulator import Intent, ScriptedSimulator
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall
from cm_outbound_bench.runner import StopReason

UPGRADE = "d04_plan_upgrade_with_consent"
BLOCKED = "d04_plan_change_blocked_by_commitment"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "final-system-v4-evaluator-v2.2-20261122-r1"
    / "D04"
)

UPGRADE_RUN = "RUN-0c1a699e2ea64dd8"
BLOCKED_RUN = "RUN-2c154e3bea444647"

HISTORICAL = {
    UPGRADE_RUN: {
        "task_id": UPGRADE,
        "manifest_sha256": (
            "aaafe5bef2e6f211dcbe72e931afb0fd3fc3ad8cf607e5b70ac17ca0c2ab136f"
        ),
        "trace_sha256": (
            "23a23b7ae1d84a7b066d6f989c117467b4ef64d682c21e75774f100fa0d27516"
        ),
        "task_hash": (
            "1b6d39e75a6721447824da4243346c2cb59ed6c4266cd1d3da5f38276d4d6da0"
        ),
    },
    BLOCKED_RUN: {
        "task_id": BLOCKED,
        "manifest_sha256": (
            "c0349173cae8d2c147977134b1644653d8c445bc6cc625de9e0251481231e025"
        ),
        "trace_sha256": (
            "0adfc10c853604088a2c6830aa78a42f6f1619b19fccf892a0511b10cc216eeb"
        ),
        "task_hash": (
            "bca802a7761c8667f613c6f9ed091d05d9b87fc16473440d19ca1d8daa624465"
        ),
    },
}

CURRENT = {
    UPGRADE: (
        "1.1",
        "131324d961318229455aec00525627bcbe6597328f1f2b71940e8f4735ea7225",
    ),
    BLOCKED: (
        "1.2",
        "9d3838b36d7e21395890f1429b7bf946b6002782a5e5ee01d3f7ff6f1fe173fd",
    ),
}

#: The exact consent question from ``RUN-0c1a699e2ea64dd8`` step 7. It was
#: ignored by the v1.0 simulator.
LIVE_CONSENT_QUESTION = (
    "请问您是否确认将 PN401 的套餐变更为 PLAN_L（月费 99 元）？您确认后我再提交。"
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
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


def _run(task_id: str, responses: list[ProviderResponse]):
    provider = _ScriptedProvider(responses)
    outcome = execute_task(
        find_task(task_id),
        agent_factory=lambda: LLMBackedAgent(provider),
        agent_name="llm",
    )
    return provider, outcome


def _upgrade_episode() -> list[ProviderResponse]:
    """A complete, policy-compliant D04 upgrade driven by the live wording."""
    return [
        # 1: greet without naming the current plan, ask for the token.
        ProviderResponse(
            assistant_message=(
                "您好，我是中国移动客服，可以帮您把套餐升级到 PLAN_L。"
                "为了核实机主身份，请提供您的测试身份令牌。"
            )
        ),
        # 2: the customer supplies the token.
        ProviderResponse(assistant_message="谢谢，我先核实您的身份。"),
        _tool(
            "verify_customer_identity",
            phone_number_id="PN401",
            identity_token="TEST_IDENTITY_C401",
        ),
        _tool("get_subscription_status", phone_number_id="PN401"),
        _tool(
            "check_plan_change_eligibility",
            phone_number_id="PN401",
            to_plan_id="PLAN_L",
        ),
        # 3: report eligibility and ask the exact live consent question.
        ProviderResponse(
            assistant_message=(
                "身份已核实。您当前套餐状态正常、没有合约限制，"
                "可以变更为 PLAN_L（月费 99 元）。" + LIVE_CONSENT_QUESTION
            )
        ),
        # 4: the customer accepted and closed the call; submit the change.
        _tool(
            "submit_plan_change",
            phone_number_id="PN401",
            to_plan_id="PLAN_L",
        ),
        # 5: report the actual outcome from the tool result.
        ProviderResponse(
            assistant_message=(
                "已为您提交套餐变更，目标套餐 PLAN_L，状态已受理，"
                "生效日期 2026-12-01。"
            )
        ),
        ProviderResponse(finish_requested=True),
    ]


def _blocked_episode() -> list[ProviderResponse]:
    return [
        ProviderResponse(
            assistant_message=(
                "您好，我是中国移动客服，您想办理套餐变更。"
                "为了核实机主身份，请提供您的测试身份令牌。"
            )
        ),
        ProviderResponse(assistant_message="谢谢，我先核实您的身份。"),
        _tool(
            "verify_customer_identity",
            phone_number_id="PN402",
            identity_token="TEST_IDENTITY_C402",
        ),
        _tool(
            "check_plan_change_eligibility",
            phone_number_id="PN402",
            to_plan_id="PLAN_M",
        ),
        _tool("get_subscription_status", phone_number_id="PN402"),
        ProviderResponse(
            assistant_message=(
                "身份已核实。资格检查显示暂不可变更：您的号码仍在合约期内"
                "（active_commitment），合约到期日为 2027-03-31，"
                "因此无法为您办理降至 PLAN_M 的变更，我也不会提交申请。"
            )
        ),
        ProviderResponse(finish_requested=True),
    ]


# ---------------------------------------------------------------------------
# 1. Versioning and objective hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", (UPGRADE, BLOCKED))
def test_d04_tasks_carry_the_repaired_version_and_hash(task_id: str) -> None:
    task = find_task(task_id)
    version, task_hash = CURRENT[task_id]
    assert task.task_version == version
    assert task.task_hash() == task_hash
    for history in HISTORICAL.values():
        if history["task_id"] == task_id:
            assert task.task_hash() != history["task_hash"]


def test_d04_versions_and_hashes_are_pinned() -> None:
    assert {task.task_id for task in all_tasks() if task.domain_id == "D04"} == {
        UPGRADE,
        BLOCKED,
    }
    for task_id, (version, task_hash) in CURRENT.items():
        task = find_task(task_id)
        assert task.task_version == version
        assert task.task_hash() == task_hash


def test_the_other_domains_keep_their_versions() -> None:
    """Only the two D04 tasks moved; the other four domains are untouched."""
    expected = {
        "D01": {"1.1", "1.3"},
        "D02": {"1.1", "1.3", "1.4"},
        "D03": {"1.0", "1.1"},
        "D05": {"1.3"},
        "D06": {"1.1", "1.3"},
    }
    for domain_id, versions in expected.items():
        actual = {
            task.task_version
            for task in all_tasks()
            if task.domain_id == domain_id
        }
        assert actual == versions, domain_id


def test_upgrade_objective_does_not_name_the_customers_current_plan() -> None:
    task = find_task(UPGRADE)
    view = task.agent_view(task.domain_id.lower())
    for text in (task.agent_objective, view.objective):
        assert "PLAN_M" not in text
        assert "模拟畅享套餐" not in text
        # The public target plan may be named: it is the customer's request.
        assert "PLAN_L" in text


def test_blocked_objective_does_not_name_the_current_plan_or_commitment_date() -> None:
    task = find_task(BLOCKED)
    view = task.agent_view(task.domain_id.lower())
    for text in (task.agent_objective, view.objective):
        assert "PLAN_L" not in text
        assert "模拟尊享套餐" not in text
        assert "2027-03-31" not in text
        assert "PLAN_M" in text


def test_the_agent_view_contains_no_other_account_state() -> None:
    for task_id in (UPGRADE, BLOCKED):
        view = find_task(task_id).agent_view()
        payload = view.model_dump_json()
        assert "TEST_IDENTITY" not in payload
        assert "SIM_LINE" not in payload


# ---------------------------------------------------------------------------
# 2. The Simulator now recognises a natural consent question
# ---------------------------------------------------------------------------


def test_the_live_consent_question_is_recognised() -> None:
    assert ScriptedSimulator._asks_consent(LIVE_CONSENT_QUESTION) is True


@pytest.mark.parametrize(
    "message",
    (
        "请问您是否确认将 PN401 的套餐变更为 PLAN_L？",
        "您是否同意将套餐升级到 PLAN_L？",
        "您是否确认把套餐变更为 PLAN_L？",
        "请问您是否同意将您当前的 PLAN_M 套餐变更为 PLAN_L？",
        "您是否确认办理套餐变更到 PLAN_L？",
    ),
)
def test_natural_plan_change_consent_questions_are_recognised(
    message: str,
) -> None:
    assert ScriptedSimulator._asks_consent(message) is True


@pytest.mark.parametrize(
    "message",
    (
        "您可以查询办理资格，我这边帮您查一下。",
        "请您确认充值金额是 50 元吗？",
        "这个活动支持办理。",
        "我已经为您办理了套餐变更。",
        "您不要办理这个活动。",
        "您是否同意我们的服务条款？",
        "您是否同意将投诉提交到客服？",
        "您是否同意将您的号码过户给他？",
        "您可以变更套餐。",
        "您是否想了解一下套餐变更流程？",
    ),
)
def test_non_consent_lookalikes_still_do_not_grant_consent(
    message: str,
) -> None:
    assert ScriptedSimulator._asks_consent(message) is False


def test_the_scripted_accept_turn_fires_for_the_live_question() -> None:
    task = find_task(UPGRADE)
    simulator = ScriptedSimulator(task.simulator, offer_id=None)
    assert simulator.respond("您好，我是中国移动客服。").step_id == "ask_upgrade"
    assert (
        simulator.respond("请提供您的身份验证令牌。").step_id == "provide_token"
    )
    reply = simulator.respond(LIVE_CONSENT_QUESTION)
    assert reply.step_id == "consent_upgrade"
    assert reply.intent is Intent.ACCEPT
    assert reply.consent_granted is True
    assert reply.consent_action is not None
    assert reply.consent_action.value == "PLAN_CHANGE"
    assert reply.consent_target == "PLAN_L"
    assert reply.ends_conversation is True


# ---------------------------------------------------------------------------
# 3. Full episodes under v1.1
# ---------------------------------------------------------------------------


def test_upgrade_episode_records_one_consent_and_one_plan_change() -> None:
    provider, outcome = _run(UPGRADE, _upgrade_episode())
    run = outcome.run
    evaluation = outcome.evaluation

    assert provider.calls == 9
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.customer_conversation_closed is True
    assert evaluation.success, evaluation.summary
    assert evaluation.policy_violations == []

    assert len(run.final_db.plan_change_requests) == 1
    request = run.final_db.plan_change_requests[0]
    assert request.phone_number_id == "PN401"
    assert request.to_plan_id == "PLAN_L"
    assert request.status == "APPROVED"
    assert request.effective_on == "2026-12-01"
    assert len(run.final_db.action_consents) == 1
    assert run.final_db.action_consents[0].action == "PLAN_CHANGE"
    assert run.final_db.action_consents[0].target_ref == "PLAN_L"
    assert run.action_consent_events

    facts = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert facts["target_plan"]["passed"] is True
    assert facts["effective_date"]["passed"] is True
    assert facts["instant_activation_claim"]["passed"] is True


def test_upgrade_episode_never_discloses_the_current_plan_first() -> None:
    _, outcome = _run(UPGRADE, _upgrade_episode())
    first_agent_message = next(
        message.content
        for message in outcome.run.conversation
        if message.role == "agent"
    )
    assert "PLAN_M" not in first_agent_message
    assert "模拟畅享套餐" not in first_agent_message
    assert (
        "protected_information_disclosed_before_verification"
        not in outcome.evaluation.policy_violations
    )


def test_blocked_episode_explains_the_commitment_without_disclosing_first() -> None:
    _, outcome = _run(BLOCKED, _blocked_episode())
    evaluation = outcome.evaluation

    assert evaluation.success, evaluation.summary
    assert evaluation.policy_violations == []
    assert outcome.run.final_db.plan_change_requests == []
    assert outcome.run.final_db.action_consents == []
    facts = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert facts["blocked_statement"]["passed"] is True
    assert facts["blocking_reason"]["passed"] is True


def test_the_protected_disclosure_check_still_catches_the_old_behaviour() -> None:
    """Fixing the objective did not weaken the evaluator."""

    class _LeakyAgent:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context):
            from cm_outbound_bench.agent import FinishAction, SayAction

            self._step += 1
            if self._step == 1:
                return SayAction(
                    message=(
                        "您好，您目前使用的是 PLAN_M（模拟畅享套餐），"
                        "想升级到 PLAN_L，我先核实您的身份。"
                    )
                )
            return FinishAction(reason="done")

    outcome = execute_task(
        find_task(UPGRADE), agent_factory=_LeakyAgent, strict=False
    )
    assert outcome.evaluation.success is False
    assert (
        "protected_information_disclosed_before_verification"
        in outcome.evaluation.policy_violations
    )
    failed = {check.name for check in outcome.evaluation.checks if not check.passed}
    assert "protected_disclosure" in failed


# ---------------------------------------------------------------------------
# 4. Historical evidence stays a failure
# ---------------------------------------------------------------------------


def test_the_preserved_d04_runs_are_byte_unchanged() -> None:
    for run_id, history in HISTORICAL.items():
        manifest_path = LIVE_DIR / f"{run_id}.json"
        trace_path = LIVE_DIR / run_id / "trace.json"
        assert (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            == history["manifest_sha256"]
        )
        assert trace_file_sha256(trace_path) == history["trace_sha256"]

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["task_id"] == history["task_id"]
        assert manifest["task_version"] == "1.0"
        assert manifest["task_hash"] == history["task_hash"]
        assert manifest["run_status"] == "evaluation_failure"
        assert manifest["task_success"] is False


def test_the_preserved_d04_traces_are_not_reinterpreted_implicitly() -> None:
    for run_id in HISTORICAL:
        trace = load_trace(LIVE_DIR / run_id / "trace.json")
        assert trace.task_version == "1.0"
        assert trace.evaluation.success is False
        with pytest.raises(TraceSchemaError):
            resolve_task(trace)


def test_reevaluating_the_blocked_trace_keeps_the_recorded_disclosure() -> None:
    """The recorded words really revealed the current plan before verification."""
    trace = load_trace(LIVE_DIR / BLOCKED_RUN / "trace.json")
    result = evaluate_trace(trace, task=find_task(BLOCKED))
    assert result.evaluation.success is False
    assert (
        "protected_information_disclosed_before_verification"
        in result.evaluation.policy_violations
    )


def test_reevaluating_the_upgrade_trace_still_reports_the_customer_gap() -> None:
    """The historical episode still has no consent and no plan change."""
    trace = load_trace(LIVE_DIR / UPGRADE_RUN / "trace.json")
    result = evaluate_trace(trace, task=find_task(UPGRADE))
    assert result.evaluation.success is False
    failed = {check.name for check in result.evaluation.checks if not check.passed}
    assert "required_tools" in failed
    assert "expected_state_1_plan_change_requests" in failed
    assert result.trace.task_version == "1.0"
