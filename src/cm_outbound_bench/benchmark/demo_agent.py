"""Deterministic baseline agent for the multi-domain benchmark tasks.

This is a reference baseline, not an evaluation target. It follows a fixed
business workflow per domain using only agent-visible context: the objective,
the target line, the target offer (when present) and tool results. It never sees
simulator-private or evaluator-private state.

``DemoAgent`` remains the legacy marketing baseline; this agent covers the
declarative D01–D06 tasks.
"""

from __future__ import annotations

import re
from typing import Any

from cm_outbound_bench.agent import (
    AgentAction,
    AgentContext,
    CallToolAction,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.domain.tools import ToolResult

_PLAN_ID = re.compile(r"PLAN_[A-Z]+")
_AMOUNT_CNY = re.compile(r"(\d+)\s*(?:CNY|元|块钱|块)")
_TOKEN = re.compile(r"TEST_IDENTITY_[A-Za-z0-9_]+")

#: The documented operating contract of the deterministic reference baseline.
#: It is hashed into ``prompt_hash`` so a manifest records which agent contract
#: produced the episode instead of a free-floating version label.
SCRIPTED_AGENT_PROMPT = (
    "Deterministic reference baseline agent for the CM outbound benchmark.\n"
    "It follows a fixed per-domain business workflow using only agent-visible "
    "context: the task objective, the target synthetic line, the target offer "
    "and tool results.\n"
    "It never reads simulator-private or evaluator-private state, never invents "
    "a tool result, and finishes every episode with an explicit finish action."
)

_ACCEPTANCE = ("同意", "可以办", "麻烦你办", "帮我办", "办吧", "提交")
_OPT_OUT = (
    "免打扰",
    "不要再打",
    "别再打",
    "不感兴趣",
    "不需要",
    "不想了解",
    "拒绝",
)
_IDENTITY_PROMPT = (
    "为了核对机主身份，请提供您的测试身份令牌（TEST_IDENTITY_ 开头）。"
)
_MAX_IDENTITY_REQUESTS = 2

#: Customer-facing wording for the tool's blocking-condition codes. The baseline
#: quotes both the plain-language reason and the tool code so the customer hears
#: the true reason and an auditor can match it to the tool result.
_BLOCKER_PHRASES = {
    "real_name_not_verified": "未实名（real_name_not_verified）",
    "arrears_outstanding": "欠费未缴清（arrears_outstanding）",
    "active_commitment": "合约未到期（active_commitment）",
}


def _blocker_phrases(codes: list[str]) -> str:
    if not codes:
        return "不满足过户条件"
    return "、".join(_BLOCKER_PHRASES.get(code, code) for code in codes)

# Tool error codes that do not mean the workflow is unrecoverable.
_TOLERATED_ERRORS = frozenset({"consent_required"})


class BenchmarkDemoAgent:
    """A small deterministic workflow agent for the D01–D06 baseline."""

    def __init__(self) -> None:
        self._verified = False
        self._identity_requests = 0
        self._asked_consent = False
        self._accepted = False
        self._absorbed_tools = 0
        self._handled_messages = 0
        self._finished = False
        self._failure = False
        self._error = ""
        self._pending_tool: tuple[str, dict[str, Any]] | None = None
        self._dnc_requested = False
        self._dnc_registered = False

        self._offer: dict[str, Any] | None = None
        self._plan_details: dict[str, dict[str, Any]] = {}
        self._plans_listed = False
        self._comparison: dict[str, Any] | None = None
        self._account: dict[str, Any] | None = None
        self._billing: dict[str, Any] | None = None
        self._charges: list[dict[str, Any]] | None = None
        self._subscription: dict[str, Any] | None = None
        self._eligibility: bool | None = None
        self._eligibility_reasons: list[str] = []
        self._identity: dict[str, Any] | None = None
        self._conditions: dict[str, Any] | None = None
        self._enrollment_id: str | None = None
        self._recharge_id: str | None = None
        self._change_request_id: str | None = None
        self._sms_sent = False
        self._notification_sent = False
        self._notification_id: str | None = None
        self._notification_status: str | None = None
        self._terms_stated = False

    # -- agent protocol ----------------------------------------------------

    def act(self, context: AgentContext) -> AgentAction:
        self._absorb(context)
        if self._finished:
            return FinishAction(reason="conversation closed")
        if self._failure:
            return FinishAction(reason=f"unrecoverable tool error: {self._error}")

        message = self._take_message(context)
        if message is not None:
            self._handle_message(message)
        return self._continue(context)

    # -- message handling --------------------------------------------------

    def _take_message(self, context: AgentContext) -> str | None:
        if not context.messages or context.messages[-1].role != "user":
            return None
        if len(context.messages) <= self._handled_messages:
            return None
        self._handled_messages = len(context.messages)
        return context.messages[-1].content

    def _handle_message(self, message: str) -> None:
        if not self._dnc_requested and any(term in message for term in _OPT_OUT):
            self._dnc_requested = True
            self._pending_tool = ("register_do_not_call", {})
            return
        token = _TOKEN.search(message)
        if token is not None and not self._verified:
            self._pending_tool = (
                "verify_customer_identity",
                {"identity_token": token.group(0)},
            )
            return
        if self._asked_consent and any(term in message for term in _ACCEPTANCE):
            self._accepted = True

    # -- workflow dispatch -------------------------------------------------

    def _continue(self, context: AgentContext) -> AgentAction:
        if self._pending_tool is not None:
            name, extra = self._pending_tool
            self._pending_tool = None
            arguments = {"phone_number_id": context.task.phone_number_id}
            arguments.update(extra)
            return CallToolAction(tool_name=name, arguments=arguments)

        if self._dnc_registered:
            return self._finish(
                "好的，已为您登记免打扰，后续不会再进行营销联系。感谢接听，再见。"
            )

        handlers = {
            "d01": self._workflow_d01,
            "d02": self._workflow_offer,
            "d03": self._workflow_d03,
            "d04": self._workflow_d04,
            "d05": self._workflow_d05,
            "d06": self._workflow_d06,
        }
        handler = handlers.get(context.task.domain)
        if handler is None:
            self._finished = True
            return FinishAction(reason="unsupported domain")
        return handler(context)

    # -- shared steps ------------------------------------------------------

    def _identity_step(self) -> AgentAction | None:
        if self._verified:
            return None
        if self._identity_requests >= _MAX_IDENTITY_REQUESTS:
            return None
        self._identity_requests += 1
        return SayAction(message=_IDENTITY_PROMPT)

    def _consent_step(self, text: str) -> AgentAction:
        self._asked_consent = True
        return SayAction(message=text)

    def _finish(self, message: str) -> AgentAction:
        self._finished = True
        return SayAction(message=message)

    # -- D01 ---------------------------------------------------------------

    def _workflow_d01(self, context: AgentContext) -> AgentAction:
        step = self._identity_step()
        if step is not None:
            return step
        if not self._verified:
            return SayAction(message="好的，那我们下次再为您查询套餐内容。")
        if self._account is None:
            return CallToolAction(
                tool_name="get_customer_account",
                arguments={"phone_number_id": context.task.phone_number_id},
            )
        if not self._plans_listed:
            return CallToolAction(tool_name="list_plans", arguments={})

        requested = _PLAN_ID.findall(context.task.objective)
        if len(requested) >= 2:
            if self._comparison is None:
                return CallToolAction(
                    tool_name="compare_plans",
                    arguments={
                        "plan_id_a": requested[0],
                        "plan_id_b": requested[1],
                    },
                )
            return self._finish(self._describe_comparison())

        target = requested[0] if requested else self._account["current_plan"]["plan_id"]
        if target not in self._plan_details:
            return CallToolAction(
                tool_name="get_plan_details", arguments={"plan_id": target}
            )
        return self._finish(self._describe_plan(target))

    def _describe_plan(self, plan_id: str) -> str:
        plan = self._plan_details[plan_id]
        allowance = plan.get("allowance") or {}
        return (
            f"『{plan['name']}』月费 {plan['monthly_fee']:g} {plan['currency']}，"
            f"包含 {allowance.get('data_mb', 0)}MB 流量、"
            f"{allowance.get('voice_minutes', 0)} 分钟语音、"
            f"{allowance.get('sms_count', 0)} 条短信。感谢接听，再见。"
        )

    def _describe_comparison(self) -> str:
        assert self._comparison is not None
        first, second = self._comparison["plans"]
        first_allowance = first.get("allowance") or {}
        second_allowance = second.get("allowance") or {}
        return (
            f"『{first['name']}』月费 {first['monthly_fee']:g} 元，"
            f"流量 {first_allowance.get('data_mb', 0)}MB、语音 "
            f"{first_allowance.get('voice_minutes', 0)} 分钟；"
            f"『{second['name']}』月费 {second['monthly_fee']:g} 元，"
            f"流量 {second_allowance.get('data_mb', 0)}MB、语音 "
            f"{second_allowance.get('voice_minutes', 0)} 分钟。"
            f"两者月费相差 {abs(self._comparison['monthly_fee_delta']):g} 元。"
            "如需变更套餐请告知客服。感谢接听，再见。"
        )

    # -- D02 / D06 offer flow ---------------------------------------------

    def _workflow_offer(self, context: AgentContext) -> AgentAction:
        offer_id = context.task.offer_id
        if offer_id is None:
            self._finished = True
            return FinishAction(reason="offer task without an offer")
        if self._offer is None:
            return CallToolAction(
                tool_name="get_offer_details", arguments={"offer_id": offer_id}
            )
        if not self._terms_stated:
            self._terms_stated = True
            return SayAction(message=self._terms_message())
        step = self._identity_step()
        if step is not None:
            return step
        if not self._verified:
            return self._finish("抱歉，未能完成身份核验，本次先不办理。感谢接听，再见。")
        if self._eligibility is None:
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "offer_id": offer_id,
                },
            )
        if self._eligibility is False:
            reasons = "、".join(self._eligibility_reasons) or "不满足活动条件"
            return self._finish(
                f"很抱歉，您的号码暂不符合该活动的参加条件（{reasons}），"
                "这次无法为您办理。感谢接听，再见。"
            )
        if not self._asked_consent:
            return self._consent_step(
                f"您的号码符合『{self._offer['name']}』的参加条件。"
                f"{self._terms_message()}"
                "您是否同意办理这个活动？需要您明确同意后我才能提交。"
            )
        if self._enrollment_id is None:
            if not self._accepted:
                return self._finish("好的，尊重您的选择，这次不办理。感谢接听，再见。")
            return CallToolAction(
                tool_name="enroll_offer",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "offer_id": offer_id,
                },
            )
        if context.task.domain == "d06":
            if not self._notification_sent:
                return CallToolAction(
                    tool_name="send_notification",
                    arguments={
                        "phone_number_id": context.task.phone_number_id,
                        "kind": "CONFIRMATION",
                        "related_ref": self._enrollment_id,
                    },
                )
            if self._notification_status is None:
                return CallToolAction(
                    tool_name="get_notification_status",
                    arguments={"notification_id": self._notification_id or ""},
                )
            return self._finish(
                f"已为您办理『{self._offer['name']}』，确认消息状态为 "
                f"{self._notification_status}，已模拟发送。感谢接听，再见。"
            )
        if not self._sms_sent:
            return CallToolAction(
                tool_name="send_confirmation_sms",
                arguments={"enrollment_id": self._enrollment_id},
            )
        return self._finish(
            f"已为您成功办理『{self._offer['name']}』，确认短信已模拟发送。"
            "感谢接听，再见。"
        )

    def _terms_message(self) -> str:
        assert self._offer is not None
        renewal = (
            "到期后自动续订" if self._offer["auto_renewal"] else "到期后不自动续订"
        )
        restrictions = str(self._offer["restrictions"]).rstrip("。")
        return (
            f"『{self._offer['name']}』的月费是 {self._offer['monthly_fee']:g} "
            f"{self._offer['currency']}，有效期 {self._offer['validity_months']} 个月，"
            f"{renewal}，限制：{restrictions}。"
        )

    # -- D03 ---------------------------------------------------------------

    def _workflow_d03(self, context: AgentContext) -> AgentAction:
        recharge_amount = self._recharge_amount(context.task.objective)
        step = self._identity_step()
        if step is not None:
            return step
        if not self._verified:
            return self._finish("抱歉，未能完成身份核验，无法查询账单。感谢接听，再见。")
        if self._billing is None:
            return CallToolAction(
                tool_name="get_billing_summary",
                arguments={"phone_number_id": context.task.phone_number_id},
            )
        if recharge_amount is None:
            if self._charges is None:
                return CallToolAction(
                    tool_name="list_charges",
                    arguments={"phone_number_id": context.task.phone_number_id},
                )
            return self._finish(self._describe_bill())
        if not self._asked_consent:
            return self._consent_step(
                f"您的当前余额是 {self._billing['balance_cents'] / 100:.2f} 元，"
                f"欠费 {self._billing['arrears_cents'] / 100:.2f} 元。"
                f"您是否同意充值 {recharge_amount / 100:.0f} 元？"
                "需要您明确同意后我才能提交。"
            )
        if self._recharge_id is None:
            if not self._accepted:
                return self._finish("好的，这次不充值。感谢接听，再见。")
            return CallToolAction(
                tool_name="recharge_account",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "amount_cents": recharge_amount,
                },
            )
        if context.task.domain == "d06" and not self._notification_sent:
            return CallToolAction(
                tool_name="send_notification",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "kind": "CONFIRMATION",
                    "related_ref": self._recharge_id,
                },
            )
        if context.task.domain == "d06":
            if self._notification_status is None:
                return CallToolAction(
                    tool_name="get_notification_status",
                    arguments={"notification_id": self._notification_id or ""},
                )
            return self._finish(
                f"已为您模拟充值 {recharge_amount / 100:.0f} 元，"
                f"当前余额 {self._billing['balance_cents'] / 100:.2f} 元，"
                f"确认通知已模拟发送（状态 {self._notification_status}）。"
                "感谢接听，再见。"
            )
        return self._finish(
            f"已为您模拟充值 {recharge_amount / 100:.0f} 元，"
            f"当前余额 {self._billing['balance_cents'] / 100:.2f} 元。"
            "感谢接听，再见。"
        )

    def _describe_bill(self) -> str:
        assert self._billing is not None and self._charges is not None
        items = "；".join(
            f"{charge['description']} {charge['amount_cents'] / 100:.2f} 元"
            for charge in self._charges
        )
        return (
            f"您当前余额 {self._billing['balance_cents'] / 100:.2f} 元，"
            f"欠费 {self._billing['arrears_cents'] / 100:.2f} 元。"
            f"本期账单项：{items}。如需争议处理会有单独的流程。"
            "感谢接听，再见。"
        )

    @staticmethod
    def _recharge_amount(objective: str) -> int | None:
        """Return the top-up amount in cents when the objective names one."""
        match = _AMOUNT_CNY.search(objective)
        if match is None:
            return None
        return int(match.group(1)) * 100

    # -- D04 ---------------------------------------------------------------

    def _workflow_d04(self, context: AgentContext) -> AgentAction:
        target_plan = self._target_plan(context)
        step = self._identity_step()
        if step is not None:
            return step
        if not self._verified:
            return self._finish("抱歉，未能完成身份核验，无法办理套餐变更。感谢接听，再见。")
        if self._subscription is None:
            return CallToolAction(
                tool_name="get_subscription_status",
                arguments={"phone_number_id": context.task.phone_number_id},
            )
        if self._eligibility is None:
            return CallToolAction(
                tool_name="check_plan_change_eligibility",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "to_plan_id": target_plan,
                },
            )
        if self._eligibility is False:
            reasons = "、".join(self._eligibility_reasons) or "不满足变更条件"
            return self._finish(
                f"很抱歉，当前无法为您办理套餐变更（{reasons}）。"
                "合约到期后再办理会更合适。感谢接听，再见。"
            )
        if not self._asked_consent:
            return self._consent_step(
                f"您的号码可以变更到 {target_plan}。"
                "您是否同意办理套餐变更？需要您明确同意后我才能提交。"
            )
        if self._change_request_id is None:
            if not self._accepted:
                return self._finish("好的，这次不变更套餐。感谢接听，再见。")
            return CallToolAction(
                tool_name="submit_plan_change",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "to_plan_id": target_plan,
                },
            )
        return self._finish("套餐变更已提交，生效日期为 2026-12-01。感谢接听，再见。")

    def _target_plan(self, context: AgentContext) -> str:
        candidates = _PLAN_ID.findall(context.task.objective)
        current = (
            self._subscription["plan_id"] if self._subscription is not None else None
        )
        for plan_id in candidates:
            if plan_id != current:
                return plan_id
        return candidates[-1] if candidates else "PLAN_L"

    # -- D05 ---------------------------------------------------------------

    def _workflow_d05(self, context: AgentContext) -> AgentAction:
        step = self._identity_step()
        if step is not None:
            return step
        if not self._verified:
            return self._finish("抱歉，未能完成身份核验，无法查询实名信息。感谢接听，再见。")
        if self._identity is None:
            return CallToolAction(
                tool_name="get_identity_status",
                arguments={"phone_number_id": context.task.phone_number_id},
            )
        if self._conditions is None:
            return CallToolAction(
                tool_name="check_ownership_transfer_conditions",
                arguments={"phone_number_id": context.task.phone_number_id},
            )
        status = self._identity["real_name_status"]
        if self._conditions["transferable"]:
            return self._finish(
                f"您的实名状态是 {status}，目前符合过户条件。"
                "过户需要本人携带证件到营业厅办理，本次通话无法直接完成。"
                "感谢接听，再见。"
            )
        blockers = _blocker_phrases(self._conditions["blocking_conditions"])
        return self._finish(
            f"您的实名状态是 {status}，目前暂不符合过户条件：{blockers}。"
            "请先处理这些条件，再到营业厅办理过户。感谢接听，再见。"
        )

    # -- D06 ---------------------------------------------------------------

    def _workflow_d06(self, context: AgentContext) -> AgentAction:
        if context.task.offer_id is not None:
            return self._workflow_offer(context)
        return self._workflow_d03(context)

    # -- tool result absorption -------------------------------------------

    def _absorb(self, context: AgentContext) -> None:
        for entry in context.tool_results[self._absorbed_tools :]:
            name = entry.tool_name
            result: ToolResult = entry.result
            if not result.ok:
                code = (
                    result.error.code if result.error is not None else "unknown_error"
                )
                if code not in _TOLERATED_ERRORS:
                    self._failure = True
                    self._error = code
                continue
            data = result.data
            if name == "verify_customer_identity":
                self._verified = bool(data.get("verified"))
            elif name == "get_offer_details":
                self._offer = data
            elif name == "check_offer_eligibility":
                self._eligibility = bool(data.get("eligible"))
                self._eligibility_reasons = list(data.get("reasons") or [])
            elif name == "enroll_offer":
                self._enrollment_id = str(data.get("enrollment_id"))
            elif name == "send_confirmation_sms":
                self._sms_sent = True
            elif name == "send_notification":
                self._notification_sent = True
                self._notification_id = str(data.get("notification_id"))
            elif name == "get_notification_status":
                self._notification_status = str(data.get("status"))
            elif name == "get_customer_account":
                self._account = data
            elif name == "list_plans":
                self._plans_listed = True
            elif name == "get_plan_details":
                self._plan_details[str(data.get("plan_id"))] = data
            elif name == "compare_plans":
                self._comparison = data
            elif name == "get_billing_summary":
                self._billing = data
            elif name == "list_charges":
                self._charges = list(data.get("charges") or [])
            elif name == "recharge_account":
                self._recharge_id = str(data.get("recharge_id"))
                if self._billing is not None:
                    self._billing["balance_cents"] = data.get(
                        "balance_cents", self._billing["balance_cents"]
                    )
            elif name == "get_subscription_status":
                self._subscription = data
            elif name == "check_plan_change_eligibility":
                self._eligibility = bool(data.get("eligible"))
                self._eligibility_reasons = list(data.get("reasons") or [])
            elif name == "submit_plan_change":
                self._change_request_id = str(data.get("request_id"))
            elif name == "get_identity_status":
                self._identity = data
            elif name == "check_ownership_transfer_conditions":
                self._conditions = data
            elif name == "register_do_not_call":
                self._dnc_registered = True
        self._absorbed_tools = len(context.tool_results)
