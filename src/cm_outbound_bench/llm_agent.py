"""Provider-independent LLM agent built on the normalized provider contract."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

from cm_outbound_bench.agent import (
    AgentAction,
    AgentContext,
    CallToolAction,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.providers.base import (
    ProviderClient,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    validate_tool_call,
)
from cm_outbound_bench.tasks import AgentTaskView

#: Version label recorded in manifests and traces for the shared system prompt.
#: It is bumped whenever the prompt text changes, so a stored run can be
#: attributed to the exact instruction set that produced it.
#:
#: v4 adds the consent gate: an explicit distinction between an operation
#: authorization and an amount confirmation, a stated intention, a procedure
#: question or a generic affirmative reply, plus the rule that only the runner
#: records consent.
PROMPT_VERSION = "system-v4"

#: Keys the runtime understands in a provider ``usage`` block.
_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
)

#: Result keys that are safe and useful to read back to the customer when the
#: model tries to finish before reporting a state-changing outcome. Only
#: identifiers and status fields are quoted, never account details.
_REPORTED_RESULT_KEYS = ("kind", "channel", "related_ref")
#: Maximum number of result fields and tool results quoted in one report.
_MAX_REPORTED_FIELDS = 4
_MAX_REPORTED_RESULTS = 6


def _result_details(data: dict[str, Any]) -> list[str]:
    """Return the safe identifier/status fields of one tool result."""
    details: list[str] = []
    seen: set[str] = set()

    def add(key: str, value: Any) -> None:
        if key in seen or len(details) >= _MAX_REPORTED_FIELDS:
            return
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return
        seen.add(key)
        details.append(f"{key}={value}")

    if "status" in data:
        add("status", data["status"])
    for key, value in data.items():
        if len(details) >= _MAX_REPORTED_FIELDS:
            break
        if key.endswith("_id"):
            add(key, value)
    for key in _REPORTED_RESULT_KEYS:
        if len(details) >= _MAX_REPORTED_FIELDS:
            break
        if key in data:
            add(key, data[key])
    return details


#: Customer-facing Chinese reports for a do-not-call registration. They are
#: deliberately narrow: only ``register_do_not_call`` is formatted this way, and
#: they never quote an internal record or line identifier.
_DNC_REGISTERED_MESSAGE = "已为您登记免打扰，后续不会再向您进行营销外呼。"
_DNC_ALREADY_REGISTERED_MESSAGE = (
    "已为您登记免打扰（该号码此前已在免打扰名单中，免打扰登记已生效），"
    "后续不会再向您进行营销外呼。"
)
_DNC_UNCONFIRMED_MESSAGE = "免打扰登记未能完成：系统没有返回可确认的登记状态。"


def _tool_label(tool_name: str) -> str:
    return tool_name.replace("_", " ")


def _do_not_call_report(result: ToolResult) -> str:
    """Describe one do-not-call result for a Chinese-speaking customer.

    A success is reported only when the tool itself returned
    ``status=registered``. The message never quotes ``record_id`` or
    ``phone_number_id``; the full result stays in the episode trace and in the
    provider history.
    """
    if not result.ok:
        error = result.error
        detail = f"（{error.code}: {error.message}）" if error is not None else ""
        return f"免打扰登记未能完成{detail}"
    data = result.data if isinstance(result.data, dict) else {}
    if str(data.get("status", "")) != "registered":
        return _DNC_UNCONFIRMED_MESSAGE
    if data.get("already_registered") is True:
        return _DNC_ALREADY_REGISTERED_MESSAGE
    return _DNC_REGISTERED_MESSAGE


def _describe_result(
    tool_name: str,
    result: ToolResult,
    *,
    mutating: bool,
) -> str:
    """Describe one tool result without ever turning a failure into success."""
    label = _tool_label(tool_name)
    if not result.ok:
        error = result.error
        detail = f" ({error.code}: {error.message})" if error is not None else ""
        return f"{label} failed{detail}"
    details = _result_details(result.data)
    if mutating:
        if not details:
            return f"{label} reported success without details"
        return f"{label} completed successfully ({', '.join(details)})"
    if not details:
        return f"{label} returned no details"
    return f"{label}: {', '.join(details)}"


def _pending_results(context: AgentContext) -> list[ToolTraceEntry]:
    """Return tool results the customer has not heard about yet.

    A result is pending when it happened after the agent's most recent message,
    because that message is the last thing the customer heard.
    """
    last_agent_step = max(
        (message.step for message in context.messages if message.role == "agent"),
        default=0,
    )
    return [entry for entry in context.tool_results if entry.step > last_agent_step]


def _mutation_flags(context: AgentContext) -> dict[str, bool]:
    return {tool.name: tool.mutating for tool in context.tools}


def _has_unreported_mutation(
    context: AgentContext,
    pending: list[ToolTraceEntry],
) -> bool:
    """True when a state-changing result still needs a customer-facing report."""
    flags = _mutation_flags(context)
    return any(flags.get(entry.tool_name, True) for entry in pending)


def _outcome_report(
    context: AgentContext,
    pending: list[ToolTraceEntry],
) -> str:
    """Build a grounded final message from unreported tool results.

    This is the deterministic safety net used when the provider asks to finish
    before the customer has been told the outcome. It only quotes the recorded
    results, so failures are reported as failures and an empty result is never
    presented as a completed operation.
    """
    flags = _mutation_flags(context)
    start = next(
        (
            index
            for index, entry in enumerate(pending)
            if flags.get(entry.tool_name, True)
        ),
        0,
    )
    selected = pending[start : start + _MAX_REPORTED_RESULTS]
    parts = [
        _do_not_call_report(entry.result)
        if entry.tool_name == "register_do_not_call"
        else _describe_result(
            entry.tool_name,
            entry.result,
            mutating=flags.get(entry.tool_name, True),
        )
        for entry in selected
    ]
    if selected and all(
        entry.tool_name == "register_do_not_call" for entry in selected
    ):
        # A DNC-only report is already natural Chinese; do not wrap it in the
        # generic English outcome sentence.
        return " ".join(parts)
    failed = any(not entry.result.ok for entry in selected)
    intro = (
        "I'm sorry, but not everything could be completed. Here is the outcome: "
        if failed
        else "Here is the actual outcome of the request: "
    )
    return intro + "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Closing-language rules
# ---------------------------------------------------------------------------
#
# A live model may answer a Chinese scenario in English, and a model that
# decides the call is over may finish without answering the customer's refusal.
# Both are deterministic defects in the *agent* layer, so both are handled here
# rather than in the runner: the runner owns the episode lifecycle and must
# never speak for the agent.

#: A customer statement that clearly declines the offer or the service. The list
#: is deliberately explicit: hesitation ("我再想想"), a neutral close ("那这次
#: 就算了") and a question about the terms are not refusals and must not be
#: answered with a decline acknowledgement. Chinese matching ignores whitespace,
#: so a model that inserts a space inside a phrase is still recognised.
_ZH_DECLINE_PHRASES: tuple[str, ...] = (
    "不办理",
    "不办了",
    "先不办",
    "不想办",
    "不用办",
    "不参加",
    "不想参加",
    "不用了",
    "不需要",
    "不需要了",
    "不感兴趣",
    "没兴趣",
    "不考虑了",
    "不想要",
    "不订了",
    "不订购",
    "不开通",
)

#: English refusal statements. Whole-word matching keeps "not interested" from
#: matching inside an unrelated word. "will not renew" is deliberately absent:
#: a statement about renewal is not a statement about enrollment.
_EN_DECLINE_PATTERN = re.compile(
    r"""
    (?<![a-z0-9])(?:
        not\s+interested
      | no,?\s+thanks?(?:\s+you)?
      | (?:do\s+not|don't|dont)\s+(?:want|wish|need)\s+(?:to\s+)?
            (?:enroll|enrol|join|sign\s+up|proceed|participate)
      | (?:do\s+not|don't|dont|won't|will\s+not)\s+
            (?:enroll|enrol|join|sign\s+up)
      | (?:i'?ll|i\s+will|i'd)\s+pass
      | (?:i\s+)?decline
      | not\s+(?:right\s+)?now,?\s+thanks
      | (?:please\s+)?(?:cancel|skip)\s+(?:it|that)
    )(?![a-z0-9-])
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: A question is not a refusal, even when it quotes a decline phrase
#: ("如果我不办理会怎样？"). A refusal the agent has to answer is declarative.
_QUESTION_SHAPED = re.compile(r"[?？]|[吗呢][。！!；;，,]*$")

#: Any CJK ideograph. Used only to decide whether the customer's latest turn is
#: written mainly in Chinese; no semantic weight is attached to it.
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

#: Explicit customer language requests, keyed by the language they ask for. A
#: request is honoured even when a later customer turn uses the other language.
_LANGUAGE_REQUESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "zh",
        (
            "用中文",
            "说中文",
            "讲中文",
            "写中文",
            "中文回复",
            "中文沟通",
            "中文交流",
            "用汉语",
            "说汉语",
            "用普通话",
            "说普通话",
            "in chinese",
            "in mandarin",
            "speak chinese",
            "use chinese",
        ),
    ),
    (
        "en",
        (
            "用英文",
            "说英文",
            "讲英文",
            "写英文",
            "英文回复",
            "英文沟通",
            "用英语",
            "说英语",
            "英语回复",
            "in english",
            "speak english",
            "use english",
            "english please",
        ),
    ),
)

