"""Focused tests: the LLM agent reports business outcomes before finishing.

All providers are mocked. No network access is performed.
"""

from __future__ import annotations

import json

from pydantic import SecretStr

from cm_outbound_bench.agent import (
    AgentContext,
    ConversationMessage,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.llm_agent import LLMBackedAgent, build_system_prompt
from cm_outbound_bench.providers import (
    ProviderMessage,
    ProviderResponse,
    ProviderToolCall,
    ResolvedProviderConfig,
)
from cm_outbound_bench.providers.base import FINISH_TOOL
from cm_outbound_bench.providers.http import HttpResponse
from cm_outbound_bench.providers.openai_compatible import OpenAICompatibleClient
from cm_outbound_bench.runner import MarketingRunner, StopReason
from cm_outbound_bench.tasks import find_task as find_marketing_task

D06_TASK_ID = "d06_enrollment_confirmation_notification"

ENROLLMENT_OK = {
    "enrollment_id": "ENR-0001",
    "offer_id": "OFFER_D02_001",
    "phone_number_id": "PN601",
    "status": "enrolled",
}
NOTIFICATION_OK = {
    "channel": "SMS",
    "kind": "CONFIRMATION",
    "notification_id": "NTF-0001",
    "phone_number_id": "PN601",
    "status": "SENT",
}


class _FinishClient:
    """A provider that immediately asks to finish, like the recorded live run."""

    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[ProviderMessage] = []

    def complete(self, *, messages, tools):
        self.calls += 1
        self.messages = list(messages)
        return ProviderResponse(finish_requested=True)


class _ScriptedClient:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, *, messages, tools):
        self.calls += 1
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        return self.responses.pop(0)


class _StaticTransport:
    """Returns one canned Chat Completions body without network access."""

    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls = 0

    def post(self, url, headers, payload, timeout):
        self.calls += 1
        return HttpResponse(200, json.dumps(self.body))


class _CaptureTransport:
    """Captures the provider payload and answers with a plain greeting."""

    def __init__(self) -> None:
        self.payload: dict = {}

    def post(self, url, headers, payload, timeout):
        self.payload = payload
        return HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Hello.",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            ),
        )


def _context(
    database: CmOutboundDB,
    traces: list[ToolTraceEntry],
    *,
    messages: list[ConversationMessage] | None = None,
    step: int = 10,
) -> AgentContext:
    task = find_task(D06_TASK_ID)
    return AgentContext(
        task=task.agent_view(),
        policy="Synthetic test policy.",
        tools=build_toolkit(database).list_tools(),
        messages=messages
        if messages is not None
        else [
            ConversationMessage(step=1, role="agent", content="Hello."),
            ConversationMessage(step=6, role="user", content="我同意办理。"),
        ],
        tool_results=traces,
        step=step,
    )


def _success(step: int, tool_name: str, data: dict) -> ToolTraceEntry:
    return ToolTraceEntry(
        step=step,
        tool_name=tool_name,
        arguments={},
        result=ToolResult.success(data),
    )


def _failure(step: int, tool_name: str, code: str, message: str) -> ToolTraceEntry:
    return ToolTraceEntry(
        step=step,
        tool_name=tool_name,
        arguments={},
        result=ToolResult.failure(code, message),
    )


def _provider_config(tool_mode: str = "function_calling") -> ResolvedProviderConfig:
    return ResolvedProviderConfig(
        api_protocol="openai_compatible",
        base_url="https://example.test/v1",
        model="mock-model",
        timeout=5,
        max_retries=0,
        tool_mode=tool_mode,
        api_key=SecretStr("sk-test-secret-value"),
        headers={},
        extra_params={},
    )


def test_successful_enrollment_and_notification_do_not_finish_silently(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [
            _success(7, "enroll_offer", ENROLLMENT_OK),
            _success(8, "send_notification", NOTIFICATION_OK),
            _success(9, "get_notification_status", NOTIFICATION_OK),
        ],
    )
    client = _FinishClient()

    action = LLMBackedAgent(client).act(context)

    assert isinstance(action, SayAction)
    assert "enroll" in action.message.lower()
    assert "ENR-0001" in action.message
    assert "NTF-0001" in action.message
    assert "SENT" in action.message


