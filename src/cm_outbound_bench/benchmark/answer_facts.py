"""Deterministic answer-fact assertions for information-delivery tasks.

The evaluator's state, tool and policy checks answer "did the agent do the right
thing?". They do not answer "did the agent tell the customer the truth?".  This
module is a small, bounded, deterministic layer for that second question.

It deliberately does **not** attempt unrestricted natural-language truth
verification. Each fact is an explicit, task-authored pattern list:

* a *required fact* passes only when at least one accepted pattern occurs as an
  affirmed statement in the agent's text;
* a *forbidden fact* fails when any forbidden pattern occurs as an affirmed
  statement.

A required fact may additionally be *grounded* in a recorded tool result. When
it declares ``grounding_tools``/``grounding_field``/``grounding_value``, the
statement must be both present in the agent text **and** backed by a successful
result from one of those tools. That keeps a confident-but-false free-text claim
from satisfying a fact the tool never produced.

"Affirmed" is decided by a short negation window immediately before the match.
A negation marker inside the pattern itself (for example ``不自动续订`` or
``没有欠费``) is part of the literal statement the task author wants to require,
so it does not de-affirm the occurrence. Only text *before* the match does.

Amounts are compared by value, not by spelling. A decimal amount is
canonicalized before matching (``29.00`` -> ``29``, ``29.50`` -> ``29.5``), and
every numeric token in a pattern is required to stand on its own numeric
boundary, so ``29 元`` matches ``29.00 元/月`` without matching ``129 元`` or
``29.5 元``. This is still deterministic literal matching, not fuzzy matching.

Patterns are language-specific literals. Nothing here translates or infers
across languages: a bilingual fact works because the task declares both the
Chinese and the English forms of the same statement, and a statement in either
language satisfies it. A claim in the opposite polarity is never satisfied by
the negative pattern, because the polarity is part of the literal
(``does not renew automatically`` is a different literal from
``renews automatically``).

Which agent text a fact is checked against is chosen by the declared scope, and
that choice is made from recorded structure, never from prose. ``any_agent_message``
is every agent message. ``final_report`` is the agent messages after the last
simulated customer turn, which is where a post-mutation outcome report is
delivered. ``final_answer`` is the last *substantive* customer-facing agent
message: the Runner keeps the turn after the simulated customer closes the
conversation, so an agent may add a closing courtesy, and the Runner records that
turn with the ``post_conversation_message`` action outcome. Such a courtesy is
not the agent's answer. A post-conversation message that *answers* a terminal
customer decision (a refusal, a do-not-call request, a consent decision) is the
answer, and is kept; the distinction comes from the recorded simulator intent,
not from the message text.

This is a heuristic, and it is documented as one. Tasks whose facts cannot be
expressed this way must declare no assertions; the evaluator then reports the
answer-correctness dimension as NOT_APPLICABLE instead of claiming a pass.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from cm_outbound_bench.runner import POST_CONVERSATION_MESSAGE_OUTCOME

#: Markers that flip the polarity of a *preceding* statement window.
NEGATION_MARKERS: tuple[str, ...] = (
    "不",
    "没",
    "无",
    "未",
    "非",
    "别",
    "并非",
    "绝非",
)

#: Idioms that contain a negation character but do not negate what follows.
#: They are masked out of the inspection window before polarity is decided.
NEGATION_FALSE_FRIENDS: tuple[str, ...] = (
    "不过",
    "不仅",
    "不但",
    "不管",
    "不论",
    "不错",
    "不好意思",
    "不得不",
    "无论",
)

#: How many characters before a match are inspected for a negation marker.
DEFAULT_NEGATION_WINDOW = 8

#: English negation suffixes. Whitespace is removed by :func:`normalise_text`, so
#: a negated English statement ends with one of these immediately before the
#: matched pattern ("was not successfully enrolled" -> "...wasnotsuccessfully...").
_EN_NEGATION_SUFFIXES: tuple[str, ...] = ("not", "never", "n't", "n’t")

_WHITESPACE = re.compile(r"\s+")
_SECRET_LIKE = re.compile(r"(sk-[A-Za-z0-9]{8,}|Bearer\s+\S+)", re.IGNORECASE)
#: Inline Markdown emphasis and code markers. They carry no business meaning, so
#: they are removed before literal matching. This lets ``**SENT**`` and ``SENT``
#: compare equal without introducing fuzzy or semantic matching.
_MARKDOWN_EMPHASIS = re.compile(r"[*_`]+")

#: A complete decimal amount embedded in text. A trailing sentence period is not
#: part of the number, so only a following digit or a following decimal amount is
#: treated as a continuation. The leading boundary stops a fractional tail such
#: as ``1.29`` from being read as a standalone integer.
_DECIMAL_AMOUNT = re.compile(r"(?<![\d.])(\d+)\.(\d+)(?!\d|\.\d)")

#: One numeric token inside a normalized literal pattern. A pattern may contain
#: digits without being made only of digits (``29 元`` normalizes to ``29元``),
#: so every numeric token is boundary-checked.
_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def canonicalize_decimal_amounts(text: str) -> str:
    """Canonicalize decimal amounts without changing their numeric value.

    ``29.0`` and ``29.00`` become ``29``, ``29.50`` becomes ``29.5``, and
    ``29.05`` keeps its significant trailing digit. Only a complete numeric token
    is rewritten, so a date-like or identifier-like string without a decimal
    amount is untouched.
    """

    def replace(match: re.Match[str]) -> str:
        whole, fraction = match.group(1), match.group(2).rstrip("0")
        return f"{whole}.{fraction}" if fraction else whole

    return _DECIMAL_AMOUNT.sub(replace, text)


def normalise_text(text: str) -> str:
    """Normalise text for deterministic literal matching.

    NFKC folds full-width digits/letters to ASCII, which lets a task author write
    ``10`` and still match ``１０``. Common inline Markdown emphasis markers
    (``*``, ``_`` and backticks) are removed, so an agent may bold a value
    (``**SENT**``) without changing the literal it is compared against. Case is
    folded and **all** whitespace is removed, so ``29 CNY``, ``29CNY`` and
    ``29 元`` behave predictably and a Chinese paraphrase is not sensitive to
    spacing. Decimal amounts are then canonicalized, so ``29.00 元/月`` and
    ``29 元`` compare equal. Because whitespace no longer separates tokens,
    numeric patterns are matched with numeric-token boundaries rather than by
    relying on spaces.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.replace("\u3000", " ")
    folded = _MARKDOWN_EMPHASIS.sub("", folded)
    return canonicalize_decimal_amounts(
        _WHITESPACE.sub("", folded).strip().lower()
    )