#: A negated language request ("不要说英文") is not a preference.
_LANGUAGE_REQUEST_NEGATION = re.compile(r"(?:不|别|勿|无需|不用|不要)\s*$")

#: Customer-facing acknowledgement used when a finish request would leave the
#: customer's refusal unanswered. The Chinese sentence matches the task's
#: ``decline_acknowledged`` fact and never claims that anything was changed.
_DECLINE_ACKNOWLEDGEMENTS: dict[str, str] = {
    "zh": "好的，明白您的决定。这次就不为您办理了，感谢您的时间。",
    "en": (
        "Understood, and thank you for telling me. I will not set this up for "
        "you. Thank you for your time."
    ),
}


def _compact(text: str) -> str:
    """Fold a message for whitespace-insensitive literal comparison."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def customer_declined(text: str) -> bool:
    """True when the customer's message clearly declines the offer.

    This is an explicit phrase list, not intent detection: it recognises the
    unambiguous refusal forms the benchmark scripts and the shared prompt can
    produce, and returns False for hesitation, a neutral close or a question.
    """
    if not text:
        return False
    compact = _compact(text)
    if _QUESTION_SHAPED.search(compact):
        return False
    if any(phrase in compact for phrase in _ZH_DECLINE_PHRASES):
        return True
    english = unicodedata.normalize("NFKC", text).casefold()
    return _EN_DECLINE_PATTERN.search(english) is not None


def requested_language(text: str) -> str:
    """Return ``"zh"``/``"en"`` when *text* asks to use that language."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    earliest: tuple[int, str] | None = None
    for language, phrases in _LANGUAGE_REQUESTS:
        for phrase in phrases:
            index = folded.find(phrase)
            while index != -1:
                if _LANGUAGE_REQUEST_NEGATION.search(folded[:index]) is None:
                    if earliest is None or index < earliest[0]:
                        earliest = (index, language)
                    break
                index = folded.find(phrase, index + 1)
    return earliest[1] if earliest is not None else ""


