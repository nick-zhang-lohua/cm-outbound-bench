"""Agent interface and a deterministic smoke-test agent."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import ConfigDict, Field

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.domain.tools import ToolDefinition
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.tasks import AgentTaskView


class ConversationMessage(DomainModel):
    step: int = Field(ge=1)
    role: Literal["agent", "user"]
    content: str


class SayAction(DomainModel):
    type: Literal["say"] = "say"
    message: str = Field(min_length=1)


class CallToolAction(DomainModel):
    type: Literal["call_tool"] = "call_tool"
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = Field(
        default=None,
        min_length=1,
        description="Provider tool-call ID, when supplied by an LLM provider.",
    )


class FinishAction(DomainModel):
    type: Literal["finish"] = "finish"
    reason: str = ""


AgentAction: TypeAlias = Annotated[
    SayAction | CallToolAction | FinishAction,
    Field(discriminator="type"),
]


class AgentContext(DomainModel):
    """Everything an agent may use for one decision.

    The context is a frozen snapshot with defensively copied contents. An agent
    cannot rewrite the runner's conversation or the environment's tool trace by
    mutating what it received, and it cannot attach new attributes.
    """

    model_config = ConfigDict(frozen=True)

    task: AgentTaskView
    policy: str
    tools: list[ToolDefinition]
    messages: list[ConversationMessage]
    tool_results: list[ToolTraceEntry]
    step: int = Field(ge=1)


class Agent(Protocol):
    """Minimal interface for any deterministic or future LLM-backed agent."""

    def act(self, context: AgentContext) -> AgentAction:
        """Return exactly one next action."""


@runtime_checkable
class UsageReporting(Protocol):
    """Optional capability: report provider token usage for the episode."""

    def usage_snapshot(self) -> dict[str, int]:
        """Return any token counters reported by the provider."""


_TOKEN_PATTERN = re.compile(r"TEST_IDENTITY_[A-Za-z0-9_]+")


class DemoAgent:
    """Deterministic smoke-test agent that completes the five starter tasks.

    It reacts only to the agent-visible task and the user's message text. It
    never reads simulator-private task fields.
    """

    def __init__(self) -> None:
        self._offer: dict[str, Any] | None = None
        self._intro_sent = False
        self._terms_explained = False
        self._verification_explained = False
        self._verified = False
        self._account_requested = False
        self._account_loaded = False
        self._eligibility_requested = False
        self._eligibility: bool | None = None
        self._consent_prompted = False
        self._enrollment_id: str | None = None
        self._sms_requested = False
        self._sms_sent = False
        self._dnc_registered = False
        self._dnc_acked = False
        self._ready_to_finish = False
        self._fatal_error = False
        self._handled_message_count = 0
        self._absorbed_results = 0

    def act(self, context: AgentContext) -> AgentAction:
        self._absorb_results(context)
        if self._ready_to_finish:
            return FinishAction(reason="conversation closed")
        if self._fatal_error:
            return FinishAction(reason="unrecoverable tool error")

        user_message = self._take_unhandled_user_message(context)
        if user_message is not None:
            return self._handle_user_message(user_message, context)

        return self._continue_after_tools(context)

    def _handle_user_message(
        self,
        message: str,
        context: AgentContext,
    ) -> AgentAction:
        if self._is_dnc_request(message):
            return CallToolAction(
                tool_name="register_do_not_call",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                },
            )
        if self._is_not_owner(message):
            self._ready_to_finish = True
            return SayAction(
                message=(
                    "抱歉打扰了。出于隐私保护，我不能向非机主透露或查询"
                    "账户信息。请机主本人联系我们，再见。"
                )
            )
        if self._is_decline(message):
            self._ready_to_finish = True
            return SayAction(message="好的，尊重您的选择，这次不办理。感谢接听，再见。")
        if self._is_suspicion(message) and not self._verification_explained:
            self._verification_explained = True
            return SayAction(
                message=(
                    "您的担心可以理解。您可以挂断后拨打官方10086核实；"
                    "本次身份核验只使用测试身份令牌确认机主身份，"
                    "不会索要银行卡密码，也不会要求转账。"
                    "请提供您的测试身份令牌（TEST_IDENTITY_ 开头）。"
                )
            )

        token = _TOKEN_PATTERN.search(message)
        if token is not None:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "identity_token": token.group(0),
                },
            )

        if self._is_acceptance(message):
            if self._terms_explained and self._verified and self._eligibility is True:
                return CallToolAction(
                    tool_name="enroll_offer",
                    arguments={
                        "phone_number_id": context.task.phone_number_id,
                        "offer_id": context.task.offer_id,
                    },
                )
            if not self._terms_explained:
                return self._explain_terms_and_ask_identity()
            return self._ask_for_consent()

        if self._is_hesitation(message) and self._terms_explained:
            return self._clarify_renewal_and_ask_consent()

        if not self._terms_explained:
            return self._explain_terms_and_ask_identity()
        if not self._verified:
            return self._ask_for_identity()
        if not self._account_loaded:
            return self._request_account(context)
        if self._eligibility is None:
            return self._request_eligibility(context)
        if not self._consent_prompted:
            return self._ask_for_consent()

        self._ready_to_finish = True
        return SayAction(
            message="好的，如果没有其他问题，我就不打扰了。感谢接听，再见。"
        )

    def _continue_after_tools(self, context: AgentContext) -> AgentAction:
        if self._offer is None:
            return CallToolAction(
                tool_name="get_offer_details",
                arguments={"offer_id": context.task.offer_id},
            )
        if not self._intro_sent:
            self._intro_sent = True
            return SayAction(
                message=(
                    "您好，我是中国移动客服代表。请问现在方便吗？"
                    f"我想向您介绍一下『{self._offer['name']}』活动。"
                )
            )
        if self._dnc_registered and not self._dnc_acked:
            self._dnc_acked = True
            self._ready_to_finish = True
            return SayAction(
                message=(
                    "好的，已为您登记免打扰，后续不会再进行营销联系。感谢接听，再见。"
                )
            )
        if self._enrollment_id is not None and not self._sms_requested:
            self._sms_requested = True
            return CallToolAction(
                tool_name="send_confirmation_sms",
                arguments={"enrollment_id": self._enrollment_id},
            )
        if self._sms_sent:
            self._ready_to_finish = True
            return SayAction(
                message=(
                    f"已为您成功办理『{self._offer['name']}』，"
                    "确认短信已模拟发送到您的号码。感谢接听，再见。"
                )
            )
        if self._verified and not self._account_requested:
            return self._request_account(context)
        if self._account_loaded and not self._eligibility_requested:
            return self._request_eligibility(context)
        if self._eligibility is False:
            self._ready_to_finish = True
            return SayAction(
                message=(
                    "抱歉，您的号码目前不符合该活动的参加条件，这次无法办理。"
                    "感谢接听，再见。"
                )
            )
        if self._eligibility is True and not self._consent_prompted:
            return self._ask_for_consent()
        return FinishAction(reason="no further action")

    def _absorb_results(self, context: AgentContext) -> None:
        for entry in context.tool_results[self._absorbed_results :]:
            result = entry.result
            if entry.tool_name == "get_offer_details":
                if result.ok:
                    self._offer = result.data
                else:
                    self._fatal_error = True
            elif entry.tool_name == "verify_customer_identity":
                self._verified = result.ok
                self._fatal_error = not result.ok
            elif entry.tool_name == "get_customer_account":
                self._account_requested = True
                self._account_loaded = result.ok
                self._fatal_error = not result.ok
            elif entry.tool_name == "check_offer_eligibility":
                self._eligibility_requested = True
                if result.ok:
                    self._eligibility = bool(result.data.get("eligible"))
                else:
                    self._fatal_error = True
            elif entry.tool_name == "enroll_offer":
                if result.ok:
                    self._enrollment_id = str(result.data["enrollment_id"])
                else:
                    self._fatal_error = True
            elif entry.tool_name == "send_confirmation_sms":
                self._sms_sent = result.ok
                self._fatal_error = not result.ok
            elif entry.tool_name == "register_do_not_call":
                self._dnc_registered = result.ok
                self._fatal_error = not result.ok
        self._absorbed_results = len(context.tool_results)

    def _take_unhandled_user_message(self, context: AgentContext) -> str | None:
        if not context.messages or context.messages[-1].role != "user":
            return None
        if len(context.messages) <= self._handled_message_count:
            return None
        self._handled_message_count = len(context.messages)
        return context.messages[-1].content

    def _explain_terms_and_ask_identity(self) -> SayAction:
        self._terms_explained = True
        return SayAction(
            message=(
                f"{self._terms_summary()}"
                "为了核对您的号码的参加资格，请提供测试身份令牌"
                "（TEST_IDENTITY_ 开头）。"
            )
        )

    def _ask_for_identity(self) -> SayAction:
        return SayAction(
            message=(
                "为了核对机主身份，我需要您提供测试身份令牌"
                "（TEST_IDENTITY_ 开头），请放心，不会索要其他个人信息。"
            )
        )

    def _ask_for_consent(self) -> SayAction:
        self._consent_prompted = True
        self._terms_explained = True
        return SayAction(
            message=(
                f"我查到您的号码符合『{self._offer['name']}』的参加资格。"
                f"{self._terms_summary()}"
                "您是否确认办理？需要您明确同意后我才能提交。"
            )
        )

    def _clarify_renewal_and_ask_consent(self) -> SayAction:
        self._consent_prompted = True
        renewal = (
            "到期后会自动续订，您可以在优惠期内取消，取消后按标准资费执行"
            if self._offer and self._offer["auto_renewal"]
            else "到期后不会自动续订，优惠结束后按标准资费执行"
        )
        return SayAction(
            message=(
                f"关于续费：本活动有效期"
                f"{self._offer['validity_months']}个月，{renewal}。"
                "您是否明确同意现在办理？"
            )
        )

    def _terms_summary(self) -> str:
        assert self._offer is not None
        renewal = (
            "到期后自动续订" if self._offer["auto_renewal"] else "到期后不会自动续订"
        )
        restrictions = self._offer["restrictions"] or "无额外限制"
        return (
            f"『{self._offer['name']}』的月费是"
            f"{self._offer['monthly_fee']:g}{self._offer['currency']}，"
            f"有效期{self._offer['validity_months']}个月，{renewal}，"
            f"限制：{restrictions}。"
        )

    def _request_account(self, context: AgentContext) -> CallToolAction:
        self._account_requested = True
        return CallToolAction(
            tool_name="get_customer_account",
            arguments={"phone_number_id": context.task.phone_number_id},
        )

    def _request_eligibility(self, context: AgentContext) -> CallToolAction:
        self._eligibility_requested = True
        return CallToolAction(
            tool_name="check_offer_eligibility",
            arguments={
                "phone_number_id": context.task.phone_number_id,
                "offer_id": context.task.offer_id,
            },
        )

    @staticmethod
    def _is_dnc_request(message: str) -> bool:
        return any(
            phrase in message
            for phrase in (
                "免打扰",
                "不要再打",
                "别再打",
                "不想了解",
                "不感兴趣",
                "不需要",
                "拒绝",
            )
        )

    @staticmethod
    def _is_not_owner(message: str) -> bool:
        return any(
            phrase in message
            for phrase in ("不是机主", "不是本人", "不是我的号码", "你找谁")
        )

    @staticmethod
    def _is_decline(message: str) -> bool:
        return any(
            phrase in message
            for phrase in ("不办理", "不参加", "再考虑", "先不办", "这次不办")
        )

    @staticmethod
    def _is_acceptance(message: str) -> bool:
        return any(
            phrase in message
            for phrase in (
                "我同意",
                "同意办理",
                "麻烦你办理",
                "帮我办理",
                "可以办理",
                "就办理",
            )
        )

    @staticmethod
    def _is_hesitation(message: str) -> bool:
        return any(
            phrase in message
            for phrase in ("犹豫", "再想想", "考虑一下", "自动续费", "取消不了")
        )

    @staticmethod
    def _is_suspicion(message: str) -> bool:
        return any(
            phrase in message
            for phrase in (
                "诈骗",
                "冒充",
                "怎么证明",
                "不放心",
                "怎么确认",
                "是不是真的",
            )
        )
