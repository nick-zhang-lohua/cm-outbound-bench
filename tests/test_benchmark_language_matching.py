"""Customer-language matching: the shared prompt rule and its deterministic backstop.

A live model's compliance cannot be asserted offline, so these tests pin the two
things that *can* be asserted deterministically:

* the shared system prompt states the language rule for every domain, and
* the agent's closing path mirrors the customer's language and honours an
  explicit language preference, without touching tool identifiers.
"""

from __future__ import annotations

import hashlib

import pytest

from cm_outbound_bench.agent import (
    AgentContext,
    ConversationMessage,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.benchmark.domains import find_task, policy_for_domain
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.llm_agent import (
    PROMPT_VERSION,
    LLMBackedAgent,
    build_system_prompt,
    customer_declined,
    reply_language,
    requested_language,
)
from cm_outbound_bench.providers.base import ProviderResponse

#: One task per domain family, so the language rule is shown to be shared rather
#: than hard-coded for the D02 decline scenario.
DOMAIN_TASKS = (
    "d01_plan_content_explanation",
    "d02_hesitation_then_decline",
    "d02_offer_benefit_inquiry_accept",
    "d06_enrollment_confirmation_notification",
)

CUSTOMER_LANGUAGE_RULE = (
    "Respond in the language the customer primarily uses. When the customer "
    "communicates in Simplified Chinese, respond in Simplified Chinese; do not "
    "switch to English unexpectedly. If the customer states an explicit "
    "language preference, follow it. Internal tool names, schemas and API "
    "identifiers never change language."
)


class _FinishClient:
    """A provider that asks to finish, like the recorded live run."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, messages, tools):
        self.calls += 1
        return ProviderResponse(finish_requested=True)


def _context(task_id: str, messages: list[ConversationMessage]) -> AgentContext:
    task = find_task(task_id)
    return AgentContext(
        task=task.agent_view(),
        policy="Synthetic test policy.",
        tools=build_toolkit(CmOutboundDB()).list_tools(),
        messages=messages,
        tool_results=[],
        step=9,
    )


def _agent_action(task_id: str, messages: list[ConversationMessage]):
    return LLMBackedAgent(_FinishClient()).act(_context(task_id, messages))


# ---------------------------------------------------------------------------
# Shared prompt rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", DOMAIN_TASKS)
def test_shared_prompt_requires_customer_language_matching(task_id: str) -> None:
    prompt = build_system_prompt(find_task(task_id).agent_view(), "Policy.")
    assert CUSTOMER_LANGUAGE_RULE in prompt


def test_language_rule_is_shared_and_not_scenario_specific() -> None:
    prompts = {
        task_id: build_system_prompt(
            find_task(task_id).agent_view(), "Synthetic test policy."
        )
        for task_id in DOMAIN_TASKS
    }
    rule_counts = {
        task_id: prompt.count("Respond in the language the customer primarily uses")
        for task_id, prompt in prompts.items()
    }
    assert set(rule_counts.values()) == {1}, rule_counts
    # The rule never names a domain tool, a language-specific scenario or a
    # concrete customer line, so it cannot silently special-case D02.
    for prompt in prompts.values():
        rule = prompt[prompt.index("Respond in the language") :]
        rule = rule[: rule.index("\n")]
        for identifier in (
            "enroll_offer",
            "check_offer_eligibility",
            "PN102",
            "OFFER_D02",
            "d02_hesitation_then_decline",
        ):
            assert identifier not in rule


def test_language_rule_keeps_internal_identifiers_unchanged() -> None:
    prompt = build_system_prompt(
        find_task("d02_hesitation_then_decline").agent_view(), "Policy."
    )
    assert "Internal tool names, schemas and API identifiers never change language" in (
        prompt
    )
    # The tool schemas the agent receives are unchanged by the prompt rule.
    names = {tool.name for tool in build_toolkit(CmOutboundDB()).list_tools()}
    assert "verify_customer_identity" in names
    assert "check_offer_eligibility" in names


def test_prompt_version_is_bumped_and_recorded() -> None:
    assert PROMPT_VERSION == "system-v4"
    task = find_task("d01_plan_content_explanation")
    outcome = execute_task(
        task,
        agent_factory=lambda: LLMBackedAgent(_FinishClient()),
        agent_name="llm",
    )
    assert outcome.prompt_version == PROMPT_VERSION
    expected_prompt = build_system_prompt(
        task.agent_view("d01"), policy_for_domain(task.domain_id)
    )
    assert outcome.prompt_hash == hashlib.sha256(
        expected_prompt.encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Explicit language preference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("请用中文回复。", "zh"),
        ("你可以说中文。", "zh"),
        ("用中文沟通更方便。", "zh"),
        ("请用英文回复。", "en"),
        ("说英文也可以。", "en"),
        ("Please reply in English.", "en"),
        ("Could you speak Chinese?", "zh"),
        ("不要说英文。", ""),
        ("别用中文了。", ""),
        ("The weather is nice.", ""),
    ),
)
def test_requested_language_recognises_explicit_preferences(
    text: str, expected: str
) -> None:
    assert requested_language(text) == expected


def test_explicit_preference_overrides_a_later_turn_in_the_other_language() -> None:
    english_preference = [
        ConversationMessage(step=1, role="user", content="请用英文回复，谢谢。"),
        ConversationMessage(step=2, role="agent", content="Of course."),
        ConversationMessage(step=3, role="user", content="那算了，我这次先不办了。"),
    ]
    assert reply_language(english_preference) == "en"

    chinese_preference = [
        ConversationMessage(step=1, role="user", content="Please speak Chinese."),
        ConversationMessage(step=2, role="agent", content="好的。"),
        ConversationMessage(step=3, role="user", content="No thanks, I'll pass."),
    ]
    assert reply_language(chinese_preference) == "zh"


def test_without_a_preference_the_latest_customer_turn_decides() -> None:
    assert (
        reply_language(
            [ConversationMessage(step=1, role="user", content="每个月多少钱？")]
        )
        == "zh"
    )
    assert (
        reply_language(
            [ConversationMessage(step=1, role="user", content="How much is it?")]
        )
        == "en"
    )
    # Agent text never decides the language.
    assert (
        reply_language(
            [
                ConversationMessage(step=1, role="user", content="多少钱？"),
                ConversationMessage(step=2, role="agent", content="15 CNY."),
            ]
        )
        == "zh"
    )


# ---------------------------------------------------------------------------
# Deterministic backstop: the closing acknowledgement follows the customer
# ---------------------------------------------------------------------------


def test_chinese_customer_decline_produces_a_chinese_agent_response() -> None:
    action = _agent_action(
        "d02_hesitation_then_decline",
        [
            ConversationMessage(step=1, role="agent", content="您是否同意办理？"),
            ConversationMessage(step=8, role="user", content="那算了，我这次先不办了。"),
        ],
    )
    assert isinstance(action, SayAction)
    assert "明白您的决定" in action.message
    assert "不为您办理" in action.message
    assert "will not set this up" not in action.message


def test_english_customer_decline_stays_supported() -> None:
    action = _agent_action(
        "d02_hesitation_then_decline",
        [
            ConversationMessage(step=1, role="agent", content="Shall I enroll you?"),
            ConversationMessage(step=8, role="user", content="No thanks, I'll pass."),
        ],
    )
    assert isinstance(action, SayAction)
    assert "I will not set this up for you" in action.message
    assert not any("\u4e00" <= character <= "\u9fff" for character in action.message)


def test_explicit_preference_selects_the_acknowledgement_language() -> None:
    action = _agent_action(
        "d02_hesitation_then_decline",
        [
            ConversationMessage(step=1, role="user", content="请用英文回复。"),
            ConversationMessage(step=2, role="agent", content="Of course."),
            ConversationMessage(step=8, role="user", content="那算了，先不办了。"),
        ],
    )
    assert isinstance(action, SayAction)
    assert "I will not set this up for you" in action.message


def test_a_non_refusal_closing_turn_finishes_without_a_new_message() -> None:
    """The language rule must not turn a neutral close into an acknowledgement."""
    for message in (
        "嗯……我还有点犹豫。",
        "哦，明白了，那这次就算了，谢谢你帮我查。",
        "好的，听明白了，谢谢你解释。",
        "如果我不办理会怎样？",
    ):
        action = _agent_action(
            "d02_hesitation_then_decline",
            [
                ConversationMessage(step=1, role="agent", content="您好。"),
                ConversationMessage(step=8, role="user", content=message),
            ],
        )
        assert isinstance(action, FinishAction), message
        assert customer_declined(message) is False, message