def _numeric_token_spans(pattern: str) -> list[tuple[int, int]]:
    """Return the offsets of every numeric token in a normalized pattern."""
    return [match.span() for match in _NUMERIC_TOKEN.finditer(pattern)]


def _is_numeric_continuation(character: str) -> bool:
    return character.isdigit() or character == "."


def _numeric_boundary_ok(
    text: str,
    match_index: int,
    span: tuple[int, int],
) -> bool:
    """True when a numeric token is not part of a larger number.

    Whitespace is removed by :func:`normalise_text`, so a non-digit character is
    the only reliable token separator. A decimal point is also a continuation:
    ``29`` must not match inside ``129``, ``29.5``, ``29.50`` or ``1.29``.
    """
    start, end = span
    before_index = match_index + start - 1
    after_index = match_index + end
    before = text[before_index] if before_index >= 0 else ""
    after = text[after_index] if after_index < len(text) else ""
    return not _is_numeric_continuation(before) and not _is_numeric_continuation(
        after
    )


def _is_affirmed(text: str, index: int, window: int) -> bool:
    """Return True when no negation marker precedes the match within *window*."""
    if window <= 0:
        return True
    start = max(0, index - window)
    prefix = text[start:index]
    for idiom in NEGATION_FALSE_FRIENDS:
        prefix = prefix.replace(idiom, "\u0000" * len(idiom))
    if prefix.endswith(_EN_NEGATION_SUFFIXES):
        return False
    return not any(marker in prefix for marker in NEGATION_MARKERS)


def find_affirmed_occurrences(
    text: str,
    pattern: str,
    *,
    negation_window: int = DEFAULT_NEGATION_WINDOW,
) -> list[int]:
    """Return match indices for *pattern* that are not negated by their prefix.

    Every numeric token inside the pattern must stand on its own numeric
    boundary, so ``100`` does not match inside ``1000``, ``29 元`` does not match
    inside ``129 元``, and ``29`` does not match inside ``29.5``. Patterns with
    no numeric token use plain literal containment.
    """
    if not pattern:
        return []
    occurrences: list[int] = []
    numeric_spans = _numeric_token_spans(pattern)
    index = text.find(pattern)
    while index != -1:
        boundary_ok = all(
            _numeric_boundary_ok(text, index, span) for span in numeric_spans
        )
        if boundary_ok and _is_affirmed(text, index, negation_window):
            occurrences.append(index)
        index = text.find(pattern, index + 1)
    return occurrences