def test_final_report_is_grounded_in_recorded_tool_results(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [
            _success(7, "enroll_offer", {"enrollment_id": "ENR-4242"}),
            _success(8, "send_notification", {"notification_id": "NTF-7777"}),
        ],
    )
    client = _FinishClient()

    action = LLMBackedAgent(client).act(context)

    assert isinstance(action, SayAction)
    assert "ENR-4242" in action.message
    assert "NTF-7777" in action.message
    # One provider response still produces exactly one action.
    assert client.calls == 1


def test_provider_history_contains_the_successful_tool_results(
    database: CmOutboundDB,
) -> None:
    """The model really receives the recorded results, not a summary guess."""
    context = _context(
        database,
        [
            _success(7, "enroll_offer", ENROLLMENT_OK),
            _success(8, "send_notification", NOTIFICATION_OK),
        ],
    )
    client = _FinishClient()

    LLMBackedAgent(client).act(context)

    tool_messages = [message for message in client.messages if message.role == "tool"]
    assert [message.name for message in tool_messages] == [
        "enroll_offer",
        "send_notification",
    ]
    assert all('"ok": true' in message.content for message in tool_messages)
    assert "ENR-0001" in tool_messages[0].content
    assert "NTF-0001" in tool_messages[1].content
    assert "actual outcome before finishing" in client.messages[0].content


def test_report_does_not_quote_sensitive_result_fields(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [
            _success(7, "enroll_offer", ENROLLMENT_OK),
            _success(
                8,
                "get_customer_account",
                {
                    "account_id": "BA-PN601",
                    "balance_cents": 2500,
                    "arrears_cents": 0,
                    "status": "active",
                },
            ),
        ],
    )

    action = LLMBackedAgent(_FinishClient()).act(context)

    assert isinstance(action, SayAction)
    assert "balance_cents" not in action.message
    assert "2500" not in action.message
    assert "arrears_cents" not in action.message


def test_failed_enrollment_is_never_reported_as_success(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [
            _failure(
                7,
                "enroll_offer",
                "consent_required",
                "Explicit customer consent is required.",
            )
        ],
    )

    action = LLMBackedAgent(_FinishClient()).act(context)

    assert isinstance(action, SayAction)
    assert "failed" in action.message.lower()
    assert "consent_required" in action.message
    assert "completed successfully" not in action.message.lower()


def test_enrollment_success_with_failed_notification_is_reported_accurately(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [
            _success(7, "enroll_offer", ENROLLMENT_OK),
            _failure(
                8,
                "send_notification",
                "notification_delivery_failed",
                "The simulated notification could not be sent.",
            ),
        ],
    )

    action = LLMBackedAgent(_FinishClient()).act(context)

    assert isinstance(action, SayAction)
    message = action.message
    assert "enroll offer completed successfully" in message
    assert "send notification failed" in message
    assert "notification_delivery_failed" in message


def test_finish_without_tool_results_does_not_claim_completion(
    database: CmOutboundDB,
) -> None:
    action = LLMBackedAgent(_FinishClient()).act(_context(database, []))

    assert isinstance(action, FinishAction)


def test_ambiguous_result_data_does_not_fabricate_completion(
    database: CmOutboundDB,
) -> None:
    context = _context(database, [_success(7, "enroll_offer", {})])

    action = LLMBackedAgent(_FinishClient()).act(context)

    assert isinstance(action, SayAction)
    assert "reported success without details" in action.message
    assert "completed successfully" not in action.message.lower()


def test_read_only_tool_finish_behavior_is_unchanged(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [
            _success(
                7,
                "get_offer_details",
                {"offer_id": "OFFER_D02_001", "active": True},
            )
        ],
    )

    action = LLMBackedAgent(_FinishClient()).act(context)

    assert isinstance(action, FinishAction)


def test_finish_is_allowed_once_the_customer_has_been_told(
    database: CmOutboundDB,
) -> None:
    context = _context(
        database,
        [_success(7, "enroll_offer", ENROLLMENT_OK)],
        messages=[
            ConversationMessage(step=1, role="agent", content="Hello."),
            ConversationMessage(step=6, role="user", content="我同意办理。"),
            ConversationMessage(
                step=8,
                role="agent",
                content="已为您成功办理，确认通知已发送。",
            ),
        ],
        step=9,
    )

    action = LLMBackedAgent(_FinishClient()).act(context)

    assert isinstance(action, FinishAction)


