"""D01 regression: corrected task specification, money matching and history.

Four confirmed D01 defects are pinned here:

1. the current-plan task required an unnecessary catalogue call (``list_plans``);
2. its objective described catalogue browsing instead of the customer's account;
3. the exact live answer ``月费：29.00 元/月`` did not satisfy the fee fact;
4. the recorded v1.0 run must never be silently reinterpreted as v1.1.

The historical trace is read-only. Any evaluation against the revised
specification is labelled a **diagnostic against the revised specification**,
never a historical v1.0 PASS and never a formal v1.1 run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.answer_facts import (
    canonicalize_decimal_amounts,
    evaluate_forbidden_fact,
    evaluate_required_fact,
    find_affirmed_occurrences,
    normalise_text,
)
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import (
    find_task,
    policy_for_domain,
)
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import evaluate_trace
from cm_outbound_bench.benchmark.tasks import RequiredFact
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.evaluator import evaluate_benchmark_task
from cm_outbound_bench.llm_agent import build_system_prompt

D01_TASK_ID = "d01_plan_content_explanation"
D01_COMPARISON_TASK_ID = "d01_plan_comparison"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_TRACE_PATH = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "D01"
    / "RUN-c0b1e6f723784a4f"
    / "trace.json"
)
LIVE_MANIFEST_PATH = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "D01"
    / "RUN-c0b1e6f723784a4f.json"
)

#: Integrity values of the preserved v1.0 artifact. They must never change.
LIVE_TRACE_FILE_SHA256 = (
    "dbc76fa603bb947ca66a4a5accadf772b9fb95a42eecbc62a8dedd7bebde6cce"
)
LIVE_TRACE_CONTENT_SHA256 = (
    "8cc040d43fe88687cfbc26d2dec097153bf3f6375ba3c57cd114a781bef3e543"
)
LIVE_MANIFEST_SHA256 = (
    "b36ca3e32cee056ae4c1e5897d5862354c99dcec14be944fe17a5ab230496e57"
)
LIVE_V1_TASK_HASH = (
    "a577f0bc4195cf38cd6aab45dbe3c22f5ff86958852ad6d4ac94fc0b5ed70f05"
)
#: The comparison task keeps its objective, tool allow-list and answer facts, but
#: its scripted customer behaviour is repaired separately: ``done.min_turn`` moved
#: from 4 to 3 so a completed comparison closes the conversation on the next
#: customer turn instead of falling back. That repair bumped the task to 1.1.
#: A later live repair moved the assertion scope to ``any_agent_message`` (the
#: comparison is delivered in the comparison turn, not necessarily repeated in
#: the closing line) and bumped the task to 1.2. The v1.3 repair makes the
#: identity requirement explicit and false: this is a public catalogue comparison
#: that never requires account access, and the protected-access and
#: protected-disclosure checks still guard any real account tool use. Earlier
#: hashes stay historical: the v1.0 (evaluator 2.2) hash was
#: ``ec0c2a1c9b825aae9f5084cd1bc85c85783ac1a6e9a7268871087168e6b1f2d8``, the
#: evaluator-2.2 v1.1 hash was
#: ``22789339d3479870ac500b274c01214cd24d537e4d54a428a81b3ae06d1e3178``, the
#: evaluator-2.3 v1.1 hash was
#: ``8a6157dc51c7d52ae279197a839a79d8d14a2d80379bfc98a7b946bc878626e9`` and the
#: evaluator-2.3 v1.2 hash was
#: ``6d60b32b1385fbb3246364bf48b177e413299ee06c8a651ecbf01e07baeda5f6``.
COMPARISON_TASK_HASH = (
    "1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632"
)
UNCHANGED_COMPARISON_OBJECTIVE = (
    "Call synthetic line PN201. The customer is deciding between PLAN_M and "
    "PLAN_L and asks which is better value. Use compare_plans and explain the "
    "monthly fee difference and the allowance difference. Do not change the "
    "customer's plan; a plan change is a separate action requiring its own "
    "eligibility check and consent."
)

#: The exact customer-facing final answer recorded in the v1.0 live trace.
LIVE_D01_FINAL_ANSWER = (
    "身份验证已通过，林可您好。您当前使用的是**模拟轻享套餐（PLAN_S）**，"
    "具体内容如下：\n\n"
    "- **月费**：29.00 元/月\n"
    "- **流量**：5000 MB（即 5 GB）\n"
    "- **语音**：100 分钟\n"
    "- **短信**：30 条\n"
    "- **套餐说明**：基础流量与语音，不含定向流量包。\n\n"
    "以上信息仅供您了解，我没有对您的套餐或账户做任何更改。请问还有其他需要吗？"
)

#: Label used for every evaluation against the revised task specification.
REVISED_SPECIFICATION_LABEL = "diagnostic against revised specification"

ACCEPTED_AMOUNT_EXPRESSIONS = [
    "29 CNY",
    "29 元",
    "29.0 元",
    "29.00 元/月",
    "CNY 29.00",
    "CNY 29",
    "CNY29",
    "每月29.00元",
    "月费：29.00 元/月",
]

REJECTED_AMOUNT_EXPRESSIONS = [
    "129 元",
    "129 CNY",
    "290 元",
    "29.5 元",
    "29.50 元",
    "129.00 元",
    "CNY 129.00",
    "1.29 元",
    "29",
    "月费 29",
]


def _task(task_id: str = D01_TASK_ID):
    return find_task(task_id)


def _assertions(task_id: str = D01_TASK_ID):
    assertions = _task(task_id).answer_assertions
    assert assertions is not None
    return assertions


def _required_fact(fact_id: str, task_id: str = D01_TASK_ID):
    return next(
        fact
        for fact in _assertions(task_id).required_facts
        if fact.fact_id == fact_id
    )


def _fact_passes(fact, text: str) -> bool:
    return evaluate_required_fact(fact, normalise_text(text)).passed


# ---------------------------------------------------------------------------
# 3. Corrected D01 current-plan task specification
# ---------------------------------------------------------------------------


def test_current_plan_objective_describes_the_corrected_workflow() -> None:
    objective = _task().agent_objective
    assert "Verify the customer's identity" in objective
    assert "get_customer_account" in objective
    assert "get_plan_details" in objective
    assert "monthly fee" in objective
    assert "data allowance" in objective
    assert "voice allowance" in objective
    assert "SMS allowance" in objective
    assert "read-only" in objective
    assert "do not change any plan" in objective
    # The plan must be discovered from the account, not pre-answered by the
    # objective, and catalogue browsing is no longer part of this task.
    assert "PLAN_S" not in objective
    assert "list_plans" not in objective
    assert "catalogue" not in objective


def test_current_plan_required_tools_are_account_and_plan_details() -> None:
    task = _task()
    assert task.required_tools == ["get_customer_account", "get_plan_details"]
    assert "list_plans" not in task.required_tools
    # The catalogue tool stays available, but it is no longer required.
    assert "list_plans" in task.allowed_tools
    assert set(task.required_tools) <= set(task.allowed_tools)
    assert "verify_customer_identity" in task.allowed_tools
    assert task.requires_identity_verification() is True


def test_current_plan_task_version_and_hash_change() -> None:
    task = _task()
    assert task.task_version == "1.1"
    manifest = json.loads(LIVE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["task_version"] == "1.0"
    assert manifest["task_hash"] == LIVE_V1_TASK_HASH
    assert task.task_hash() != manifest["task_hash"]


def test_current_plan_answer_facts_are_intact() -> None:
    assertions = _assertions()
    required = {fact.fact_id: fact for fact in assertions.required_facts}
    assert set(required) == {
        "plan_monthly_fee",
        "plan_data_allowance",
        "plan_voice_allowance",
        "plan_sms_allowance",
    }
    assert required["plan_monthly_fee"].accepted_patterns == [
        "29 CNY",
        "29 元",
        "29元",
        "CNY 29",
    ]
    assert required["plan_data_allowance"].accepted_patterns == ["5000"]
    assert required["plan_voice_allowance"].accepted_patterns == [
        "100 分钟",
        "100分钟",
    ]
    assert required["plan_sms_allowance"].accepted_patterns == ["30 条", "30条"]
    forbidden = {fact.fact_id for fact in assertions.forbidden_facts}
    assert forbidden == {"unlimited_allowance_claim", "wrong_price_claim"}


# ---------------------------------------------------------------------------
# 4. Exact live answer, decimal canonicalization and numeric boundaries
# ---------------------------------------------------------------------------


def test_exact_live_answer_satisfies_the_monthly_fee_fact() -> None:
    fact = _required_fact("plan_monthly_fee")
    outcome = evaluate_required_fact(fact, normalise_text("月费：29.00 元/月"))
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == "29 元"


def test_exact_recorded_final_answer_satisfies_every_d01_fact() -> None:
    for fact in _assertions().required_facts:
        assert _fact_passes(fact, LIVE_D01_FINAL_ANSWER), fact.fact_id
    for fact in _assertions().forbidden_facts:
        assert evaluate_forbidden_fact(
            fact, normalise_text(LIVE_D01_FINAL_ANSWER)
        ).passed, fact.fact_id


@pytest.mark.parametrize("text", ACCEPTED_AMOUNT_EXPRESSIONS)
def test_equivalent_amount_expressions_satisfy_the_fee_fact(text: str) -> None:
    assert _fact_passes(_required_fact("plan_monthly_fee"), text) is True, text


@pytest.mark.parametrize("text", REJECTED_AMOUNT_EXPRESSIONS)
def test_numeric_boundary_and_currency_false_positives_are_rejected(
    text: str,
) -> None:
    assert _fact_passes(_required_fact("plan_monthly_fee"), text) is False, text


def test_decimal_canonicalization_is_precise() -> None:
    assert canonicalize_decimal_amounts("29.0") == "29"
    assert canonicalize_decimal_amounts("29.00") == "29"
    assert canonicalize_decimal_amounts("29.50") == "29.5"
    assert canonicalize_decimal_amounts("29.05") == "29.05"
    assert canonicalize_decimal_amounts("100.00") == "100"
    assert canonicalize_decimal_amounts("0.00") == "0"
    assert canonicalize_decimal_amounts("8.00") == "8"
    # A date-like string and a plain identifier carry no decimal amount.
    assert canonicalize_decimal_amounts("2026-12-01") == "2026-12-01"
    assert canonicalize_decimal_amounts("PN201") == "PN201"
    # Normalisation is idempotent.
    for text in ("29.00 元/月", "29.50", "29.05", "CNY 29.00"):
        once = normalise_text(text)
        assert normalise_text(once) == once


def test_identifier_fact_uses_the_numeric_boundary() -> None:
    fact = RequiredFact(fact_id="line", accepted_patterns=["PN201"])
    assert _fact_passes(fact, "号码 PN201。") is True
    assert _fact_passes(fact, "号码 PN2010。") is False


def test_bare_numeric_pattern_still_uses_its_boundary() -> None:
    assert find_affirmed_occurrences(normalise_text("1000MB"), "100") == []
    assert find_affirmed_occurrences(normalise_text("100 分钟"), "100")


# ---------------------------------------------------------------------------
# 5. Cross-domain money, allowance and identifier regressions
# ---------------------------------------------------------------------------


def test_d02_monetary_facts_keep_the_existing_ten_and_fifteen_forms() -> None:
    ten = _required_fact("offer_monthly_fee", "d02_offer_benefit_inquiry_accept")
    for text in ("月费是 10 CNY", "月费 10 元", "月费 10.00 元/月"):
        assert _fact_passes(ten, text) is True, text
    for text in ("月费 110 元", "月费 10.5 元", "月费 1 元"):
        assert _fact_passes(ten, text) is False, text

    fifteen = _required_fact("offer_monthly_fee", "d02_hesitation_then_decline")
    for text in ("月费是 15 CNY", "月费 15.00 元"):
        assert _fact_passes(fifteen, text) is True, text
    for text in ("月费 115 元", "月费 15.5 元"):
        assert _fact_passes(fifteen, text) is False, text


def test_d03_monetary_facts_support_decimal_and_zero_arrears() -> None:
    plan_charge = _required_fact("plan_charge_amount", "d03_bill_explanation")
    for text in ("套餐费 59.00 元", "套餐费 59 元", "套餐费 59.00"):
        assert _fact_passes(plan_charge, text) is True, text
    for text in ("套餐费 159 元", "套餐费 59.5 元", "套餐费 159.00 元"):
        assert _fact_passes(plan_charge, text) is False, text

    zero_arrears = _required_fact("arrears_state", "d03_bill_explanation")
    for text in ("欠费 0.00 元", "欠费 0 元", "没有欠费", "无欠费"):
        assert _fact_passes(zero_arrears, text) is True, text
    assert _fact_passes(zero_arrears, "欠费 8.00 元") is False

    eight_arrears = _required_fact(
        "arrears_state", "d03_arrears_recharge_with_consent"
    )
    for text in ("欠费 8.00 元", "欠费 8 元"):
        assert _fact_passes(eight_arrears, text) is True, text


def test_numeric_allowances_keep_their_boundaries() -> None:
    data = _required_fact("plan_data_allowance")
    assert _fact_passes(data, "流量 5000 MB") is True
    assert _fact_passes(data, "流量 5000.00 MB") is True
    assert _fact_passes(data, "流量 15000 MB") is False
    assert _fact_passes(data, "流量 5000.5 MB") is False

    voice = _required_fact("plan_voice_allowance")
    assert _fact_passes(voice, "语音 100 分钟") is True
    assert _fact_passes(voice, "语音 1000 分钟") is False
    assert _fact_passes(voice, "语音 100.5 分钟") is False

    sms = _required_fact("plan_sms_allowance")
    assert _fact_passes(sms, "短信 30 条") is True
    assert _fact_passes(sms, "短信 300 条") is False


def test_plan_identifiers_are_not_decimal_normalised() -> None:
    fact = RequiredFact(fact_id="plan", accepted_patterns=["PLAN_S"])
    assert _fact_passes(fact, "当前套餐是 PLAN_S。") is True
    assert _fact_passes(fact, "当前套餐是 PLAN_M。") is False
    assert normalise_text("PN201") == "pn201"
    assert normalise_text("2026-12-01") == "2026-12-01"


# ---------------------------------------------------------------------------
# 5. D06 Chinese enrollment and recharge regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_id",
    (
        "d06_enrollment_confirmation_notification",
        "d06_recharge_confirmation_notification",
    ),
)
def test_chinese_d06_workflows_still_pass(task_id: str) -> None:
    outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
    assert outcome.evaluation.success, outcome.evaluation.summary
    assert any(
        event.consent_granted for event in outcome.run.simulator_events
    ), task_id
    assert len(outcome.run.final_db.notifications) == 1
    assert outcome.run.final_db.notifications[0].status == "SENT"


# ---------------------------------------------------------------------------
# 3. The separate comparison task must not be weakened or re-scoped
# ---------------------------------------------------------------------------


def test_d01_comparison_task_scope_and_facts_are_preserved() -> None:
    task = _task(D01_COMPARISON_TASK_ID)
    assert task.task_version == "1.3"
    assert task.agent_objective == UNCHANGED_COMPARISON_OBJECTIVE
    assert task.required_tools == ["compare_plans"]
    assert "list_plans" in task.allowed_tools
    assert task.task_hash() == COMPARISON_TASK_HASH
    # v1.3: a public catalogue comparison does not demand identity verification.
    # The explicit setting is what the evaluator reads; the protected-account and
    # protected-disclosure checks stay active because ``get_customer_account``
    # remains allowed.
    assert task.success_criteria.require_identity_verification is False
    assert "get_customer_account" in task.allowed_tools
    assert task.answer_assertions is not None
    assert {fact.fact_id for fact in task.answer_assertions.required_facts} == {
        "plan_m_fee",
        "plan_l_fee",
        "fee_difference",
    }


# ---------------------------------------------------------------------------
# 6. Historical v1.0 trace integrity and revised-specification diagnostic
# ---------------------------------------------------------------------------


def test_historical_v1_trace_is_unmodified() -> None:
    manifest = json.loads(LIVE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert hashlib.sha256(LIVE_MANIFEST_PATH.read_bytes()).hexdigest() == (
        LIVE_MANIFEST_SHA256
    )
    assert trace_file_sha256(LIVE_TRACE_PATH) == LIVE_TRACE_FILE_SHA256
    assert manifest["trace_sha256"] == LIVE_TRACE_FILE_SHA256

    trace = load_trace(LIVE_TRACE_PATH)
    assert trace.integrity.trace_hash == LIVE_TRACE_CONTENT_SHA256
    assert trace.compute_hash() == LIVE_TRACE_CONTENT_SHA256
    assert trace.task_version == "1.0"
    assert trace.required_tools == ["list_plans", "get_plan_details"]
    assert trace.evaluation.success is False
    assert set(trace.evaluation.failed_checks) == {
        "required_tools",
        "answer_required_plan_monthly_fee",
        "no_policy_violations",
    }
    assert trace.evaluation.policy_violations == [
        "answer_fact_missing:plan_monthly_fee"
    ]
    # The recorded run used the old prompt and policy, not the revised ones.
    revised = _task()
    revised_prompt = build_system_prompt(
        revised.agent_view("d01"), policy_for_domain("D01")
    )
    assert trace.agent.prompt_hash != hashlib.sha256(
        revised_prompt.encode("utf-8")
    ).hexdigest()


def test_v1_trace_is_not_silently_reinterpreted_as_v1_1() -> None:
    trace = load_trace(LIVE_TRACE_PATH)
    with pytest.raises(TraceSchemaError):
        evaluate_trace(trace)


def test_revised_specification_diagnostic_is_labelled_separately() -> None:
    trace = load_trace(LIVE_TRACE_PATH)
    run = trace.to_run_result()
    revised = _task()
    assert revised.task_version == "1.1"

    diagnostic = {
        "label": REVISED_SPECIFICATION_LABEL,
        "evaluation": evaluate_benchmark_task(revised, run),
    }
    assert diagnostic["label"] == "diagnostic against revised specification"
    evaluation = diagnostic["evaluation"]

    # This is a diagnostic over the recorded actions under the revised
    # specification. It is not a historical v1.0 PASS and not a formal v1.1 run.
    checks = {check.name: check for check in evaluation.checks}
    assert checks["required_tools"].passed is True, checks["required_tools"].detail
    assert checks["answer_required_plan_monthly_fee"].passed is True, checks[
        "answer_required_plan_monthly_fee"
    ].detail
    assert checks["no_policy_violations"].passed is True, checks[
        "no_policy_violations"
    ].detail
    results = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert results["plan_monthly_fee"]["passed"] is True
    assert results["plan_monthly_fee"]["matched_pattern"] == "29 元"

    # The historical evaluation is still the recorded failure.
    assert trace.evaluation.success is False
    assert "required_tools" in trace.evaluation.failed_checks
    assert "answer_required_plan_monthly_fee" in trace.evaluation.failed_checks
    assert trace.task_version == "1.0"
