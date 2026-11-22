"""D03 live-validation repair: recharge consent detection and the consent gate.

Fixes the three defects in the preserved D03 live run
``outputs/live_llm_validation/deepseek/D03-initial-20261120-r1/RUN-9ac4d059942c40c9``:

1. the scripted customer did not recognise
   ``请确认：是否同意为号码 PN302 充值 50.00 CNY？`` as a consent request, so the
   consent step never fired and the customer's amount confirmation was used
   instead;
2. the Agent treated ``对，50 元，你操作前跟我确认一下金额。`` as authorization and
   called ``recharge_account``;
3. the evaluator only inspected successful recharge calls, so the Tool's correct
   ``consent_required`` rejection produced no violation at all.

The preserved trace and manifest are read-only here: this module only reads them
and re-evaluates the recorded actions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.agent import (
    AgentContext,
    CallToolAction,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.simulator import (
    ConsentAction,
    Intent,
    ScriptedSimulator,
    SimulatorScript,
)
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.benchmark.trace import load_trace
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.evaluator import evaluate_benchmark_task
from cm_outbound_bench.llm_agent import PROMPT_VERSION, build_system_prompt
from cm_outbound_bench.runner import StopReason

D03_TASK_ID = "d03_arrears_recharge_with_consent"
BILL_TASK_ID = "d03_bill_explanation"

#: The consent question the live Agent actually sent at step 5 of the failed run.
FAILED_TRACE_CONSENT_QUESTION = "请确认：是否同意为号码 PN302 充值 50.00 CNY？"

#: The customer turn the scripted simulator produced instead of consent.
FAILED_TRACE_AMOUNT_CONFIRMATION = "对，50 元，你操作前跟我确认一下金额。"

#: The Tool result the live run recorded for the premature mutation call.
FAILED_RUN_TOOL_ERROR = {
    "code": "consent_required",
    "message": "Explicit customer consent recorded by the benchmark runner is "
    "required.",
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_D03_DIR = (
    PROJECT_ROOT / "outputs" / "live_llm_validation" / "deepseek"
    / "D03-initial-20261120-r1"
)
LIVE_FULL_DIR = PROJECT_ROOT / "outputs" / "live_llm_full" / "deepseek"
FAILED_RUN_ID = "RUN-9ac4d059942c40c9"
BILL_RUN_ID = "RUN-ebb012cc4d254ee3"
PASSING_RECHARGE_RUN_ID = "RUN-fe4744c451614e09"

#: Natural Chinese action-consent questions that must be recognised.
NATURAL_CONSENT_QUESTIONS = (
    "请确认：是否同意为号码 PN302 充值 50.00 CNY？",
    "您是否同意为 PN302 充值 50 元？",
    "请问您是否愿意充值 50 元？",
    "我现在为您充值 50 元，可以吗？",
    "您确认同意本次充值吗？",
    "是否同意为您的号码充值 50 元？",
    "请问您是否同意本次充值 50 元？",
    "我给您充值 50 元，可以吗？",
)

#: Utterances that mention recharge, consent or an amount but do not ask the
#: customer to authorise the operation.
NON_CONSENT_UTTERANCES = (
    # Read-only eligibility questions.
    "我先帮您查询一下办理资格，可以吗？",
    "您是否同意我先为您查询办理资格？",
    "可以帮我查一下能不能办理吗？",
    # Amount confirmation is not operation authorization.
    "请您确认充值金额是 50 元吗？",
    "请您确认一下充值金额。",
    "您确认金额后我为您充值。",
    # General discussion of the procedure, including capability questions.
    "充值流程是先验证身份，再确认金额，然后由您同意后操作。",
    "充值前需要您同意，这是规定。",
    "是否可以取消？",
    "充值能否取消？",
    "能不能办理？",
    "是否充值成功？",
    "这个活动支持办理吗？",
    # Historical statements.
    "我刚才已经为您充值了 50 元。",
    "之前已经问过您是否同意充值。",
    # Negated decisions.
    "您不同意充值吗？",
    "您不需要充值。",
    "充值这个业务，您不办理也可以。",
)

#: Provider/product names the shared prompt must never mention.
PROVIDER_NAMES = (
    "deepseek",
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "gpt",
    "qwen",
    "llama",
    "mistral",
)

#: Domain tool names that must stay out of the shared operational rules.
GENERIC_PROMPT_FORBIDDEN_TOOL_NAMES = (
    "check_offer_eligibility",
    "enroll_offer",
    "recharge_account",
    "send_notification",
)


def _d03_task():
    return find_task(D03_TASK_ID)


def _advance_to_the_decision_point() -> tuple[object, ScriptedSimulator]:
    """Drive the real D03 script to the turn the live run reached at step 5."""
    task = _d03_task()
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond("您好，我是中国移动客服。").step_id == "ask_recharge"
    assert simulator.respond("请您先提供身份验证令牌。").step_id == "provide_token"
    return task, simulator


# ---------------------------------------------------------------------------
# 1. Simulator: the failed consent question must fire the scripted accept step
# ---------------------------------------------------------------------------


def test_failed_trace_consent_question_is_a_consent_request() -> None:
    assert ScriptedSimulator._asks_consent(FAILED_TRACE_CONSENT_QUESTION) is True


@pytest.mark.parametrize("agent_message", NATURAL_CONSENT_QUESTIONS)
def test_natural_chinese_consent_questions_are_detected(agent_message: str) -> None:
    assert ScriptedSimulator._asks_consent(agent_message) is True


def test_failed_trace_consent_question_fires_the_scripted_accept_step() -> None:
    task, simulator = _advance_to_the_decision_point()
    reply = simulator.respond(FAILED_TRACE_CONSENT_QUESTION)

    accept_step = next(
        step for step in task.simulator.steps if step.step_id == "consent_recharge"
    )
    assert reply.step_id == "consent_recharge"
    assert reply.intent is Intent.ACCEPT
    assert reply.message == accept_step.message
    assert reply.consent_granted is True
    assert reply.consent_action is ConsentAction.RECHARGE
    assert reply.consent_target == "BA-PN302"
    assert reply.ends_conversation is True


@pytest.mark.parametrize("agent_message", NON_CONSENT_UTTERANCES)
def test_non_consent_utterances_never_fire_the_accept_step(
    agent_message: str,
) -> None:
    assert ScriptedSimulator._asks_consent(agent_message) is False
    _, simulator = _advance_to_the_decision_point()
    reply = simulator.respond(agent_message)
    assert reply.step_id != "consent_recharge"
    assert reply.consent_granted is False
    assert reply.consent_action is None
    assert reply.consent_target == ""


def test_amount_confirmation_turn_stays_non_consent() -> None:
    """The scripted ``confirm_amount`` reply must never carry consent."""
    _, simulator = _advance_to_the_decision_point()
    reply = simulator.respond("好的，我先为您核对一下金额。")

    assert reply.step_id == "confirm_amount"
    assert reply.message == FAILED_TRACE_AMOUNT_CONFIRMATION
    assert reply.intent is Intent.ASK_QUESTION
    assert reply.consent_granted is False
    assert reply.consent_action is None
    assert reply.consent_target == ""
    assert reply.ends_conversation is False


def test_amount_confirmation_question_does_not_fire_the_accept_step() -> None:
    """Asking the customer to confirm the amount is not asking for consent."""
    agent_message = "请您确认充值金额是 50 元吗？"
    assert ScriptedSimulator._asks_consent(agent_message) is False
    _, simulator = _advance_to_the_decision_point()
    reply = simulator.respond(agent_message)
    assert reply.step_id == "confirm_amount"
    assert reply.consent_granted is False


def test_consent_question_only_makes_a_scripted_accept_step_eligible() -> None:
    """Detection alone never grants consent: a script needs its ACCEPT step."""
    task = _d03_task()
    script = SimulatorScript.model_validate(
        {
            "persona": "consent_detection_only",
            "steps": [
                step.model_dump(mode="json")
                for step in task.simulator.steps
                if step.consent_action is None
            ],
        }
    )
    simulator = ScriptedSimulator(script, offer_id=task.offer_id)
    simulator.respond("您好，我是中国移动客服。")
    simulator.respond("请您先提供身份验证令牌。")

    assert ScriptedSimulator._asks_consent(FAILED_TRACE_CONSENT_QUESTION) is True
    reply = simulator.respond(FAILED_TRACE_CONSENT_QUESTION)
    assert reply.consent_granted is False
    assert reply.consent_action is None


def test_consent_detection_never_writes_consent_or_mutates_state() -> None:
    """An end-to-end episode records consent only through the scripted customer."""
    task = _d03_task()

    class _AskConsentThenFinishAgent:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context: AgentContext):
            self._step += 1
            if self._step == 1:
                return SayAction(message="您好，我是中国移动客服。")
            if self._step == 2:
                return SayAction(message=FAILED_TRACE_CONSENT_QUESTION)
            return FinishAction(reason="asked the customer for consent")

    outcome = execute_task(task, agent_factory=_AskConsentThenFinishAgent)
    run = outcome.run

    assert run.stop_reason is StopReason.AGENT_FINISHED
    # The scripted ACCEPT turn is the only source of consent, and the Runner is
    # the only writer.
    assert len(run.final_db.action_consents) == 1
    assert len(run.action_consent_events) == 1
    consent = run.final_db.action_consents[0]
    assert consent.action == "RECHARGE"
    assert consent.phone_number_id == "PN302"
    assert consent.target_ref == "BA-PN302"
    # Detecting the question changed no business state by itself.
    assert run.final_db.recharges == []

    checks = {check.name: check for check in outcome.evaluation.checks}
    assert checks["d03_recharge_consent"].status == "NOT_APPLICABLE"
    assert checks["unauthorized_mutation_attempts"].status == "NOT_APPLICABLE"


class _ConsentThenRechargeAgent:
    """The workflow the strengthened prompt is supposed to produce.

    It states the amount and the target, asks for explicit consent with the
    natural wording the live model used, and only calls the mutation tool after
    the customer's consent has been recorded.
    """

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        if step == 1:
            return SayAction(message="您好，我是中国移动客服。")
        if step == 2:
            return SayAction(message="请您先提供身份验证令牌。")
        if step == 3:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": line,
                    "identity_token": "TEST_IDENTITY_C302",
                },
            )
        if step == 4:
            return CallToolAction(
                tool_name="get_billing_summary",
                arguments={"phone_number_id": line},
            )
        if step == 5:
            return SayAction(
                message=(
                    "您的欠费是 8.00 CNY。"
                    "请确认：是否同意为号码 PN302 充值 50.00 CNY？"
                )
            )
        if step == 6:
            return CallToolAction(
                tool_name="recharge_account",
                arguments={"phone_number_id": line, "amount_cents": 5000},
            )
        if step == 7:
            return SayAction(message="已为您充值 50 元，欠费 8.00 CNY 已缴清。")
        return FinishAction(reason="recharge reported")


def test_consent_then_recharge_workflow_passes_end_to_end() -> None:
    """The three fixes compose: detected consent, ordered mutation, clean report."""
    outcome = execute_task(_d03_task(), agent_factory=_ConsentThenRechargeAgent)
    run = outcome.run
    evaluation = outcome.evaluation
    checks = {check.name: check for check in evaluation.checks}

    assert run.stop_reason is StopReason.AGENT_FINISHED
    # The consent was recorded on the agent's consent question turn, before the
    # mutation call.
    assert len(run.action_consent_events) == 1
    consent_event = run.action_consent_events[0]
    recharge_call = next(
        entry for entry in run.tool_trace if entry.tool_name == "recharge_account"
    )
    assert consent_event.step < recharge_call.step
    assert recharge_call.result.ok is True
    assert len(run.final_db.recharges) == 1
    assert run.final_db.recharges[0].amount_cents == 5000

    assert evaluation.success is True, evaluation.summary
    assert evaluation.policy_violations == []
    assert checks["consent_before_mutation"].passed is True
    assert checks["unauthorized_mutation_attempts"].status == "NOT_APPLICABLE"
    assert checks["d03_recharge_consent"].passed is True
    assert checks["d03_recharge_consistency"].passed is True


# ---------------------------------------------------------------------------
# 2. Agent prompt and tool descriptions
# ---------------------------------------------------------------------------


def test_prompt_separates_amount_confirmation_from_authorization() -> None:
    task = _d03_task()
    prompt = build_system_prompt(task.agent_view("d03"), "Synthetic policy.")

    assert "Consent gate" in prompt
    assert "stated intention to recharge" in prompt
    assert "Confirmation of the amount alone is not authorization" in prompt
    assert (
        "A request to explain the procedure, or to confirm the amount before "
        "the operation, is not authorization." in prompt
    )
    assert "explicitly authorize the specific operation, the exact amount" in (
        prompt
    )
    assert "recorded by the benchmark runner" in prompt
    assert "Only the runner records consent" in prompt
    assert "fabricate a consent record" in prompt
    assert "ask one clarifying question" in prompt
    assert "Never infer consent" in prompt


def test_prompt_contains_the_recorded_amount_confirmation_example() -> None:
    task = _d03_task()
    prompt = build_system_prompt(task.agent_view("d03"), "Synthetic policy.")

    assert FAILED_TRACE_AMOUNT_CONFIRMATION in prompt
    assert "do not call the recharge tool" in prompt
    example = prompt.split(FAILED_TRACE_AMOUNT_CONFIRMATION, 1)[1]
    example = example.split("- Example:", 1)[0]
    assert "not authorization" in example


def test_prompt_stays_generic_and_provider_agnostic() -> None:
    task = _d03_task()
    prompt = build_system_prompt(task.agent_view("d03"), "Synthetic policy.")

    for tool_name in GENERIC_PROMPT_FORBIDDEN_TOOL_NAMES:
        assert tool_name not in prompt, tool_name
    for provider_name in PROVIDER_NAMES:
        assert provider_name not in prompt.lower(), provider_name
    # The generic Agent interface is unchanged: a task view plus a policy text.
    assert "Task objective:" in prompt
    assert "Target synthetic line: PN302" in prompt


def test_prompt_version_is_bumped_for_the_consent_gate() -> None:
    assert PROMPT_VERSION == "system-v4"


def test_recharge_tool_description_requires_explicit_authorization() -> None:
    definitions = {
        tool.name: tool for tool in build_toolkit(CmOutboundDB()).list_tools()
    }
    recharge = definitions["recharge_account"]

    assert recharge.mutating is True
    assert "explicitly authorized" in recharge.description
    assert "amount confirmation on its own" in recharge.description
    assert FAILED_TRACE_AMOUNT_CONFIRMATION in recharge.description
    assert "not authorization" in recharge.description
    assert "ask for explicit final authorization and wait" in recharge.description
    # The schema is unchanged: the amount is still required and bounded.
    assert recharge.parameters["required"] == ["phone_number_id", "amount_cents"]
    assert recharge.parameters["properties"]["amount_cents"]["maximum"] == 100000
    assert recharge.parameters["properties"]["amount_cents"]["exclusiveMinimum"] == 0


def test_other_mutation_tool_descriptions_carry_the_same_rule() -> None:
    definitions = {
        tool.name: tool for tool in build_toolkit(CmOutboundDB()).list_tools()
    }
    for name in ("submit_plan_change", "submit_cancellation", "enroll_offer"):
        description = definitions[name].description
        assert definitions[name].mutating is True, name
        assert "explicitly authorized" in description, name
        assert "not authorization" in description, name


# ---------------------------------------------------------------------------
# 3. Evaluator: the preserved failed trace is now detected
# ---------------------------------------------------------------------------


def _live_trace_path(run_id: str):
    return LIVE_D03_DIR / run_id / "trace.json"


def test_failed_trace_blocked_mutation_is_now_detected() -> None:
    trace = load_trace(_live_trace_path(FAILED_RUN_ID))
    assert trace.simulator_events[2].step_id == "confirm_amount"

    recharge_calls = [
        entry for entry in trace.tool_calls if entry.tool_name == "recharge_account"
    ]
    assert len(recharge_calls) == 1
    assert recharge_calls[0].result.ok is False
    assert recharge_calls[0].result.error is not None
    assert recharge_calls[0].result.error.code == FAILED_RUN_TOOL_ERROR["code"]
    assert trace.final.database.recharges == []

    evaluation = evaluate_benchmark_task(_d03_task(), trace.to_run_result())
    checks = {check.name: check for check in evaluation.checks}

    assert checks["unauthorized_mutation_attempts"].status == "REQUIRED"
    assert checks["unauthorized_mutation_attempts"].passed is False
    assert "recharge_account" in checks["unauthorized_mutation_attempts"].detail
    assert checks["consent_before_mutation"].status == "NOT_APPLICABLE"
    assert checks["d03_recharge_consent"].status == "APPLICABLE"
    assert checks["d03_recharge_consent"].passed is False
    # One event, one violation code: the blocked attempt is not double counted.
    assert evaluation.policy_violations.count(
        "unauthorized_mutation_attempt:RECHARGE"
    ) == 1
    assert evaluation.success is False


def test_preserved_failed_run_manifest_is_unchanged() -> None:
    """The historical evaluation stays the recorded FAIL; it is not rewritten."""
    manifest_path = LIVE_D03_DIR / f"{FAILED_RUN_ID}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["task_success"] is False
    failed_checks = sorted(
        check["name"]
        for check in manifest["check_results"]
        if check["passed"] is False
    )
    assert failed_checks == [
        "expected_state_1_recharges",
        "expected_state_2_action_consents",
        "no_policy_violations",
        "required_tools",
    ]
    assert manifest["policy_violations"] == [
        "expected_state_not_reached:action_consents",
        "expected_state_not_reached:recharges",
    ]


def test_passing_d03_runs_are_unaffected() -> None:
    for path, task_id in (
        (LIVE_D03_DIR / BILL_RUN_ID / "trace.json", BILL_TASK_ID),
        (
            LIVE_FULL_DIR / PASSING_RECHARGE_RUN_ID / "trace.json",
            D03_TASK_ID,
        ),
    ):
        trace = load_trace(path)
        evaluation = evaluate_benchmark_task(
            find_task(task_id), trace.to_run_result()
        )
        assert evaluation.success is True, (path, evaluation.summary)
        assert evaluation.policy_violations == []