@dataclass(frozen=True)
class FactOutcome:
    """The deterministic verdict for one declared fact."""

    fact_id: str
    kind: Literal["required", "forbidden"]
    passed: bool
    matched_pattern: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "kind": self.kind,
            "passed": self.passed,
            "matched_pattern": self.matched_pattern,
            "detail": self.detail,
        }


def evaluate_required_fact(
    fact: Any,
    text: str,
    *,
    tool_results: Any = (),
) -> FactOutcome:
    """A required fact passes when an accepted pattern is affirmed.

    ``tool_results`` is optional. It is consulted only when the fact declares
    tool-result grounding, so existing callers that pass plain text keep their
    behaviour unchanged. A grounded fact never passes unless the recorded tool
    result itself contains the expected field value.
    """
    window = int(getattr(fact, "negation_window", DEFAULT_NEGATION_WINDOW))
    for pattern in fact.accepted_patterns:
        if find_affirmed_occurrences(
            text, normalise_text(pattern), negation_window=window
        ):
            grounded, reason = _grounded_in_tool_results(fact, tool_results)
            if not grounded:
                return FactOutcome(
                    fact_id=fact.fact_id,
                    kind="required",
                    passed=False,
                    matched_pattern=pattern,
                    detail=(
                        f"Fact {fact.fact_id!r} was stated but is not grounded in "
                        f"a recorded tool result.{reason}"
                    ),
                )
            return FactOutcome(
                fact_id=fact.fact_id,
                kind="required",
                passed=True,
                matched_pattern=pattern,
                detail=(
                    f"Fact {fact.fact_id!r} was stated as required "
                    f"(matched {pattern!r})."
                ),
            )
    return FactOutcome(
        fact_id=fact.fact_id,
        kind="required",
        passed=False,
        detail=(
            f"Fact {fact.fact_id!r} was not stated. Expected one of: "
            + ", ".join(repr(item) for item in fact.accepted_patterns)
            + "."
        ),
    )


def _grounded_in_tool_results(fact: Any, tool_results: Any) -> tuple[bool, str]:
    """Check the optional tool-result grounding declared by a fact.

    A fact with no grounding declaration is always grounded. A fact that does
    declare grounding passes only when a successful result from one of the
    named tools carries ``grounding_field == grounding_value``.

    A list, tuple or set field is matched by exact element membership: every
    element is compared with ``str(item) == value``. The container is never
    stringified, so a reason code cannot pass through a substring or through the
    formatted representation of the whole list.
    """
    tools = tuple(getattr(fact, "grounding_tools", ()) or ())
    value = str(getattr(fact, "grounding_value", "") or "")
    field = str(getattr(fact, "grounding_field", "status") or "status")
    if not tools or not value:
        return True, ""
    for entry in tool_results or ():
        if getattr(entry, "tool_name", "") not in tools:
            continue
        result = getattr(entry, "result", None)
        if not getattr(result, "ok", False):
            continue
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            continue
        field_value = data.get(field, "")
        if isinstance(field_value, (list, tuple, set)):
            if any(str(item) == value for item in field_value):
                return True, ""
            continue
        if str(field_value) == value:
            return True, ""
    return False, (
        f" No successful {'/'.join(tools)} result recorded "
        f"{field}={value!r}."
    )


def evaluate_forbidden_fact(
    fact: Any,
    text: str,
) -> FactOutcome:
    """A forbidden fact fails when a forbidden pattern is affirmed."""
    window = int(getattr(fact, "negation_window", DEFAULT_NEGATION_WINDOW))
    for pattern in fact.forbidden_patterns:
        if find_affirmed_occurrences(
            text, normalise_text(pattern), negation_window=window
        ):
            return FactOutcome(
                fact_id=fact.fact_id,
                kind="forbidden",
                passed=False,
                matched_pattern=pattern,
                detail=(
                    f"Fact {fact.fact_id!r} was contradicted "
                    f"(matched forbidden statement {pattern!r})."
                ),
            )
    return FactOutcome(
        fact_id=fact.fact_id,
        kind="forbidden",
        passed=True,
        detail=f"Fact {fact.fact_id!r} was not contradicted.",
    )


#: Simulator intent of a plain customer sign-off ("好的，谢谢你"). It is
#: ``Intent.END`` in both simulator implementations. A sign-off is not a move the
#: agent has to answer, so anything the agent sends after it is a closing
#: courtesy rather than an answer.
SIGN_OFF_INTENT = "end"


