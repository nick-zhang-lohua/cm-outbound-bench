"""Shared identity-credential rule: ask first, never fabricate a token.

The D01 v1.0 live run fabricated ``TEST_IDENTITY_PN201`` from the target line
identifier before the customer supplied anything. These tests pin the shared
prompt rule, the provider-visible tool description and the tool's own rejection
behaviour, without any provider or network access.
"""

from __future__ import annotations

import pytest

from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task, policy_for_domain
from cm_outbound_bench.benchmark.execution import execute_task, load_initial_state
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.domain.tools import CmOutboundTools, VerifyCustomerIdentityArgs
from cm_outbound_bench.llm_agent import build_system_prompt

D01_TASK_ID = "d01_plan_content_explanation"
D06_TASK_ID = "d06_enrollment_confirmation_notification"

#: Provider/product names that must never appear in the shared system prompt.
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

#: Tokens an agent could invent from the task context instead of asking the
#: customer. The exact v1.0 regression token is the first entry.
FABRICATED_TOKENS = (
    "TEST_IDENTITY_PN201",
    "PN201",
    "C201",
    "SIM_LINE_201",
    "TEST_IDENTITY_",
    "TEST_IDENTITY_C201_EXTRA",
    "test_identity_c201",
)


def _view(task_id: str):
    task = find_task(task_id)
    return task.agent_view(task.domain_id.lower())


def _d01_toolkit():
    return build_toolkit(load_initial_state(find_task(D01_TASK_ID)).instantiate())


def _verify(toolkit, token: str):
    return toolkit.execute(
        "verify_customer_identity",
        {"phone_number_id": "PN201", "identity_token": token},
    )


# ---------------------------------------------------------------------------
# 1-2. Shared system prompt
# ---------------------------------------------------------------------------


def test_system_prompt_requires_ask_first_and_forbids_fabrication() -> None:
    prompt = build_system_prompt(_view(D01_TASK_ID), "Synthetic policy.")
    assert (
        "Never construct, guess, infer, transform or fabricate an identity token"
        in prompt
    )
    assert (
        "An identity token may only come from the customer in the current "
        "conversation" in prompt
    )
    assert (
        "never derive one from a phone number, customer ID, account ID or any "
        "other identifier" in prompt
    )
    assert (
        "Do not attempt identity verification before the customer has supplied "
        "the token" in prompt
    )
    assert "greet the customer, ask for the token and wait" in prompt
    assert "do not retry with a guessed or fabricated token" in prompt


def test_system_prompt_rule_applies_to_every_domain() -> None:
    for task_id in (D01_TASK_ID, D06_TASK_ID):
        prompt = build_system_prompt(_view(task_id), "Synthetic policy.")
        assert "Never construct, guess, infer, transform or fabricate" in prompt
        assert "may only come from the customer in the current conversation" in prompt


def test_system_prompt_shows_no_credential_format_example() -> None:
    """A format example is exactly what pattern completion would copy."""
    prompt = build_system_prompt(_view(D01_TASK_ID), "Synthetic policy.")
    assert "TEST_IDENTITY" not in prompt
    assert "TEST_IDENTITY_*" not in prompt


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
def test_system_prompt_is_provider_neutral(provider_name: str) -> None:
    prompt = build_system_prompt(_view(D01_TASK_ID), "Synthetic policy.")
    assert provider_name not in prompt.lower()


def test_shared_policy_states_the_same_rule() -> None:
    policy = policy_for_domain("D01")
    assert "Never construct, guess, infer, transform or fabricate" in policy
    assert "never derive one from a phone number, customer ID, account ID" in policy
    assert "Request the token" in policy or "request the token" in policy
    assert "do not retry with a guessed or fabricated token" in policy
    assert "TEST_IDENTITY" not in policy


# ---------------------------------------------------------------------------
# 3. Provider-visible identity-token parameter description
# ---------------------------------------------------------------------------


def test_identity_token_parameter_description_requires_a_customer_value() -> None:
    description = VerifyCustomerIdentityArgs.model_fields[
        "identity_token"
    ].description
    assert "supplied by the customer in the current conversation" in description
    assert "Never construct, guess, infer or derive" in description
    assert "phone number, customer ID, account ID" in description
    assert "TEST_IDENTITY" not in description


def test_identity_tool_description_does_not_teach_credential_format(database) -> None:
    definitions = {item.name: item for item in CmOutboundTools(database).list_tools()}
    description = definitions["verify_customer_identity"].description
    assert "the identity token the customer supplied" in description
    assert "TEST_IDENTITY_*" not in description
    parameters = definitions["verify_customer_identity"].parameters
    token_description = parameters["properties"]["identity_token"]["description"]
    assert "supplied by the customer in the current conversation" in token_description


# ---------------------------------------------------------------------------
# 4. The identity tool still rejects fabricated and derived tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", FABRICATED_TOKENS)
def test_fabricated_token_is_rejected(token: str) -> None:
    toolkit = _d01_toolkit()
    result = _verify(toolkit, token)
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "identity_verification_failed"
    # A failed attempt must not unlock the protected account tool.
    account = toolkit.execute("get_customer_account", {"phone_number_id": "PN201"})
    assert not account.ok
    assert account.error is not None
    assert account.error.code == "identity_verification_required"


def test_failed_verification_does_not_retry_with_a_fabricated_token() -> None:
    toolkit = _d01_toolkit()
    assert not _verify(toolkit, "TEST_IDENTITY_PN201").ok
    assert not _verify(toolkit, "TEST_IDENTITY_PN201").ok
    account = toolkit.execute("get_customer_account", {"phone_number_id": "PN201"})
    assert account.error is not None
    assert account.error.code == "identity_verification_required"


# ---------------------------------------------------------------------------
# 5. Existing legitimate identity flows remain valid
# ---------------------------------------------------------------------------


def test_legitimate_customer_supplied_token_is_still_accepted() -> None:
    toolkit = _d01_toolkit()
    verified = _verify(toolkit, "TEST_IDENTITY_C201")
    assert verified.ok
    assert verified.data == {"verified": True, "phone_number_id": "PN201"}
    account = toolkit.execute("get_customer_account", {"phone_number_id": "PN201"})
    assert account.ok
    assert account.data["current_plan"]["plan_id"] == "PLAN_S"


@pytest.mark.parametrize("task_id", (D01_TASK_ID, D06_TASK_ID))
def test_existing_legitimate_identity_flows_still_pass(task_id: str) -> None:
    outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
    assert outcome.evaluation.success, outcome.evaluation.summary
    verified = [
        entry
        for entry in outcome.run.tool_trace
        if entry.tool_name == "verify_customer_identity" and entry.result.ok
    ]
    assert verified, task_id
    assert verified[0].arguments["identity_token"] == task_id_token(task_id)
    assert verified[0].step > 1, "the agent must ask before verifying"


def task_id_token(task_id: str) -> str:
    return {
        D01_TASK_ID: "TEST_IDENTITY_C201",
        D06_TASK_ID: "TEST_IDENTITY_C601",
    }[task_id]
