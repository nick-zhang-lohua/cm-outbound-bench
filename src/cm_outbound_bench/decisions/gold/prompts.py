"""Versioned DeepSeek V4 Pro prompts for annotation, adjudication and scoring.

Every prompt lives in a plain Markdown file under the repository ``prompts/``
directory, so wording changes are reviewable in version control. Each prompt is
identified by ``<name>_<version>``; the exact file content is hashed and the hash
is recorded in every API record, so a rerun can prove which wording produced a
label.

The prompt files never contain conversation text: the task prefix is rendered at
call time from the agent-visible payload only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cm_outbound_bench.decisions.schema import PublicDecisionTask

PROMPTS_DIR_NAME = "prompts"

ANNOTATION_PASS_A = "decision_annotation_pass_a"
ANNOTATION_PASS_B = "decision_annotation_pass_b"
ANNOTATION_ADJUDICATION = "decision_annotation_adjudication"
SCORING_PASS_A = "group_a_output_scoring_pass_a"
SCORING_PASS_B = "group_a_output_scoring_pass_b"
SCORING_ADJUDICATION = "group_a_output_adjudication"

KNOWN_PROMPT_NAMES: frozenset[str] = frozenset(
    {
        ANNOTATION_PASS_A,
        ANNOTATION_PASS_B,
        ANNOTATION_ADJUDICATION,
        SCORING_PASS_A,
        SCORING_PASS_B,
        SCORING_ADJUDICATION,
    }
)

#: The fixed annotation JSON contract both passes must fill. The prompts embed a
#: human-readable copy; this constant is the authoritative field list used by the
#: parser's forgiveness checks.
ANNOTATION_CONTRACT_FIELDS: tuple[str, ...] = (
    "task_answerability",
    "customer_intent",
    "conversation_state",
    "primary_expected_decision",
    "acceptable_alternative_decisions",
    "required_information",
    "required_steps",
    "optional_steps",
    "prohibited_steps",
    "branch_conditions",
    "expected_branch",
    "external_policy_dependency",
    "policy_verified",
    "unsupported_claims_to_avoid",
    "evidence_refs",
    "assumptions_not_allowed",
    "confidence",
    "scoreable",
    "unscorable_reason",
)

SCORING_CONTRACT_FIELDS: tuple[str, ...] = (
    "outcome",
    "rationale",
    "critical_errors",
    "confidence",
)

_ROLE_LABELS = {
    "customer": "客户",
    "historical_agent": "客服",
}


class PromptError(RuntimeError):
    """Raised when a required prompt file is missing or malformed."""


def prompts_dir(project_root: Path | None = None) -> Path:
    if project_root is None:
        from cm_outbound_bench.decisions.paths import project_root as _root

        project_root = _root()
    return project_root / PROMPTS_DIR_NAME


def load_prompt(
    name: str,
    version: str,
    *,
    project_root: Path | None = None,
) -> str:
    """Load one frozen prompt file. Version is part of the file name."""
    if name not in KNOWN_PROMPT_NAMES:
        raise PromptError(f"unknown prompt name {name!r}")
    safe_version = version.replace("..", "").replace("/", "").replace("\\", "")
    target = prompts_dir(project_root) / f"{name}_{safe_version}.md"
    if not target.is_file():
        raise PromptError(f"prompt file not found: {target}")
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PromptError(f"cannot read prompt {target}: {error}") from error
    if not content.strip():
        raise PromptError(f"prompt file is empty: {target}")
    return content


def prompt_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_task_prefix(task: PublicDecisionTask) -> str:
    """Render the agent-visible prefix as a numbered, role-labelled transcript.

    Identical rendering to the Group A agent prompt, so the annotator sees exactly
    the same information the evaluated agent saw.
    """
    lines = ["【通话记录】"]
    for index, turn in enumerate(task.visible_context, start=1):
        label = _ROLE_LABELS[turn.role]
        lines.append(f"{index}. {label}：{turn.content}")
    return "\n".join(lines)


def build_annotation_messages(
    task: PublicDecisionTask,
    *,
    prompt_name: str,
    prompt_version: str,
    extra: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> tuple[str, str]:
    """Return ``(system, user)`` messages for one annotation API call.

    The system message is the frozen prompt; the user message contains the rendered
    agent-visible prefix plus any stage-specific additions (adjudication inputs).
    Nothing else is ever included.
    """
    prompt = load_prompt(prompt_name, prompt_version, project_root=project_root)
    parts = [render_task_prefix(task), "【任务】", task.agent_task.instruction]
    if extra:
        for key, value in extra.items():
            parts.append(f"【{key}】")
            parts.append(value)
    return prompt, "\n\n".join(parts)


__all__ = [
    "ANNOTATION_ADJUDICATION",
    "ANNOTATION_CONTRACT_FIELDS",
    "ANNOTATION_PASS_A",
    "ANNOTATION_PASS_B",
    "KNOWN_PROMPT_NAMES",
    "PROMPTS_DIR_NAME",
    "SCORING_ADJUDICATION",
    "SCORING_CONTRACT_FIELDS",
    "SCORING_PASS_A",
    "SCORING_PASS_B",
    "PromptError",
    "build_annotation_messages",
    "load_prompt",
    "prompt_sha256",
    "prompts_dir",
    "render_task_prefix",
]
