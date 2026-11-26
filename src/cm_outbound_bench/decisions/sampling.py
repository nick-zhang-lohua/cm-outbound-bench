"""Deterministic, reproducible annotation sampling plans.

The corpus has no verified business-domain label, so a plan can only be
stratified on **observable** task characteristics: how much context is visible, how
the conversation is shaped, where the decision point sits, and what data-quality
features the prefix carries. This module therefore never claims domain
stratification. Every plan records that limitation explicitly.

Rules implemented:

* one task per conversation unit, so tasks from the same phone conversation never
  inflate the effective sample size;
* a fixed integer seed;
* proportional allocation across observable strata with a largest-remainder
  rounding, so the sample tracks the pool instead of over-representing one shape;
* a deterministic order inside each stratum derived from ``sha256(seed:task_id)``;
* a selection hash, so a later run can prove it reproduced the same sample.

Nothing here reads conversation text beyond the agent-visible prefix, and the plan
manifest stores counts, ids and observable features — never transcript content.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.annotation import ANNOTATION_GUIDELINE_VERSION
from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask
from cm_outbound_bench.decisions.store import TaskStore
from cm_outbound_bench.domain.data_model import DomainModel

SAMPLING_VERSION = "1.0"

PURPOSE_PILOT = "train_r_annotation_pilot"
PURPOSE_TRAIN_R_FEEDBACK = "train_r_feedback_sample"
PURPOSE_DEV_EVALUATION = "dev_baseline_evaluation_sample"

PURPOSE_VALUES: tuple[str, ...] = (
    PURPOSE_PILOT,
    PURPOSE_TRAIN_R_FEEDBACK,
    PURPOSE_DEV_EVALUATION,
)

#: Recommended target sizes from the milestone plan.
RECOMMENDED_TARGETS: dict[str, int] = {
    PURPOSE_PILOT: 100,
    PURPOSE_TRAIN_R_FEEDBACK: 300,
    PURPOSE_DEV_EVALUATION: 400,
}

#: Fixed seed for every plan. Recorded in the manifest and required to reproduce.
DEFAULT_SAMPLING_SEED = 20261126

#: Bucket edges for the observable strata.
VISIBLE_TURN_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (2, 4, "2-4"),
    (5, 8, "5-8"),
    (9, 14, "9-14"),
    (15, 10**6, "15+"),
)
CUSTOMER_TURN_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "1"),
    (2, 2, "2"),
    (3, 10**6, "3+"),
)
AGENT_TURN_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 1, "0-1"),
    (2, 3, "2-3"),
    (4, 10**6, "4+"),
)
POSITION_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 0.25, "early"),
    (0.25, 0.5, "mid_early"),
    (0.5, 0.75, "mid_late"),
    (0.75, 1.01, "late"),
)


class SamplingError(RuntimeError):
    """Raised when a plan cannot be produced or verified."""


class SamplingSpec(DomainModel):
    """One sampling request."""

    purpose: str
    split: str
    target_size: int = Field(ge=1)
    seed: int = DEFAULT_SAMPLING_SEED
    one_task_per_conversation: bool = True
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    exclude_conversations: list[str] = Field(
        default_factory=list,
        description=(
            "Conversation units to keep out of the sample, so a later sample is "
            "independent of an earlier one (for example a pilot)."
        ),
    )


class SampledTask(DomainModel):
    """One selected task with its observable features. No conversation text."""

    task_id: str
    conversation_id: str
    split: str
    stratum: str
    visible_turn_count: int
    customer_turn_count: int
    agent_turn_count: int
    cutoff_position_ratio: float
    cutoff_start_ms: int
    has_truncated_turn: bool
    has_masked_entity: bool
    prefix_char_count: int
    data_quality_status: str


class SamplingPlan(DomainModel):
    """A reproducible record of one annotation sample."""

    sampling_version: str = SAMPLING_VERSION
    plan_id: str = ""
    purpose: str = ""
    split: str = ""
    seed: int = 0
    target_size: int = 0
    pool_tasks: int = 0
    pool_conversations: int = 0
    selected_size: int = 0
    selected_conversations: int = 0
    one_task_per_conversation: bool = True
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    excluded_conversations: int = 0
    excluded_conversation_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit exclusion list, recorded so the plan stays reproducible "
            "rather than depending on a previous file."
        ),
    )
    strata: dict[str, int] = Field(default_factory=dict)
    pool_strata: dict[str, int] = Field(default_factory=dict)
    selected: list[SampledTask] = Field(default_factory=list)
    selection_sha256: str = ""
    created_at: str = ""
    stratification: dict[str, object] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def task_ids(self) -> list[str]:
        return [item.task_id for item in self.selected]

    @property
    def conversation_ids(self) -> list[str]:
        return [item.conversation_id for item in self.selected]


def _bucket(
    value: float, edges: Sequence[tuple[float, float, str]]
) -> str:
    for low, high, label in edges:
        if low <= value < high:
            return label
    return edges[-1][2]


def _observable_features(
    task: PublicDecisionTask, reference: PrivateTaskReference
) -> SampledTask:
    """Compute only features that are visible or structural. No invented labels."""
    customer_turns = sum(1 for turn in task.visible_context if turn.role == "customer")
    agent_turns = len(task.visible_context) - customer_turns
    hidden = reference.hidden_speech_turn_count
    position = reference.cutoff_record_index / max(
        1, reference.cutoff_record_index + hidden
    )
    visible_text = "\n".join(turn.content for turn in task.visible_context)
    stratum = "|".join(
        [
            f"visible={_bucket(len(task.visible_context), VISIBLE_TURN_BUCKETS)}",
            f"customer={_bucket(customer_turns, CUSTOMER_TURN_BUCKETS)}",
            f"agent={_bucket(agent_turns, AGENT_TURN_BUCKETS)}",
            f"position={_bucket(position, POSITION_BUCKETS)}",
        ]
    )
    return SampledTask(
        task_id=task.task_id,
        conversation_id=reference.conversation_id,
        split=reference.split,
        stratum=stratum,
        visible_turn_count=len(task.visible_context),
        customer_turn_count=customer_turns,
        agent_turn_count=agent_turns,
        cutoff_position_ratio=round(position, 4),
        cutoff_start_ms=reference.cutoff_start_ms,
        has_truncated_turn=any(
            turn.content.endswith("+") for turn in task.visible_context
        ),
        has_masked_entity="<" in visible_text and ">" in visible_text,
        prefix_char_count=len(visible_text),
        data_quality_status=reference.data_quality_status,
    )


def _selection_order(task_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()


def _selection_digest(task_ids: Iterable[str]) -> str:
    payload = "\n".join(sorted(task_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pick_one_per_conversation(
    candidates: Sequence[SampledTask],
) -> list[SampledTask]:
    """Keep the richest task per conversation, deterministically.

    "Richest" means the longest visible prefix, which gives the annotator the most
    evidence; ties break on the opaque task id, so the choice never depends on
    dictionary ordering.
    """
    grouped: dict[str, list[SampledTask]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.conversation_id].append(candidate)
    picked: list[SampledTask] = []
    for conversation_id in sorted(grouped):
        members = grouped[conversation_id]
        members.sort(key=lambda item: (-item.visible_turn_count, item.task_id))
        picked.append(members[0])
    return picked


def _allocate(strata: dict[str, int], target: int) -> dict[str, int]:
    """Proportional allocation with largest-remainder rounding."""
    total = sum(strata.values())
    if total == 0 or target <= 0:
        return dict.fromkeys(strata, 0)
    quotas = {key: target * count / total for key, count in strata.items()}
    floors = {key: min(math.floor(value), strata[key]) for key, value in quotas.items()}
    remainder = target - sum(floors.values())
    if remainder > 0:
        order = sorted(
            (key for key in strata if floors[key] < strata[key]),
            key=lambda key: (-(quotas[key] - floors[key]), key),
        )
        for key in order:
            if remainder <= 0:
                break
            floors[key] += 1
            remainder -= 1
    return floors


def build_sampling_plan(
    store: TaskStore,
    spec: SamplingSpec,
) -> SamplingPlan:
    """Build a deterministic annotation sample for one split."""
    if spec.purpose not in PURPOSE_VALUES:
        raise SamplingError(f"unknown sampling purpose: {spec.purpose!r}")
    task_ids = store.task_ids_for_split(spec.split)
    if not task_ids:
        raise SamplingError(f"no tasks available in split {spec.split!r}")

    candidates = [
        _observable_features(
            store.load_public_task(task_id), store.require_reference(task_id)
        )
        for task_id in task_ids
    ]
    pool_tasks = len(candidates)
    pool_conversations = len({item.conversation_id for item in candidates})

    excluded = set(spec.exclude_conversations)
    if excluded:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.conversation_id not in excluded
        ]
        if not candidates:
            raise SamplingError(
                "every candidate conversation was excluded; nothing left to sample"
            )

    if spec.one_task_per_conversation:
        candidates = _pick_one_per_conversation(candidates)

    pool_strata = dict(sorted(Counter(item.stratum for item in candidates).items()))
    allocation = _allocate(pool_strata, spec.target_size)

    selected: list[SampledTask] = []
    for stratum, quota in allocation.items():
        if quota <= 0:
            continue
        members = [item for item in candidates if item.stratum == stratum]
        members.sort(key=lambda item: _selection_order(item.task_id, spec.seed))
        selected.extend(members[:quota])

    selected.sort(key=lambda item: (item.stratum, item.task_id))
    selected_size = len(selected)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan_id = "SAMPLE-" + _selection_digest(
        [item.task_id for item in selected]
    )[:16]
    plan = SamplingPlan(
        plan_id=plan_id,
        purpose=spec.purpose,
        split=spec.split,
        seed=spec.seed,
        target_size=spec.target_size,
        pool_tasks=pool_tasks,
        pool_conversations=pool_conversations,
        selected_size=selected_size,
        selected_conversations=len({item.conversation_id for item in selected}),
        one_task_per_conversation=spec.one_task_per_conversation,
        guideline_version=spec.guideline_version,
        excluded_conversations=len(excluded),
        excluded_conversation_ids=sorted(excluded),
        strata=dict(sorted(Counter(item.stratum for item in selected).items())),
        pool_strata=pool_strata,
        selected=selected,
        selection_sha256=_selection_digest(item.task_id for item in selected),
        created_at=now,
        stratification={
            "by_business_domain": False,
            "reason": (
                "The corpus carries no verified business-domain label, so this "
                "sample is stratified on observable task characteristics only. "
                "Domain stratification is neither claimed nor performed."
            ),
            "observable_dimensions": [
                "visible_turn_count",
                "customer_turn_count",
                "agent_turn_count",
                "cutoff_position_ratio",
                "has_truncated_turn",
                "has_masked_entity",
                "prefix_char_count",
            ],
        },
        notes=[
            "One task per conversation unit is selected, so the effective sample "
            "size equals the number of independent conversations.",
            "Selection inside a stratum is ordered by sha256(seed:task_id), so the "
            "sample is reproducible from the seed alone.",
        ],
    )
    if selected_size < spec.target_size:
        plan.notes.append(
            f"the eligible pool supplied {selected_size} task(s) from "
            f"{plan.selected_conversations} conversation(s), fewer than the "
            f"requested {spec.target_size}; the actual sample size is reported and "
            "no task was duplicated to reach the target."
        )
    return plan


def verify_sampling_plan(store: TaskStore, plan: SamplingPlan) -> bool:
    """Recompute a plan from its own specification and compare the selection."""
    spec = SamplingSpec(
        purpose=plan.purpose,
        split=plan.split,
        target_size=plan.target_size,
        seed=plan.seed,
        one_task_per_conversation=plan.one_task_per_conversation,
        guideline_version=plan.guideline_version,
        exclude_conversations=plan.excluded_conversation_ids,
    )
    rebuilt = build_sampling_plan(store, spec)
    return rebuilt.selection_sha256 == plan.selection_sha256


def save_sampling_plan(path: str | Path, plan: SamplingPlan) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_sampling_plan(path: str | Path) -> SamplingPlan:
    target = Path(path)
    if not target.is_file():
        raise SamplingError(f"sampling plan not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SamplingError(f"sampling plan is not valid JSON: {target}") from error
    return SamplingPlan.model_validate(payload)


def render_sampling_plan_markdown(plan: SamplingPlan) -> str:
    """Render a plan as markdown. Counts and ids only, no conversation text."""
    lines = [
        f"# Sampling plan {plan.plan_id}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Purpose | `{plan.purpose}` |",
        f"| Split | `{plan.split}` |",
        f"| Seed | {plan.seed} |",
        f"| Target size | {plan.target_size} |",
        f"| Pool tasks | {plan.pool_tasks} |",
        f"| Pool conversations | {plan.pool_conversations} |",
        f"| Selected tasks | {plan.selected_size} |",
        f"| Selected conversations | {plan.selected_conversations} |",
        f"| One task per conversation | {plan.one_task_per_conversation} |",
        f"| Guideline version | {plan.guideline_version} |",
        f"| Selection sha256 | `{plan.selection_sha256}` |",
        "",
        "## Strata (selected / pool)",
        "",
        "| Stratum | Selected | Pool |",
        "|---|---:|---:|",
    ]
    for stratum, count in plan.strata.items():
        lines.append(
            f"| `{stratum}` | {count} | {plan.pool_strata.get(stratum, 0)} |"
        )
    lines.append("")
    if plan.notes:
        lines.append("## Notes")
        lines.append("")
        for note in plan.notes:
            lines.append(f"- {note}")
        lines.append("")
    if plan.excluded_conversations:
        lines.append(
            f"Excluded {plan.excluded_conversations} conversation unit(s) that "
            "belong to another sample, so this sample is independent of it."
        )
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "AGENT_TURN_BUCKETS",
    "CUSTOMER_TURN_BUCKETS",
    "DEFAULT_SAMPLING_SEED",
    "POSITION_BUCKETS",
    "PURPOSE_DEV_EVALUATION",
    "PURPOSE_PILOT",
    "PURPOSE_TRAIN_R_FEEDBACK",
    "PURPOSE_VALUES",
    "RECOMMENDED_TARGETS",
    "SAMPLING_VERSION",
    "VISIBLE_TURN_BUCKETS",
    "SampledTask",
    "SamplingError",
    "SamplingPlan",
    "SamplingSpec",
    "build_sampling_plan",
    "load_sampling_plan",
    "render_sampling_plan_markdown",
    "save_sampling_plan",
    "verify_sampling_plan",
]