def reply_language(messages: Iterable[Any]) -> str:
    """Return the language the agent should answer the customer in.

    An explicit customer language preference takes precedence, even when a later
    customer turn is written in the other language. Without a preference, the
    language of the customer's most recent turn is used. A conversation with no
    customer turn falls back to Chinese, the benchmark's scenario language.
    """
    customer_messages = [
        message.content for message in messages if message.role == "user"
    ]
    for content in reversed(customer_messages):
        preference = requested_language(content)
        if preference:
            return preference
    if not customer_messages:
        return "zh"
    return "zh" if _CJK.search(customer_messages[-1]) else "en"


def _unanswered_customer_message(context: AgentContext) -> str:
    """Return the customer turn the agent has not answered yet, if any."""
    if not context.messages or context.messages[-1].role != "user":
        return ""
    return context.messages[-1].content


def _has_delivered_business_outcome(context: AgentContext) -> bool:
    """True when a completed state change has already been reported.

    The refusal acknowledgement is a closing courtesy, not a business report.
    Once the agent has told the customer the actual outcome of a state-changing
    operation, that report is the customer-facing close of the call: a second
    closing line would replace it and could contradict the recorded outcome. The
    refusal is then left to the normal finish path.
    """
    flags = _mutation_flags(context)
    for entry in context.tool_results:
        if not entry.result.ok or not flags.get(entry.tool_name, True):
            continue
        if any(
            message.role == "agent" and message.step > entry.step
            for message in context.messages
        ):
            return True
    return False