def _post_conversation_steps(run: Any) -> set[int]:
    """Return the steps whose recorded action is a post-conversation message.

    The Runner records one normalized action per step, so a step number
    identifies the message it belongs to. Only ``say`` actions are inspected:
    Tool Arguments and Tool Results are never customer-facing answers. A run
    without recorded actions (an older trace) yields no steps and therefore keeps
    the historical "last agent message" behaviour.
    """
    return {
        action.step
        for action in getattr(run, "actions", None) or ()
        if getattr(action, "action_type", "") == "say"
        and getattr(action, "outcome", "") == POST_CONVERSATION_MESSAGE_OUTCOME
    }


def _customer_signed_off(run: Any) -> bool:
    """True when the customer closed the conversation with a plain sign-off.

    The decision uses the simulator's recorded intent for the last customer turn,
    never the wording of the agent message. When the run carries no structured
    record for that turn the extraction fails closed: a trailing
    post-conversation message is treated as a courtesy, not as an answer.
    """
    last_user_step = max(
        (
            message.step
            for message in run.conversation
            if message.role == "user"
        ),
        default=None,
    )
    if last_user_step is None:
        return False
    closing = [
        event
        for event in getattr(run, "simulator_events", None) or ()
        if getattr(event, "step", None) == last_user_step
    ]
    if not closing:
        return True
    return getattr(closing[-1], "intent", "") == SIGN_OFF_INTENT


def _final_answer_texts(run: Any) -> tuple[list[str], list[str]]:
    """Return ``(eligible, excluded)`` agent message texts for ``final_answer``.

    A message is excluded only when it corresponds to a
    ``post_conversation_message`` action *and* the customer closed the
    conversation with a sign-off. A post-conversation message that answers a
    terminal customer decision stays eligible, because the Runner gives the agent
    exactly that one turn to answer the decision.
    """
    post_steps = _post_conversation_steps(run)
    courtesy = _customer_signed_off(run)
    eligible: list[str] = []
    excluded: list[str] = []
    for message in run.conversation:
        if message.role != "agent":
            continue
        if courtesy and message.step in post_steps:
            excluded.append(message.content)
            continue
        eligible.append(message.content)
    return eligible, excluded


def agent_text_for_scope(run: Any, scope: str) -> str:
    """Return the agent text a required-fact scope applies to.

    ``final_answer`` is the last substantive customer-facing agent message. The
    Runner keeps the turn after the simulated customer closed the conversation,
    so an agent may append a closing courtesy; that turn is recorded as a
    ``post_conversation_message`` action and is not the agent's answer. It is
    skipped, and the last normal agent message is selected instead. When no
    eligible message exists the text is empty, so a courtesy message can never
    satisfy a business fact on its own.

    ``final_report`` is the agent's answer after the conversation's last
    simulated customer turn. In a task whose consent turn closes the
    conversation, this is the outcome report produced after the state-changing
    operations, which is exactly the customer-facing result that must carry the
    notification status. Post-conversation messages therefore stay in this
    scope.

    Every other scope is ``any_agent_message``: the concatenation of all agent
    messages, including post-conversation ones.
    """
    if scope == "final_answer":
        eligible, _ = _final_answer_texts(run)
        return normalise_text(eligible[-1]) if eligible else ""
    if scope == "final_report":
        last_user_step = max(
            (
                message.step
                for message in run.conversation
                if message.role == "user"
            ),
            default=0,
        )
        report = [
            message.content
            for message in run.conversation
            if message.role == "agent" and message.step > last_user_step
        ]
        return normalise_text(" \n ".join(report))
    messages = [
        message.content for message in run.conversation if message.role == "agent"
    ]
    return normalise_text(" \n ".join(messages))


def text_for_forbidden_facts(run: Any, scope: str) -> str:
    """Return the text a forbidden-fact scope must inspect.

    A forbidden fact records a statement the agent must never make. Narrowing
    ``final_answer`` to the last substantive message must not hide a false claim
    made in the closing courtesy that was skipped, so the inspected text is the
    selected answer followed by the post-conversation messages that were excluded
    from it. That is never weaker than the previous behaviour, where the trailing
    message *was* the final answer and was inspected on its own.

    Every other scope keeps exactly the text :func:`agent_text_for_scope`
    returns, so ``any_agent_message`` and ``final_report`` are unchanged.
    """
    if scope != "final_answer":
        return agent_text_for_scope(run, scope)
    eligible, excluded = _final_answer_texts(run)
    return normalise_text(" \n ".join(eligible[-1:] + excluded))


def redact_secret_like(text: str) -> str:
    """Remove credential-looking substrings from free text."""
    return _SECRET_LIKE.sub("[REDACTED]", text)
