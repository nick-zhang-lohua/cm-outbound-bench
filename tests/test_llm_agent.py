import json

import pytest
from pydantic import SecretStr

from cm_outbound_bench.agent import (
    AgentContext,
    CallToolAction,
    ConversationMessage,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.domain.tools import CmOutboundTools, ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.evaluator import evaluate_task
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.providers import (
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ProviderToolCall,
    ResolvedProviderConfig,
)
from cm_outbound_bench.providers.http import HttpResponse
from cm_outbound_bench.providers.openai_compatible import OpenAICompatibleClient
from cm_outbound_bench.runner import MarketingRunner
from cm_outbound_bench.tasks import AgentTaskView, find_task


class ScriptedClient:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[ProviderMessage], list]] = []

    def complete(self, *, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        return self.responses.pop(0)


class StaticTransport:
    """Returns one canned Chat Completions body without network access."""

    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls = 0

    def post(self, url, headers, payload, timeout):
        self.calls += 1
        return HttpResponse(200, json.dumps(self.body))


def _context(
    database,
    policy: str,
    *,
    messages=None,
    traces=None,
    step: int = 1,
) -> AgentContext:
    return AgentContext(
        task=AgentTaskView(
            domain="marketing",
            objective="Contact PN001 about OFFER_DATA_001.",
            phone_number_id="PN001",
            offer_id="OFFER_DATA_001",
        ),
        policy=policy,
        tools=CmOutboundTools(database).list_tools(),
        messages=messages or [],
        tool_results=traces or [],
        step=step,
    )


def test_llm_agent_maps_text_tool_and_finish(database, policy):
    context = _context(database, policy)
    text_client = ScriptedClient([ProviderResponse(assistant_message="您好。")])
    assert isinstance(LLMBackedAgent(text_client).act(context), SayAction)

    tool_client = ScriptedClient(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="call-42",
                        tool_name="get_offer_details",
                        arguments={"offer_id": "OFFER_DATA_001"},
                    )
                ]
            )
        ]
    )
    tool_action = LLMBackedAgent(tool_client).act(context)
    assert isinstance(tool_action, CallToolAction)
    assert tool_action.call_id == "call-42"
    assert tool_action.tool_name == "get_offer_details"

    finish_client = ScriptedClient([ProviderResponse(finish_requested=True)])
    assert isinstance(LLMBackedAgent(finish_client).act(context), FinishAction)


def test_llm_agent_preserves_call_id_from_text_and_tool_call(database, policy):
    transport = StaticTransport(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Let me check your offer details.",
                        "tool_calls": [
                            {
                                "id": "mixed-call-9",
                                "type": "function",
                                "function": {
                                    "name": "get_offer_details",
                                    "arguments": json.dumps(
                                        {"offer_id": "OFFER_DATA_001"}
                                    ),
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
        ResolvedProviderConfig(
            api_protocol="openai_compatible",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            timeout=30,
            max_retries=0,
            tool_mode="function_calling",
            api_key=SecretStr("sk-test-secret-value"),
            headers={},
            extra_params={},
        ),
        transport,
        sleep=lambda _: None,
    )

    action = LLMBackedAgent(client).act(_context(database, policy))

    assert isinstance(action, CallToolAction)
    assert action.call_id == "mixed-call-9"
    assert action.tool_name == "get_offer_details"
    assert action.arguments == {"offer_id": "OFFER_DATA_001"}
    assert transport.calls == 1


def test_llm_agent_validates_tool_calls_locally(database, policy):
    context = _context(database, policy)
    unknown = LLMBackedAgent(
        ScriptedClient(
            [
                ProviderResponse(
                    tool_calls=[
                        ProviderToolCall(
                            call_id="call-1",
                            tool_name="not_a_tool",
                            arguments={},
                        )
                    ]
                )
            ]
        )
    )
    with pytest.raises(ProviderError) as error:
        unknown.act(context)
    assert error.value.code == "unknown_tool"

    invalid = LLMBackedAgent(
        ScriptedClient(
            [
                ProviderResponse(
                    tool_calls=[
                        ProviderToolCall(
                            call_id="call-2",
                            tool_name="get_offer_details",
                            arguments={},
                        )
                    ]
                )
            ]
        )
    )
    with pytest.raises(ProviderError) as error:
        invalid.act(context)
    assert error.value.code == "invalid_tool_arguments"


def test_llm_agent_reconstructs_tool_history(database, policy):
    trace = ToolTraceEntry(
        step=2,
        tool_name="get_offer_details",
        arguments={"offer_id": "OFFER_DATA_001"},
        result=ToolResult.success({"offer_id": "OFFER_DATA_001"}),
        call_id="call-history",
    )
    context = _context(
        database,
        policy,
        messages=[
            ConversationMessage(step=1, role="agent", content="您好。"),
            ConversationMessage(step=1, role="user", content="请介绍一下。"),
        ],
        traces=[trace],
        step=3,
    )
    client = ScriptedClient([ProviderResponse(finish_requested=True)])
    LLMBackedAgent(client).act(context)

    messages = client.calls[0][0]
    assert messages[0].role == "system"
    assert "marketing policy" in messages[0].content
    assert messages[1].role == "assistant"
    assert messages[2].role == "user"
    assert messages[3].role == "assistant"
    assert messages[3].tool_calls[0].call_id == "call-history"
    assert messages[3].tool_calls[0].tool_name == "get_offer_details"
    assert messages[4].role == "tool"
    assert messages[4].tool_call_id == "call-history"
    assert messages[4].name == "get_offer_details"
    assert '"ok": true' in messages[4].content


def test_llm_agent_runs_through_unchanged_runner(database, policy, marketing_tasks):
    client = ScriptedClient(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="offer-call",
                        tool_name="get_offer_details",
                        arguments={"offer_id": "OFFER_DATA_001"},
                    )
                ]
            ),
            ProviderResponse(
                assistant_message=(
                    "您好，我是中国移动客服代表，想向您介绍这个活动，请问方便吗？"
                )
            ),
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="dnc-call",
                        tool_name="register_do_not_call",
                        arguments={"phone_number_id": "PN001"},
                    )
                ]
            ),
            ProviderResponse(assistant_message="好的，已登记免打扰，感谢接听。"),
            ProviderResponse(finish_requested=True),
        ]
    )
    task = find_task("marketing_direct_refusal", marketing_tasks)
    runner = MarketingRunner(
        initial_db=database,
        policy=policy,
        agent_factory=lambda: LLMBackedAgent(client),
    )

    run = runner.run(task)
    evaluation = evaluate_task(task, run)

    assert evaluation.success, evaluation.summary
    assert len(run.final_db.do_not_call_records) == 1
    assert [item.call_id for item in run.tool_trace] == [
        "offer-call",
        "dnc-call",
    ]
    assert len(client.calls) == 5
    history = client.calls[2][0]
    assert any(
        message.role == "tool" and message.tool_call_id == "offer-call"
        for message in history
    )


def test_invalid_normalized_response_is_rejected(database, policy):
    class BadClient:
        def complete(self, *, messages, tools):
            return {"assistant_message": "not a model"}

    with pytest.raises(ProviderError) as error:
        LLMBackedAgent(BadClient()).act(_context(database, policy))
    assert error.value.code == "invalid_provider_response"