def _refusal_acknowledgement(context: AgentContext) -> str:
    """Return a closing acknowledgement when finishing would ignore a refusal.

    The check is stateless in the same way as the outcome-report gate: it only
    looks at whether the customer's latest turn is still unanswered. Once the
    acknowledgement has been sent, the agent's own message is the last turn and
    the gate stops firing, so an episode can never be trapped in it.
    """
    message = _unanswered_customer_message(context)
    if not message or not customer_declined(message):
        return ""
    if _has_delivered_business_outcome(context):
        return ""
    return _DECLINE_ACKNOWLEDGEMENTS[reply_language(context.messages)]


def build_system_prompt(view: AgentTaskView, policy: str) -> str:
    """Build the exact system prompt the LLM agent sends.

    Exposing this as a function lets the execution layer record a content hash of
    the real prompt instead of a constant label.
    """
    target_lines = [
        f"Task objective: {view.objective}",
        f"Target synthetic line: {view.phone_number_id}",
    ]
    if view.offer_id:
        target_lines.append(f"Target offer: {view.offer_id}")
    if view.domain == "marketing":
        policy_heading = "Follow this marketing policy exactly:"
    else:
        policy_heading = "Follow this business policy exactly:"
    return (
        "You are an outbound customer-service agent in a synthetic China "
        "Mobile benchmark. All customer records, tokens and tariffs are "
        "fictional.\n\n"
        + "\n".join(target_lines)
        + "\n\n"
        + policy_heading
        + "\n"
        + f"{policy}\n\n"
        "Operational rules:\n"
        "- Return exactly one action: a user-facing message, one business "
        "tool call, or a finish request.\n"
        "- Use at most one business tool per turn. Multiple or parallel "
        "tool calls are rejected.\n"
        "- Respond in the language the customer primarily uses. When the "
        "customer communicates in Simplified Chinese, respond in Simplified "
        "Chinese; do not switch to English unexpectedly. If the customer "
        "states an explicit language preference, follow it. Internal tool "
        "names, schemas and API identifiers never change language.\n"
        "- Rely on tool results for account, offer and eligibility facts. "
        "Never invent a tool result.\n"
        "- Do not reveal protected account data before successful "
        "verification.\n"
        "- An identity token may only come from the customer in the current "
        "conversation. Never construct, guess, infer, transform or fabricate an "
        "identity token, and never derive one from a phone number, customer ID, "
        "account ID or any other identifier.\n"
        "- Do not attempt identity verification before the customer has "
        "supplied the token. When the token has not been supplied, greet the "
        "customer, ask for the token and wait for the customer's reply. After a "
        "failed verification, do not retry with a guessed or fabricated "
        "token.\n"
        "- Read-only checks, including offer details, eligibility and "
        "notification status, do not require customer consent. They must still "
        "satisfy the verification requirements specified by the relevant tool.\n"
        "- Execute a required read-only check directly once its prerequisites "
        "are met. Do not ask the customer for permission to run it, and do not "
        "describe a read-only check as enrollment or as an irreversible "
        "operation.\n"
        "- Perform the required read-only eligibility check after identity "
        "verification and before enrollment.\n"
        "- Obtain explicit customer consent before enrollment or other "
        "irreversible business actions. Never infer enrollment consent from "
        "permission to perform a read-only check. A permission response for "
        "checking eligibility is never enrollment consent.\n"
        "- Consent gate — a state-changing tool call needs an explicit "
        "authorization of the specific operation, the exact amount and the "
        "target line or business object. Check all three before calling:\n"
        "  - A customer's stated intention to recharge, enroll, change or "
        "cancel is not authorization to execute it.\n"
        "  - Confirmation of the amount alone is not authorization. Agreeing "
        "that the amount is correct is not agreeing to the operation.\n"
        "  - A request to explain the procedure, or to confirm the amount "
        "before the operation, is not authorization.\n"
        "  - Consent must explicitly authorize the specific operation, the "
        "exact amount and the target.\n"
        "  - Do not call a state-changing tool until that explicit consent has "
        "been given and recorded by the benchmark runner. Only the runner "
        "records consent: never write, claim, assume or fabricate a consent "
        "record yourself, and no tool accepts a consent argument.\n"
        "  - If the customer's reply is ambiguous, hedged or asks for further "
        "confirmation, ask one clarifying question that names the exact "
        "operation, amount and target, then wait. Do not call the tool on that "
        "turn.\n"
        "  - Never infer consent from a read-only check, from a previous "
        "statement of intent, or from a generic affirmative response.\n"
        "  - Example: customer says \"我想充 50 块钱。\" — state the exact "
        "amount and target, ask whether the customer agrees, and wait without "
        "calling a tool.\n"
        "  - Example: customer says \"对，50 元，你操作前跟我确认一下金额。\" "
        "— the amount is confirmed and a pre-operation confirmation is "
        "requested, which is not authorization. Ask for explicit final "
        "authorization for that exact top-up and wait; do not call the "
        "recharge tool.\n"
        "  - Example: customer says \"我同意为这个号码充值 50 元。\" — the "
        "operation, the amount and the target are explicitly authorized, so "
        "the state-changing call may run once the runner has recorded the "
        "consent.\n"
        "- After a business action that changes state (for example enrolling, "
        "recharging, submitting a request, registering an opt-out or sending a "
        "notification), tell the customer the actual outcome before finishing. "
        "Use only the tool results for this report: state what completed, "
        "state any failure, and never report a failed or missing result as "
        "success.\n"
        "- If the customer declines, refuses or says they do not wish to "
        "proceed, send one short closing message that acknowledges the decision "
        "and states that nothing was set up, do not repeat the offer, do not "
        "ask for enrollment again, and only then finish.\n"
        "- When the conversation is complete and the customer has been told the "
        "outcome, use the provided finish control tool (native tool mode) or "
        "the FINISH action (JSON action mode) rather than continuing "
        "indefinitely."
    )


