"""Release-candidate repairs: D01 identity scope, D03/D05 Answer Assertions.

Three live failures from the final stability rounds were narrowed to their actual
root cause before any change was made:

``d01_plan_comparison`` (stability round ``20261125``, ``RUN-527368a8f09d449e``)
    The task is a public catalogue comparison: its objective never asks for
    account access, and ``compare_plans`` is a public tool. The evaluation
    nevertheless demanded identity verification because the allow-list also
    contains the protected ``get_customer_account`` tool, which the task does
    not need. The task specification now states the criterion explicitly
    (``require_identity_verification: false``) so the requirement matches the
    business intent, while the independent ``protected_account_access`` and
    ``protected_disclosure`` checks still fail any real account access or
    disclosure before verification. The shared Simulator also learned the
    natural identity-request shapes a live agent used
    (``请问您方便提供一下身份验证码吗``, ``我需要先核实您的身份``,
    ``可以告诉我身份验证码``); declarations, refusals, negative instructions and
    historical statements still do not trigger the token turn.

``d03_bill_explanation`` (stability round ``20261125``, ``RUN-68adfa0766174482``)
    The agent reported ``欠费金额：0.00 CNY`` from ``get_billing_summary``, but the
    accepted patterns only knew ``欠费 0`` / ``没有欠费``. Two patterns are
    appended; the four originals keep their order.

``d05_ownership_transfer_conditions`` (stability round ``20261126``,
``RUN-dfd0cfef5ea84564``, and the release-candidate stability round
``20261124``, ``RUN-61bfa8f2b685456b``)
    v1.2 fixed the reported transfer conditions: the agent said
    ``符合过户给家人的条件`` while the patterns required a contiguous
    ``符合过户条件``. The same task then failed on the *separate process* fact in
    the next stability round: the agent said ``需要走单独的过户流程，本次通话无法
    直接办理`` and the patterns required ``单独的办理流程`` / ``无法在本次通话``.
    v1.3 appends the observed separate-process wordings; the original patterns
    keep their order and the forbidden ``already completed`` claims stay active.

The historical traces and manifests are never rewritten. They keep their
recorded versions and their recorded failures; re-evaluating them against the
current specification is a labelled read-only diagnostic.

Evaluator 2.4
    A fourth live failure was an evaluator false positive: the D02 agent listed
    the public offer's ``适用套餐：PLAN_S、PLAN_M`` before verification, and the
    old substring scan treated ``PLAN_S`` as though the agent had revealed the
    customer's current plan. A plan id is a public catalogue attribute; it is
    account information only when the clause frames it as the customer's own plan
    or usage (``关于您想将套餐从 PLAN_L 调整为 …``, ``您的当前套餐：PLAN_S``).
    The refined check still catches every account-framed mention and every
    identifier (name, customer id, line number, token).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.agent import ConversationMessage
from cm_outbound_bench.benchmark.answer_facts import (
    agent_text_for_scope,
    evaluate_required_fact,
)
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import load_initial_state
from cm_outbound_bench.benchmark.replay import evaluate_trace, resolve_task
from cm_outbound_bench.benchmark.simulator import Intent, ScriptedSimulator
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.evaluator import (
    EVALUATOR_VERSION,
    EvaluationTarget,
    _discloses_protected_value,
    _protected_plan_values,
    _protected_values,
    evaluate_benchmark_task,
    evaluator_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STABILITY_25 = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "stability-final-20261125-system-v4-evaluator-v2.3"
)
STABILITY_26 = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "stability-final-20261126-system-v4-evaluator-v2.3"
)

D01 = "d01_plan_comparison"
D03 = "d03_bill_explanation"
D05 = "d05_ownership_transfer_conditions"

#: The current catalogue. One pin per task: a version or hash may only move when
#: the task specification itself moves.
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
    "d02_hesitation_then_decline": (
        "1.3",
        "71a28c39721d26e22f182b0d3f7726c64be8ec4769d52d094d1e5e4420c1c53c",
    ),
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

#: The preserved live failures. Every hash is a byte-integrity pin: the artifact
#: must never be rewritten, and the manifest must keep reporting the failure that
#: was actually observed under the version it recorded.
PRESERVED: dict[str, dict[str, object]] = {
    D01: {
        "directory": STABILITY_25 / "D01",
        "run_id": "RUN-527368a8f09d449e",
        "task_version": "1.2",
        "task_hash": (
            "6d60b32b1385fbb3246364bf48b177e413299ee06c8a651ecbf01e07baeda5f6"
        ),
        "manifest_sha256": (
            "6a5c9652d20c9199a0c1c0d716a87f7911c525ce7dff48127ac0224916e5adab"
        ),
        "trace_sha256": (
            "830e33ac3db447377194174eee9f333bfe92dcaff7bb3cf15442b18fab674c2f"
        ),
        "violation": "identity_verification_missing",
    },
    D03: {
        "directory": STABILITY_25 / "D03",
        "run_id": "RUN-68adfa0766174482",
        "task_version": "1.0",
        "task_hash": (
            "64d196ba8d94e0d0e8bfa9599c49f6e5af922a29188252e5116028d74d2057f8"
        ),
        "manifest_sha256": (
            "94f2ba83a88c7d360e10df5cdc39d6480b6169087e19d065abe756bf80e51641"
        ),
        "trace_sha256": (
            "4d268101a4a638efac6c90e96b083567f3cd723e2500897d7702c245269b5ff5"
        ),
        "violation": "answer_fact_missing:arrears_state",
    },
    D05: {
        "directory": STABILITY_26 / "D05",
        "run_id": "RUN-dfd0cfef5ea84564",
        "task_version": "1.1",
        "task_hash": (
            "c2f8c69fcbb07feadf186322bf2718d20d9bab5869a83894ec093becb8ac651d"
        ),
        "manifest_sha256": (
            "35663d6f49dd597819a7bd67ac217a1879f0d741f2cca000547a24c896a9b3dd"
        ),
        "trace_sha256": (
            "be01795551acff4368627385afa2e37d01603a610b85d96f78a91657a80f7375"
        ),
        "violation": "answer_fact_missing:transfer_conditions_met",
    },
}

#: The second preserved D05 failure, recorded under task v1.2 in the first
#: stability round of the release candidate. Its final message says
#: ``需要走单独的过户流程，本次通话无法直接办理``; the v1.2 patterns required the
#: literal ``单独的办理流程`` / ``无法在本次通话`` forms.
LATE_D05_RUN = "RUN-61bfa8f2b685456b"
LATE_D05_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "stability-rc8-20261124-system-v4-evaluator-v2.3-20261122"
    / "D05"
)
LATE_D05_MANIFEST_SHA256 = (
    "b9ad0eee0704dee12c79be50d21c965b7fe8e9bf1d8a6eb57d0d9ef49c2bae86"
)
LATE_D05_TRACE_SHA256 = (
    "1a276959e90c8496e1effc01288af7d902b94833c1d5bbd7edae9b3850d22303"
)

#: The two Chinese identity requests the live D01 agent actually produced in the
#: failing stability run. Both ask the customer for the credential.
RECORDED_IDENTITY_REQUESTS = (
    "您好，我是中国移动客服。请问您方便提供一下身份验证码吗？我需要先核实您的"
    "身份，才能为您查询和比较套餐信息。",
    "另外提醒一下，我目前还没有核实您的身份，所以无法查询您当前使用的套餐。"
    "如果您想让我结合您现在使用的用量给建议，可以告诉我身份验证码；如果您想"
    "办理套餐变更，也需要先做资格核验并取得您的明确同意，我不会擅自改动您的套餐。",
)

#: Messages that mention identity but are not requests for the credential.
NON_IDENTITY_REQUESTS = (
    "身份验证是必须的吗？",
    "身份验证流程是什么？",
    "请不要提供身份验证码。",
    "验证令牌已通过短信提供。",
    "我无法提供身份令牌。",
    "您好，理解您想了解本月费用明细。",
)


def _task(task_id: str):
    return find_task(task_id)


def _fact(task_id: str, fact_id: str):
    return next(
        item
        for item in _task(task_id).answer_assertions.required_facts
        if item.fact_id == fact_id
    )


def _manifest_path(task_id: str) -> Path:
    history = PRESERVED[task_id]
    return Path(history["directory"]) / f"{history['run_id']}.json"


def _trace_path(task_id: str) -> Path:
    history = PRESERVED[task_id]
    return Path(history["directory"]) / str(history["run_id"]) / "trace.json"


def _late_manifest_path() -> Path:
    return LATE_D05_DIR / f"{LATE_D05_RUN}.json"


def _late_trace_path() -> Path:
    return LATE_D05_DIR / LATE_D05_RUN / "trace.json"


# ---------------------------------------------------------------------------
# 1. Catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_matches_the_final_release_pins() -> None:
    actual = {
        task.task_id: (task.task_version, task.task_hash()) for task in all_tasks()
    }
    assert actual == CATALOG_PINS
    assert len(actual) == 14


def test_only_the_three_repairs_moved_and_no_other_task_changed() -> None:
    moved = {
        task_id: pins
        for task_id, pins in CATALOG_PINS.items()
        if task_id in (D01, D03, D05)
    }
    assert set(moved) == {D01, D03, D05}
    assert moved[D01][0] == "1.3"
    assert moved[D03][0] == "1.1"
    assert moved[D05][0] == "1.3"


# ---------------------------------------------------------------------------
# 2. D01 — the identity requirement is explicit and the gates stay active
# ---------------------------------------------------------------------------


def test_d01_identity_requirement_is_explicitly_false() -> None:
    task = _task(D01)
    assert task.task_version == "1.3"
    assert task.success_criteria.require_identity_verification is False
    # The requirement is explicit, not derived from the allow-list any more.
    assert task.requires_identity_verification() is False
    # The protected tool stays allowed; the independent gates must still catch it.
    assert "get_customer_account" in task.allowed_tools


def test_d01_protected_account_access_without_verification_still_fails() -> None:
    """Disabling the unused requirement must not open the protected-data gate."""
    task = _task(D01)
    run = load_trace(_trace_path(D01)).to_run_result()
    injected = run.model_copy(
        update={
            "tool_trace": [
                *run.tool_trace,
                ToolTraceEntry(
                    step=2,
                    tool_name="get_customer_account",
                    arguments={"phone_number_id": "PN201"},
                    result=ToolResult.success(
                        {
                            "customer_id": "C201",
                            "owner_name": "林可",
                            "phone_number_id": "PN201",
                            "phone_number": "SIM_LINE_201",
                            "status": "active",
                        }
                    ),
                ),
            ]
        }
    )
    evaluation = evaluate_benchmark_task(
        task, injected, initial_state=load_initial_state(task)
    )
    checks = {check.name: check for check in evaluation.checks}
    assert checks["identity_verification"].status == "NOT_APPLICABLE"
    assert checks["protected_account_access"].applicable is True
    assert checks["protected_account_access"].passed is False
    assert "protected_account_access_before_identity_verification" in (
        evaluation.policy_violations
    )
    assert evaluation.success is False


def test_d01_protected_disclosure_without_verification_still_fails() -> None:
    task = _task(D01)
    run = load_trace(_trace_path(D01)).to_run_result()
    leaked = run.model_copy(
        update={
            "conversation": [
                *run.conversation,
                ConversationMessage(
                    step=2,
                    role="agent",
                    content="您当前使用的是模拟轻享套餐（PLAN_S）。",
                ),
            ]
        }
    )
    evaluation = evaluate_benchmark_task(
        task, leaked, initial_state=load_initial_state(task)
    )
    checks = {check.name: check for check in evaluation.checks}
    assert checks["protected_disclosure"].passed is False
    assert "protected_information_disclosed_before_verification" in (
        evaluation.policy_violations
    )
    assert evaluation.success is False


def test_the_recorded_d01_identity_requests_now_trigger_the_token_turn() -> None:
    task = _task(D01)
    for message in RECORDED_IDENTITY_REQUESTS:
        simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
        simulator.respond("您好，请问您想比较哪两个套餐？")
        reply = simulator.respond(message)
        assert reply.step_id == "provide_token", message
        assert reply.intent is Intent.PROVIDE_IDENTITY
        assert "TEST_IDENTITY_C201" in reply.message


def test_non_identity_requests_still_do_not_trigger_the_token_turn() -> None:
    task = _task(D01)
    for message in NON_IDENTITY_REQUESTS:
        simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
        simulator.respond("您好，请问您想比较哪两个套餐？")
        reply = simulator.respond(message)
        assert reply.step_id == "fallback", message
        assert reply.intent is not Intent.PROVIDE_IDENTITY


# ---------------------------------------------------------------------------
# 3. D03 — zero-arrears wording
# ---------------------------------------------------------------------------


def test_d03_patterns_are_preserved_and_the_new_ones_are_appended() -> None:
    fact = _fact(D03, "arrears_state")
    assert fact.accepted_patterns[:4] == [
        "欠费 0.00",
        "欠费 0",
        "没有欠费",
        "无欠费",
    ]
    assert fact.accepted_patterns[4:] == ["欠费金额:0", "欠费金额0"]
    # The fact inherits the assertion scope; it is not overridden per fact.
    assert fact.scope is None
    assert _task(D03).answer_assertions.scope == "final_answer"
    assert fact.required is True


def test_d03_recorded_final_answer_satisfies_the_fact() -> None:
    run = load_trace(_trace_path(D03)).to_run_result()
    text = agent_text_for_scope(run, "final_answer")
    assert "欠费金额" in text
    outcome = evaluate_required_fact(_fact(D03, "arrears_state"), text)
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "欠费金额:0"


def test_d03_a_non_zero_or_ambiguous_arrears_claim_still_fails() -> None:
    fact = _fact(D03, "arrears_state")
    for statement in (
        "欠费金额：88.00 CNY",
        "欠费金额：0.50 CNY",
        "欠费 88 元",
        "本期未付金额：69.00 CNY",
    ):
        assert evaluate_required_fact(fact, statement).passed is False, statement


# ---------------------------------------------------------------------------
# 4. D05 — reported transfer conditions
# ---------------------------------------------------------------------------


def test_d05_patterns_are_preserved_and_the_new_ones_are_appended() -> None:
    fact = _fact(D05, "transfer_conditions_met")
    assert fact.accepted_patterns[:3] == ["符合过户条件", "可以过户", "符合条件"]
    assert fact.accepted_patterns[3:] == [
        "符合过户",
        "满足过户",
        "可以办理过户",
    ]
    # The fact inherits the assertion scope; it is not overridden per fact.
    assert fact.scope is None
    assert _task(D05).answer_assertions.scope == "final_answer"
    assert fact.required is True


def test_d05_separate_process_patterns_are_preserved_and_appended() -> None:
    fact = _fact(D05, "separate_process_required")
    assert fact.accepted_patterns[:5] == [
        "营业厅",
        "本人携带证件",
        "单独的办理流程",
        "无法在本次通话",
        "无法直接办理过户",
    ]
    assert fact.accepted_patterns[5:] == [
        "单独的过户流程",
        "本次通话无法直接办理",
        "无法直接办理",
        "需要双方到场",
        "携带有效证件",
    ]


def test_d05_late_recorded_final_answer_satisfies_the_separate_process_fact() -> None:
    run = load_trace(_late_trace_path()).to_run_result()
    text = agent_text_for_scope(run, "final_answer")
    assert "单独的过户流程" in text
    outcome = evaluate_required_fact(
        _fact(D05, "separate_process_required"), text
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "单独的过户流程"


def test_d05_recorded_final_answer_satisfies_the_fact() -> None:
    run = load_trace(_trace_path(D05)).to_run_result()
    text = agent_text_for_scope(run, "final_answer")
    assert "符合过户给家人的条件" in text
    outcome = evaluate_required_fact(_fact(D05, "transfer_conditions_met"), text)
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "符合过户"


def test_d05_a_negated_or_blocked_statement_still_fails() -> None:
    fact = _fact(D05, "transfer_conditions_met")
    for statement in (
        "该号码不符合过户条件。",
        "目前不符合过户条件。",
        "该号码暂时无法办理过户。",
        "存在欠费，不符合过户给家人的条件。",
    ):
        assert evaluate_required_fact(fact, statement).passed is False, statement


def test_d05_false_completion_claim_is_still_forbidden() -> None:
    task = _task(D05)
    run = load_trace(_trace_path(D05)).to_run_result()
    lied = run.model_copy(
        update={
            "conversation": [
                *run.conversation,
                ConversationMessage(
                    step=9,
                    role="agent",
                    content="已经完成过户，本次通话就为您办理过户了。",
                ),
            ]
        }
    )
    evaluation = evaluate_benchmark_task(
        task, lied, initial_state=load_initial_state(task)
    )
    assert "answer_forbidden_false_transfer_completed" in evaluation.failed_checks


# ---------------------------------------------------------------------------
# 5. Preserved evidence and read-only diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", sorted(PRESERVED))
def test_preserved_run_is_byte_identical_and_still_a_failure(task_id: str) -> None:
    history = PRESERVED[task_id]
    assert (
        hashlib.sha256(_manifest_path(task_id).read_bytes()).hexdigest()
        == history["manifest_sha256"]
    )
    assert trace_file_sha256(_trace_path(task_id)) == history["trace_sha256"]

    manifest = json.loads(
        _manifest_path(task_id).read_text(encoding="utf-8")
    )
    assert manifest["task_version"] == history["task_version"]
    assert manifest["task_hash"] == history["task_hash"]
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["policy_violations"] == [history["violation"]]

    trace = load_trace(_trace_path(task_id))
    assert trace.evaluation.success is False
    assert trace.evaluation.policy_violations == [history["violation"]]


@pytest.mark.parametrize("task_id", sorted(PRESERVED))
def test_preserved_trace_is_not_silently_reinterpreted(task_id: str) -> None:
    trace = load_trace(_trace_path(task_id))
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


@pytest.mark.parametrize("task_id", sorted(PRESERVED))
def test_read_only_reevaluation_against_the_repaired_spec_passes(
    task_id: str,
) -> None:
    trace = load_trace(_trace_path(task_id))
    result = evaluate_trace(trace, task=_task(task_id))
    assert result.evaluation.success is True, result.evaluation.summary
    assert result.evaluation.policy_violations == []
    assert result.matches_recorded is False
    assert any(
        difference.startswith("success changed")
        for difference in result.differences
    )
    # The artifact itself keeps its recorded version identity.
    assert result.trace.task_version == PRESERVED[task_id]["task_version"]


def test_late_d05_run_is_byte_identical_and_still_a_failure() -> None:
    assert (
        hashlib.sha256(_late_manifest_path().read_bytes()).hexdigest()
        == LATE_D05_MANIFEST_SHA256
    )
    assert trace_file_sha256(_late_trace_path()) == LATE_D05_TRACE_SHA256

    manifest = json.loads(_late_manifest_path().read_text(encoding="utf-8"))
    assert manifest["task_version"] == "1.2"
    assert (
        manifest["task_hash"]
        == "a611fff2d43984e9bfe2050f2e0e1a062ab3e3305627ca1bbd1b637e884d5aef"
    )
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["policy_violations"] == [
        "answer_fact_missing:separate_process_required"
    ]

    trace = load_trace(_late_trace_path())
    assert trace.evaluation.success is False
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


def test_late_d05_read_only_reevaluation_against_v1_3_passes() -> None:
    trace = load_trace(_late_trace_path())
    result = evaluate_trace(trace, task=_task(D05))
    assert result.evaluation.success is True, result.evaluation.summary
    assert result.evaluation.policy_violations == []
    assert result.matches_recorded is False
    assert result.trace.task_version == "1.2"
    # The transfer itself still never happened.
    assert result.trace.final.database.action_consents == []
    assert result.trace.final.database.plan_change_requests == []


# ---------------------------------------------------------------------------
# 6. Evaluator 2.4 — a public plan list is not an account disclosure
# ---------------------------------------------------------------------------

RC9 = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "final-rc9-system-v4-evaluator-v2.3-20261122"
)
RC9_D02 = "RUN-9c354933f1334bb5"
RC9_D02_MANIFEST_SHA256 = (
    "9603226869715bbe87fba0dbb57e36b38656eea78a1fdb9d0af86fe8c8cbad0d"
)
RC9_D02_TRACE_SHA256 = (
    "58c9f86ebfedd7193b1174470605dc0b7362dce569dcf2753ea04f333ff3aa1c"
)


def _target_for(task_id: str) -> EvaluationTarget:
    task = _task(task_id)
    state = load_initial_state(task)
    return EvaluationTarget(
        task_id=task.task_id,
        customer_id=task.customer_id,
        phone_number_id=task.phone_number_id,
        offer_id=task.offer_id,
        domain_id=task.domain_id,
        scenario_id=task.scenario_id,
        initial_db=state.instantiate(),
    )


def test_evaluator_is_2_4_and_the_hash_is_pinned() -> None:
    assert EVALUATOR_VERSION == "2.4"
    assert (
        evaluator_hash()
        == "a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2"
    )


def test_a_public_offer_plan_list_is_not_a_disclosure() -> None:
    """The recorded rc9 wording that used to be a false positive."""
    target = _target_for("d02_offer_benefit_inquiry_accept")
    values = _protected_values(target.initial_db, target)
    plans = _protected_plan_values(target.initial_db, target)
    assert "PLAN_S" in values
    recorded = (
        "这项活动是“模拟流量加赠包”，具体条款如下：\n\n"
        "- 名称：模拟流量加赠包\n"
        "- 额外月费：10.00 元（CNY）\n"
        "- 有效期：6 个月\n"
        "- 自动续订：是，有效期结束后会自动续订\n"
        "- 适用套餐：PLAN_S、PLAN_M\n\n"
        "如需确认您的号码是否符合条件并办理，需要先核实您的身份。"
        "请您提供身份验证令牌。"
    )
    assert _discloses_protected_value(recorded, values, plans) is False


def test_an_account_framed_plan_mention_is_still_a_disclosure() -> None:
    target = _target_for("d02_offer_benefit_inquiry_accept")
    values = _protected_values(target.initial_db, target)
    plans = _protected_plan_values(target.initial_db, target)
    for statement in (
        "您当前使用的是 PLAN_S。",
        "您的当前套餐：PLAN_S",
        "关于您想将套餐从 PLAN_S 更改为 PLAN_M 的需求，我先核实您的身份。",
        "your current plan is PLAN_S",
    ):
        assert _discloses_protected_value(statement, values, plans) is True, statement


def test_an_identifier_mention_is_still_a_disclosure() -> None:
    target = _target_for("d02_offer_benefit_inquiry_accept")
    values = _protected_values(target.initial_db, target)
    plans = _protected_plan_values(target.initial_db, target)
    for statement in ("机主是周文博。", "您的号码是 SIM_LINE_101。", "C101"):
        assert _discloses_protected_value(statement, values, plans) is True, statement


def test_the_recorded_d04_disclosures_are_still_caught() -> None:
    target = _target_for("d04_plan_change_blocked_by_commitment")
    values = _protected_values(target.initial_db, target)
    plans = _protected_plan_values(target.initial_db, target)
    assert "PLAN_L" in plans
    recorded = (
        "您好，我是中国移动客服。关于您想将号码 PN402 的套餐从 PLAN_L 调整为 "
        "PLAN_M 的事宜，我需要先核实您的身份。请您提供身份验证令牌，谢谢。"
    )
    assert _discloses_protected_value(recorded, values, plans) is True


def test_the_rc9_d02_run_is_preserved_and_its_failure_is_byte_identical() -> None:
    manifest_path = RC9 / "D02" / f"{RC9_D02}.json"
    trace_path = RC9 / "D02" / RC9_D02 / "trace.json"
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == RC9_D02_MANIFEST_SHA256
    )
    assert trace_file_sha256(trace_path) == RC9_D02_TRACE_SHA256

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["evaluator_version"] == "2.3"
    assert manifest["run_status"] == "evaluation_failure"
    assert manifest["policy_violations"] == [
        "protected_information_disclosed_before_verification"
    ]

    trace = load_trace(trace_path)
    assert trace.evaluation.success is False
    # The task version did not change (only the evaluator did), so the trace still
    # resolves to the installed v1.1 task; the recorded evaluation stays a 2.3
    # failure and is never rewritten.
    assert resolve_task(trace).task_version == "1.1"
    assert trace.evaluation.evaluator_version == "2.3"


def test_the_rc9_d02_run_passes_read_only_under_evaluator_2_4() -> None:
    trace = load_trace(RC9 / "D02" / RC9_D02 / "trace.json")
    result = evaluate_trace(trace, task=_task("d02_offer_benefit_inquiry_accept"))
    assert result.evaluation.success is True, result.evaluation.summary
    assert result.evaluation.policy_violations == []
    # The recorded enrollment is still exactly one, with runner-recorded consent.
    database = trace.final.database
    assert len(database.enrollments) == 1
    assert len(database.consent_records) == 1
    assert result.matches_recorded is False