def test_openai_compatible_finish_tool_also_reports_the_outcome(
    database: CmOutboundDB,
) -> None:
    """The gate lives in the agent, so every provider normalization benefits."""
    transport = _StaticTransport(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "finish-1",
                                "type": "function",
                                "function": {
                                    "name": "finish_conversation",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    client = OpenAICompatibleClient(
        _provider_config(),
        transport,
        sleep=lambda _: None,
    )
    context = _context(
        database,
        [
            _success(7, "enroll_offer", ENROLLMENT_OK),
            _success(8, "send_notification", NOTIFICATION_OK),
        ],
    )

    action = LLMBackedAgent(client).act(context)

    assert isinstance(action, SayAction)
    assert "ENR-0001" in action.message
    assert transport.calls == 1


def test_json_action_finish_also_reports_the_outcome(
    database: CmOutboundDB,
) -> None:
    transport = _StaticTransport(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"action": "FINISH"}),
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )
    client = OpenAICompatibleClient(
        _provider_config(tool_mode="json"),
        transport,
        sleep=lambda _: None,
    )
    context = _context(
        database,
        [_success(7, "enroll_offer", ENROLLMENT_OK)],
    )

    action = LLMBackedAgent(client).act(context)

    assert isinstance(action, SayAction)
    assert "ENR-0001" in action.message


def test_tool_metadata_does_not_leak_into_the_provider_payload(
    database: CmOutboundDB,
) -> None:
    transport = _CaptureTransport()
    client = OpenAICompatibleClient(
        _provider_config(),
        transport,
        sleep=lambda _: None,
    )

    LLMBackedAgent(client).act(_context(database, []))

    serialized = json.dumps(transport.payload)
    assert "mutating" not in serialized
    assert '"name": "enroll_offer"' in serialized


def test_system_prompt_requires_an_outcome_report_before_finishing() -> None:
    view = find_task(D06_TASK_ID).agent_view()
    prompt = build_system_prompt(view, "Synthetic test policy.")

    assert "actual outcome before finishing" in prompt
    assert "failed or missing result as success" in prompt
    assert "customer has been told the outcome" in prompt
    # The instruction is generic: no domain tool names or provider names.
    assert "enroll_offer" not in prompt
    assert "send_notification" not in prompt
    assert "deepseek" not in prompt.lower()


def test_finish_tool_description_requires_the_customer_outcome() -> None:
    assert "customer has been told the actual outcome" in FINISH_TOOL.description


def test_state_changing_tools_are_marked_as_mutating(
    database: CmOutboundDB,
) -> None:
    definitions = {tool.name: tool for tool in build_toolkit(database).list_tools()}

    for name in (
        "enroll_offer",
        "send_confirmation_sms",
        "register_do_not_call",
        "send_notification",
    ):
        assert definitions[name].mutating is True, name
    for name in (
        "verify_customer_identity",
        "get_offer_details",
        "check_offer_eligibility",
        "get_notification_status",
    ):
        assert definitions[name].mutating is False, name


def test_runner_delivers_the_generated_report_before_finishing(
    database: CmOutboundDB,
    policy: str,
    marketing_tasks,
) -> None:
    client = _ScriptedClient(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="dnc-1",
                        tool_name="register_do_not_call",
                        arguments={"phone_number_id": "PN001"},
                    )
                ]
            ),
            ProviderResponse(finish_requested=True),
            ProviderResponse(finish_requested=True),
        ]
    )
    task = find_marketing_task("marketing_direct_refusal", marketing_tasks)
    runner = MarketingRunner(
        initial_db=database,
        policy=policy,
        agent_factory=lambda: LLMBackedAgent(client),
    )

    run = runner.run(task)

    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert len(run.final_db.do_not_call_records) == 1
    agent_messages = [
        message.content for message in run.conversation if message.role == "agent"
    ]
    # A do-not-call registration is reported to the customer in Chinese without
    # internal record or line identifiers.
    assert any("已为您登记免打扰" in message for message in agent_messages)
    assert all("DNC-" not in message for message in agent_messages)
    assert client.calls == 3