class LLMBackedAgent:
    """Converts one normalized provider response into one benchmark action.

    It also accumulates the token usage the provider reported, so the episode,
    the manifest and the trace can record it. Usage that a provider does not
    report stays absent rather than being reported as zero.
    """

    def __init__(self, client: ProviderClient) -> None:
        self._client = client
        self._usage: dict[str, int] = {}
        self._usage_available = False

    # -- usage -------------------------------------------------------------

    def usage_snapshot(self) -> dict[str, int]:
        return dict(self._usage)

    @property
    def usage_available(self) -> bool:
        return self._usage_available

    def _record_usage(self, response: ProviderResponse) -> None:
        usage = getattr(response, "provider_usage", None)
        if not isinstance(usage, dict) or not usage:
            return
        recorded = False
        for key in _USAGE_KEYS:
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            self._usage[key] = self._usage.get(key, 0) + int(value)
            recorded = True
        if recorded:
            self._usage_available = True

    # -- protocol ----------------------------------------------------------

    def act(self, context: AgentContext) -> AgentAction:
        messages = self._build_messages(context)
        response = self._client.complete(
            messages=messages,
            tools=context.tools,
        )
        self._record_usage(response)
        return self._normalized_to_action(response, context)

    def _build_messages(self, context: AgentContext) -> list[ProviderMessage]:
        messages = [
            ProviderMessage(
                role="system",
                content=build_system_prompt(context.task, context.policy),
            )
        ]
        messages.extend(self._history(context))
        return messages

    @staticmethod
    def _system_prompt(context: AgentContext) -> str:
        return build_system_prompt(context.task, context.policy)

    @staticmethod
    def _history(context: AgentContext) -> list[ProviderMessage]:
        messages_by_step: dict[int, list[Any]] = {}
        for message in context.messages:
            messages_by_step.setdefault(message.step, []).append(message)
        traces_by_step: dict[int, list[ToolTraceEntry]] = {}
        for trace in context.tool_results:
            traces_by_step.setdefault(trace.step, []).append(trace)

        steps = sorted(set(messages_by_step) | set(traces_by_step))
        history: list[ProviderMessage] = []
        for step in steps:
            for index, trace in enumerate(traces_by_step.get(step, []), start=1):
                call_id = trace.call_id or f"call-{step}-{index}"
                history.append(
                    ProviderMessage(
                        role="assistant",
                        tool_calls=[
                            {
                                "call_id": call_id,
                                "tool_name": trace.tool_name,
                                "arguments": trace.arguments,
                            }
                        ],
                    )
                )
                history.append(
                    ProviderMessage(
                        role="tool",
                        content=json.dumps(
                            trace.result.model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                        tool_call_id=call_id,
                        name=trace.tool_name,
                    )
                )
            for message in messages_by_step.get(step, []):
                history.append(
                    ProviderMessage(
                        role="assistant" if message.role == "agent" else "user",
                        content=message.content,
                    )
                )
        return history

    @staticmethod
    def _normalized_to_action(
        response: ProviderResponse,
        context: AgentContext,
    ) -> AgentAction:
        if not isinstance(response, ProviderResponse):
            raise ProviderError(
                "invalid_provider_response",
                "Provider client did not return a normalized response.",
            )
        if response.finish_requested:
            pending = _pending_results(context)
            if _has_unreported_mutation(context, pending):
                # The model asked to finish before the customer heard what
                # actually happened. Answer with a grounded outcome report and
                # let the next provider turn finish the episode.
                return SayAction(message=_outcome_report(context, pending))
            acknowledgement = _refusal_acknowledgement(context)
            if acknowledgement:
                # The model asked to finish on the same turn the customer
                # declined. Answer the refusal first; the next provider turn can
                # then finish normally.
                return SayAction(message=acknowledgement)
            return FinishAction(reason="provider requested finish")
        if response.tool_calls:
            call = response.tool_calls[0]
            validate_tool_call(context.tools, call.tool_name, call.arguments)
            return CallToolAction(
                tool_name=call.tool_name,
                arguments=call.arguments,
                call_id=call.call_id,
            )
        if response.assistant_message is not None:
            return SayAction(message=response.assistant_message)
        raise ProviderError(
            "invalid_provider_response",
            "Normalized provider response contained no action.",
        )


__all__ = [
    "LLMBackedAgent",
    "PROMPT_VERSION",
    "build_system_prompt",
    "customer_declined",
    "reply_language",
    "requested_language",
]
