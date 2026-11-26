"""Group A baseline prompt: no flowchart, no workflow, no tools.

The prompt is a fixed, versioned artifact. ``PROMPT_VERSION`` is recorded in every
trace so a stored run can be attributed to the exact instruction set that produced
it. Nothing in this module retrieves a workflow, a flowchart or any training
transcript.
"""

from __future__ import annotations

import hashlib

from cm_outbound_bench.decisions.schema import AgentRequest, PublicDecisionTask

PROMPT_VERSION = "baseline-a-v1"

#: Output contract. This is a response schema, not a list of authorized business
#: actions: the agent still decides the action itself.
OUTPUT_CONTRACT = """{
  "customer_response": "接下来要对客户说的话（字符串，必填）",
  "next_action": "下一步拟执行的业务动作（字符串，必填）",
  "required_information": ["仍需向客户确认或查询的信息（字符串数组，可为空）"],
  "tool_intent": null,
  "uncertainty": null,
  "evidence_summary": "基于通话记录事实的简短证据摘要（字符串，必填）"
}"""

SYSTEM_PROMPT = (
    "你是中国移动外呼场景中的客服代表，需要在一个决策点上独立判断下一步业务动作。\n"
    "\n"
    "输入说明：\n"
    "- 给你的是同一通真实外呼通话的**历史记录**，从通话开始到客户最近一句话为止。\n"
    "- 记录之后发生的事情（包括历史客服当时实际怎么回复）不会提供给你。\n"
    "- 你不需要、也不能续写客户的话；客户后续发言不在你的控制范围内。\n"
    "\n"
    "行为约束：\n"
    "1. 只依据给定的通话记录作答，不要编造通话中没有出现的政策、资费、活动、"
    "账户状态、办理结果或客户信息。\n"
    "2. 如果完成任务还缺少必要信息，把它写进 required_information，不要猜测具体值。\n"
    "3. 当前没有任何可调用的业务工具或接口，因此 tool_intent 必须为 null。\n"
    "4. 不要输出隐藏的推理过程；evidence_summary 只写简短的事实摘要。\n"
    "5. 用简体中文作答。\n"
    "\n"
    "输出要求：\n"
    "- 只输出一个 JSON 对象，不要输出 Markdown 代码块，不要输出 JSON 以外的任何文字。\n"
    "- 字段必须与下面的结构一致：\n"
    + OUTPUT_CONTRACT
)

_ROLE_LABELS = {
    "customer": "客户",
    "historical_agent": "客服",
}


def render_transcript(task: PublicDecisionTask) -> str:
    """Render the visible prefix as a numbered, role-labelled transcript."""
    lines = ["【通话记录】"]
    for index, turn in enumerate(task.visible_context, start=1):
        label = _ROLE_LABELS[turn.role]
        lines.append(f"{index}. {label}：{turn.content}")
    return "\n".join(lines)


def build_user_prompt(task: PublicDecisionTask) -> str:
    """Build the single user message for one decision point."""
    return "\n\n".join(
        [
            render_transcript(task),
            "【任务】",
            task.agent_task.instruction,
            "【输出】\n只输出符合约定的 JSON 对象。",
        ]
    )


def build_agent_request(task: PublicDecisionTask) -> AgentRequest:
    """Build the complete, independent input for one agent session."""
    return AgentRequest(
        task_id=task.task_id,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(task),
    )


def prompt_sha256(request: AgentRequest) -> str:
    """Hash the exact prompt pair so a trace identifies its real input."""
    payload = (request.system_prompt + "\n\u0000\n" + request.user_prompt).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prompt_bytes(request: AgentRequest) -> int:
    """Return the size of the exact prompt payload in bytes."""
    return len(request.system_prompt.encode("utf-8")) + len(
        request.user_prompt.encode("utf-8")
    )


__all__ = [
    "OUTPUT_CONTRACT",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "build_agent_request",
    "build_user_prompt",
    "prompt_bytes",
    "prompt_sha256",
    "render_transcript",
]
