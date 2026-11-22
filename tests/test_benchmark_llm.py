from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.domain.tools import ToolDefinition
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.providers.base import (
    ProviderMessage,
    ProviderResponse,
)


class _CapturingClient:
    def __init__(self) -> None:
        self.messages: list[ProviderMessage] = []
        self.tools: list[ToolDefinition] = []
        self.calls = 0

    def complete(self, *, messages, tools):
        self.messages = messages
        self.tools = tools
        self.calls += 1
        from cm_outbound_bench.providers.base import ProviderResponse

        return ProviderResponse(finish_requested=True)


def test_llm_agent_receives_benchmark_task_view_and_domain_tools() -> None:
    client = _CapturingClient()
    task = find_task("d03_bill_explanation")

    outcome = execute_task(
        task, agent_factory=lambda: LLMBackedAgent(client)
    )

    assert client.calls >= 1
    tool_names = {definition.name for definition in client.tools}
    assert tool_names == set(task.allowed_tools)
    assert "get_billing_summary" in tool_names
    assert "enroll_offer" not in tool_names

    system_prompt = client.messages[0].content
    assert task.customer_id not in system_prompt
    assert task.simulator.verification_token not in system_prompt
    assert "business policy" in system_prompt
    assert "marketing policy" not in system_prompt
    assert "PN301" in system_prompt
    for reference in task.evidence_refs:
        assert reference.transcript_id not in system_prompt

    # The run therefore failed the task, as expected for an agent that only
    # emits a finish request: the evaluator must not be fooled.
    assert not outcome.evaluation.success
    failed = {check.name for check in outcome.evaluation.checks if not check.passed}
    assert "required_tools" in failed
    assert "d03_billing_information_retrieved" in failed


def test_llm_agent_prompt_mentions_offer_only_when_present() -> None:
    with_offer = _CapturingClient()
    execute_task(
        find_task("d02_offer_benefit_inquiry_accept"),
        agent_factory=lambda: LLMBackedAgent(with_offer),
    )
    assert "Target offer: OFFER_D02_001" in with_offer.messages[0].content
    assert "marketing policy" not in with_offer.messages[0].content

    without_offer = _CapturingClient()
    execute_task(
        find_task("d05_ownership_transfer_conditions"),
        agent_factory=lambda: LLMBackedAgent(without_offer),
    )
    assert "Target offer:" not in without_offer.messages[0].content


class _ToolCallingClient:
    """Emits one normalized tool call, then finishes."""

    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = 0

    def complete(self, *, messages, tools):
        self.calls += 1
        if self.calls == 1:
            from cm_outbound_bench.providers.base import ProviderToolCall

            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="call-1",
                        tool_name=self.tool_name,
                        arguments=self.arguments,
                    )
                ]
            )
        return ProviderResponse(finish_requested=True)


def test_llm_agent_can_execute_a_domain_tool_through_the_runner() -> None:
    client = _ToolCallingClient("list_plans", {})
    outcome = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=lambda: LLMBackedAgent(client),
    )

    assert outcome.run.tool_trace
    assert outcome.run.tool_trace[0].tool_name == "list_plans"
    assert outcome.run.tool_trace[0].result.ok
    assert outcome.run.tool_trace[0].call_id == "call-1"


def test_llm_agent_disallowed_tool_is_a_recorded_failure_not_a_crash() -> None:
    client = _ToolCallingClient("enroll_offer", {
        "phone_number_id": "PN201",
        "offer_id": "OFFER_D02_001",
    })
    outcome = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=lambda: LLMBackedAgent(client),
    )

    assert not outcome.evaluation.success
    assert outcome.evaluation.completion_reason == "agent_protocol_error:unknown_tool"
    assert outcome.evaluation.checks[0].name == "agent_protocol"
    assert outcome.run.tool_trace == []
    assert any(
        violation.startswith("agent_protocol_error:")
        for violation in outcome.evaluation.policy_violations
    )


class _DisallowedToolAgent:
    """Bypasses provider validation and calls a tool outside the allow-list."""

    def __init__(self) -> None:
        self.calls = 0

    def act(self, context):
        from cm_outbound_bench.agent import CallToolAction, FinishAction

        self.calls += 1
        if self.calls == 1:
            return CallToolAction(
                tool_name="enroll_offer",
                arguments={
                    "phone_number_id": "PN201",
                    "offer_id": "OFFER_D02_001",
                },
            )
        return FinishAction(reason="done")


def test_runner_blocks_tools_outside_the_allow_list() -> None:
    outcome = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=_DisallowedToolAgent,
    )

    attempts = [
        entry for entry in outcome.run.tool_trace if entry.tool_name == "enroll_offer"
    ]
    assert attempts
    assert attempts[0].result.error is not None
    assert attempts[0].result.error.code == "tool_not_allowed"
    assert not outcome.evaluation.success
    assert "no_disallowed_tool_calls" in {
        check.name for check in outcome.evaluation.checks if not check.passed
    }


def test_manifest_for_llm_run_records_agent_identity(tmp_path: Path) -> None:
    from cm_outbound_bench.benchmark.metrics import build_manifest

    outcome = execute_task(
        find_task("d05_ownership_transfer_conditions"),
        agent_factory=lambda: LLMBackedAgent(_CapturingClient()),
        agent_name="llm",
        agent_metadata={"provider": "relay", "protocol": "openai_compatible"},
    )
    manifest = build_manifest(
        outcome,
        agent_type="llm",
        provider_protocol="openai_compatible",
        provider_name="relay",
        model_name="relay-model",
        sampling={"temperature": 0.0},
        random_seed=7,
    )

    payload = json.loads(manifest.model_dump_json())
    assert payload["agent_type"] == "llm"
    assert payload["provider_name"] == "relay"
    assert payload["sampling"] == {"temperature": 0.0}
    assert payload["random_seed"] == 7
    assert "api_key" not in json.dumps(payload).lower()
