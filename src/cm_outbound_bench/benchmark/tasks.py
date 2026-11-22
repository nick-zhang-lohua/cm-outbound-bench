"""Declarative benchmark task model, loading and validation.

A task is public benchmark metadata. It separates:

* the agent-visible objective and target line;
* simulator-private scripting (never shown to the agent);
* deterministic expectations used by the evaluator;
* optional deterministic answer-fact assertions (never shown to the agent);
* opaque evidence references into the corpus database.

No field in this module may contain transcript text.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import Field, model_validator

from cm_outbound_bench.benchmark.answer_facts import DEFAULT_NEGATION_WINDOW
from cm_outbound_bench.benchmark.simulator import SimulatorScript
from cm_outbound_bench.benchmark.versions import EVALUATOR_VERSION
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.paths import data_path
from cm_outbound_bench.tasks import AgentTaskView

#: Task specification version. This is deliberately independent of the
#: evaluator version below.
TASK_VERSION = "1.0"

#: Every collection a task may assert over. ``expected_actions`` and
#: ``forbidden_actions`` share one vocabulary so a negative expectation is as
#: expressive as a positive one.
STATEFUL_COLLECTIONS: tuple[str, ...] = (
    "enrollments",
    "sms_records",
    "consent_records",
    "do_not_call_records",
    "notifications",
    "recharges",
    "plan_change_requests",
    "cancellation_requests",
    "action_consents",
    "charges",
)

PROVENANCE_STATUSES = (
    "OBSERVED_PATTERN",
    "HUMAN_REVIEWED_PATTERN",
    "AUTHORITATIVE_POLICY_VERIFIED",
    "SYNTHETIC_BENCHMARK_ASSUMPTION",
    "SAFETY_STRESS_SCENARIO",
)

#: Technical execution status of a scenario. It is deliberately separate from
#: ``rule_provenance_status`` (business provenance) and from the semantic review
#: status recorded in the scenario catalogue. A CANDIDATE task is executable and
#: its results keep the CANDIDATE label.
SCENARIO_STATUSES = (
    "CANDIDATE",
    "ENGINEERING_FIXTURE",
    "HUMAN_REVIEWED",
    "DEPRECATED",
)

CollectionName = Literal[
    "enrollments",
    "sms_records",
    "consent_records",
    "do_not_call_records",
    "notifications",
    "recharges",
    "plan_change_requests",
    "cancellation_requests",
    "action_consents",
    "charges",
]


@lru_cache(maxsize=None)
def collection_field_names(collection: str) -> tuple[str, ...]:
    """Return the declared field names of one stateful collection's record model.

    This is what lets ``benchmark verify`` reject a typo in an expectation at
    load time instead of raising ``AttributeError`` during evaluation.
    """
    from cm_outbound_bench.domain.data_model import CmOutboundDB

    if collection not in STATEFUL_COLLECTIONS:
        raise KeyError(f"Unknown stateful collection {collection!r}")
    annotation = CmOutboundDB.model_fields[collection].annotation
    element = get_args(annotation)[0] if get_origin(annotation) is list else None
    if element is None:  # pragma: no cover - the DB declares list[...] fields
        raise KeyError(f"Collection {collection!r} is not a record list")
    return tuple(element.model_fields)


@lru_cache(maxsize=None)
def collection_field_types(collection: str) -> dict[str, Any]:
    """Return ``field name -> declared annotation`` for one collection."""
    from cm_outbound_bench.domain.data_model import CmOutboundDB

    annotation = CmOutboundDB.model_fields[collection].annotation
    element = get_args(annotation)[0]
    return {name: info.annotation for name, info in element.model_fields.items()}


def _annotation_accepts(annotation: Any, value: Any) -> bool:
    """A conservative, deterministic type check for one expectation value.

    Literal annotations are checked against their allowed values; model
    annotations are not checked because tasks never assert over a whole nested
    record.
    """
    members = _flatten_union(annotation)
    if value is None:
        return type(None) in members
    for candidate in members:
        if get_origin(candidate) is Literal:
            if value in get_args(candidate):
                return True
            continue
        if candidate in (list, dict):
            if isinstance(value, (list, dict)):
                return True
            continue
        if candidate is bool:
            if isinstance(value, bool):
                return True
            continue
        if candidate is int:
            if isinstance(value, int) and not isinstance(value, bool):
                return True
            continue
        if candidate is float:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return True
            continue
        if candidate is str:
            if isinstance(value, str):
                return True
            continue
        # An unresolved model or enum annotation is not type-checked here.
        return True
    return False


def _flatten_union(annotation: Any) -> tuple[Any, ...]:
    """Return the members of an ``X | None`` style annotation."""
    origin = get_origin(annotation)
    if origin is None:
        return (annotation,)
    members: list[Any] = []
    for argument in get_args(annotation):
        if get_origin(argument) is Literal:
            members.append(argument)
        elif get_origin(argument) in (list, dict):
            members.append(get_origin(argument))
        else:
            members.extend(_flatten_union(argument))
    return tuple(members)


def _validate_match_payload(collection: str, match: dict[str, Any]) -> None:
    """Reject unknown fields and impossible value types at load time."""
    known = set(collection_field_names(collection))
    unknown = sorted(set(match) - known)
    if unknown:
        raise ValueError(
            f"State expectation for {collection!r} references unknown field(s): "
            f"{', '.join(unknown)}"
        )
    types = collection_field_types(collection)
    for field_name, expected in match.items():
        annotation = types[field_name]
        if not _annotation_accepts(annotation, expected):
            declared = getattr(annotation, "__name__", str(annotation))
            raise ValueError(
                f"State expectation for {collection!r}.{field_name} has value "
                f"{expected!r} which does not match the declared type {declared}"
            )


class EvidenceRef(DomainModel):
    """Opaque, aggregate evidence backing a scenario.

    Only identifiers are stored. Transcript text is never copied into the
    benchmark catalogue or into agent context.
    """

    evidence_id: str = Field(min_length=1)
    transcript_id: str = Field(
        min_length=1,
        description="Opaque corpus transcript identifier.",
    )
    seq_start: int | None = Field(default=None, ge=0)
    seq_end: int | None = Field(default=None, ge=0)
    pattern_type: str = Field(
        min_length=1, description="Observed business pattern family."
    )
    provenance_status: Literal[
        "OBSERVED_PATTERN",
        "HUMAN_REVIEWED_PATTERN",
        "AUTHORITATIVE_POLICY_VERIFIED",
        "SYNTHETIC_BENCHMARK_ASSUMPTION",
        "SAFETY_STRESS_SCENARIO",
    ]
    note: str = Field(
        default="",
        description="Non-sensitive justification, never transcript content.",
    )

    @model_validator(mode="after")
    def validate_range(self) -> "EvidenceRef":
        if (
            self.seq_start is not None
            and self.seq_end is not None
            and self.seq_end < self.seq_start
        ):
            raise ValueError("seq_end must not precede seq_start")
        return self


class StateExpectation(DomainModel):
    """One deterministic expectation over the final synthetic database."""

    collection: CollectionName
    match: dict[str, Any] = Field(
        default_factory=dict,
        description="Field/value pairs that must match on each found record.",
    )
    expected_count: int = Field(
        default=1,
        ge=0,
        description="Exact number of matching records expected in final state.",
    )
    description: str = Field(default="", description="Human-readable intent.")

    @model_validator(mode="after")
    def validate_match_fields(self) -> "StateExpectation":
        _validate_match_payload(self.collection, self.match)
        return self


class ForbiddenOutcome(DomainModel):
    """An outcome that must not appear in the final synthetic database.

    The collection vocabulary is identical to :class:`StateExpectation`, so a
    task can forbid a consent record, an action consent or a charge line as
    precisely as it can require one.
    """

    collection: CollectionName
    match: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="")

    @model_validator(mode="after")
    def validate_match_fields(self) -> "ForbiddenOutcome":
        _validate_match_payload(self.collection, self.match)
        return self


class RequiredFact(DomainModel):
    """A statement the agent must make for the customer to be informed."""

    fact_id: str = Field(min_length=1)
    description: str = ""
    accepted_patterns: list[str] = Field(
        min_length=1,
        description="Any one of these affirmed patterns satisfies the fact.",
    )
    required: bool = True
    negation_window: int = Field(
        default=DEFAULT_NEGATION_WINDOW,
        ge=0,
        le=40,
        description="Characters before a match inspected for a negation marker.",
    )
    scope: Literal["any_agent_message", "final_answer", "final_report"] | None = Field(
        default=None,
        description=(
            "Optional per-fact scope override. When null the answer assertion's "
            "scope is used. 'final_report' means the agent's messages after the "
            "last simulated customer turn."
        ),
    )
    grounding_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Optional tool names whose recorded result must back the statement. "
            "When empty the fact is a free-text assertion only."
        ),
    )
    grounding_field: str = Field(
        default="status",
        min_length=1,
        description="Result field the grounding tools must return.",
    )
    grounding_value: str = Field(
        default="",
        description=(
            "Result value the grounding tools must return. Empty disables "
            "grounding."
        ),
    )
    expected_value: str = Field(
        default="",
        description=(
            "Human-readable expected value for documentation only. It is never "
            "shown to the agent and never used for matching."
        ),
    )

    @model_validator(mode="after")
    def validate_patterns(self) -> "RequiredFact":
        if any(not pattern.strip() for pattern in self.accepted_patterns):
            raise ValueError(f"Fact {self.fact_id!r} has an empty accepted pattern")
        return self

    @model_validator(mode="after")
    def validate_grounding(self) -> "RequiredFact":
        if not self.grounding_tools and not self.grounding_value:
            return self
        if not self.grounding_tools or not self.grounding_value:
            raise ValueError(
                f"Fact {self.fact_id!r} must declare grounding_tools and "
                "grounding_value together"
            )
        return self


class ForbiddenFact(DomainModel):
    """A statement the agent must not make because it is factually false."""

    fact_id: str = Field(min_length=1)
    description: str = ""
    forbidden_patterns: list[str] = Field(min_length=1)
    required: bool = True
    negation_window: int = Field(
        default=DEFAULT_NEGATION_WINDOW,
        ge=0,
        le=40,
    )

    @model_validator(mode="after")
    def validate_patterns(self) -> "ForbiddenFact":
        if any(not pattern.strip() for pattern in self.forbidden_patterns):
            raise ValueError(f"Fact {self.fact_id!r} has an empty forbidden pattern")
        return self


class AnswerAssertions(DomainModel):
    """Task-specific, versioned answer-fact assertions.

    This schema holds evaluator-private answer keys. It is never placed in
    :class:`~cm_outbound_bench.tasks.AgentTaskView` and never appears in agent
    context.
    """

    assertion_version: Literal["1.0"] = "1.0"
    scope: Literal["final_answer", "any_agent_message"] = "final_answer"
    required_facts: list[RequiredFact] = Field(default_factory=list)
    forbidden_facts: list[ForbiddenFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fact_ids(self) -> "AnswerAssertions":
        identifiers = [fact.fact_id for fact in self.required_facts] + [
            fact.fact_id for fact in self.forbidden_facts
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate fact_id values in answer assertions")
        return self

    def is_empty(self) -> bool:
        return not self.required_facts and not self.forbidden_facts


class SuccessCriteria(DomainModel):
    max_turns: int = Field(default=40, ge=1)
    max_tool_calls: int = Field(default=25, ge=0)
    require_finish: bool = True
    require_identity_verification: bool | None = Field(
        default=None,
        description=(
            "Explicit identity-verification requirement. When null the task "
            "derives it from the protected tools in its allow-list."
        ),
    )
    max_tool_errors: int | None = Field(
        default=None,
        ge=0,
        description="Optional hard limit on failed tool calls.",
    )
    deadline_seconds: float = Field(
        default=120.0,
        gt=0,
        le=3600,
        description="Wall-clock budget checked between agent actions.",
    )


class BenchmarkTask(DomainModel):
    task_id: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    task_version: str = TASK_VERSION
    scenario_status: Literal[
        "CANDIDATE",
        "ENGINEERING_FIXTURE",
        "HUMAN_REVIEWED",
        "DEPRECATED",
    ] = "CANDIDATE"
    description: str = Field(min_length=1)
    agent_objective: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    phone_number_id: str = Field(min_length=1)
    offer_id: str | None = None
    initial_state_id: str = Field(min_length=1)
    simulator: SimulatorScript
    allowed_tools: list[str] = Field(min_length=1)
    required_tools: list[str] = Field(default_factory=list)
    expected_actions: list[StateExpectation] = Field(default_factory=list)
    forbidden_actions: list[ForbiddenOutcome] = Field(default_factory=list)
    answer_assertions: AnswerAssertions | None = None
    success_criteria: SuccessCriteria = Field(default_factory=SuccessCriteria)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    evaluator_version: str = Field(
        default=EVALUATOR_VERSION,
        description=(
            "Evaluator version the runtime records for this task. It must match "
            "the installed evaluator implementation; task files must not pin a "
            "stale override."
        ),
    )
    rule_provenance_status: Literal[
        "OBSERVED_PATTERN",
        "HUMAN_REVIEWED_PATTERN",
        "AUTHORITATIVE_POLICY_VERIFIED",
        "SYNTHETIC_BENCHMARK_ASSUMPTION",
        "SAFETY_STRESS_SCENARIO",
    ] = "OBSERVED_PATTERN"

    @model_validator(mode="after")
    def validate_task(self) -> "BenchmarkTask":
        if self.evaluator_version != EVALUATOR_VERSION:
            raise ValueError(
                f"Task {self.task_id} declares evaluator_version "
                f"{self.evaluator_version!r}, but the installed evaluator is "
                f"{EVALUATOR_VERSION!r}. Remove the task-level override: the "
                "runtime records the version of the evaluator that actually ran."
            )
        if not set(self.required_tools) <= set(self.allowed_tools):
            missing = sorted(set(self.required_tools) - set(self.allowed_tools))
            raise ValueError(
                f"Task {self.task_id} requires tools outside its allow-list: "
                f"{', '.join(missing)}"
            )
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError(f"Task {self.task_id} lists duplicate allowed tools")
        if self.offer_id is None:
            uses_offer_tools = {
                "enroll_offer",
                "check_offer_eligibility",
                "get_offer_details",
            } & set(self.allowed_tools)
            if uses_offer_tools:
                raise ValueError(
                    f"Task {self.task_id} allows offer tools without an offer_id"
                )
        consent_steps = [
            step for step in self.simulator.steps if step.consent_action is not None
        ]
        if len(consent_steps) > 1:
            raise ValueError(
                f"Task {self.task_id} declares more than one consent step"
            )
        return self

    # -- derived views -----------------------------------------------------

    def agent_view(self, domain_name: str = "") -> AgentTaskView:
        return AgentTaskView(
            domain=domain_name or self.domain_id.lower(),
            objective=self.agent_objective,
            phone_number_id=self.phone_number_id,
            offer_id=self.offer_id,
        )

    def consent_capability(self) -> tuple[bool, str | None, str]:
        """Return (allowed, action, target) for the scripted consent step."""
        for step in self.simulator.steps:
            if step.consent_action is None:
                continue
            target = step.consent_target.replace("{offer_id}", self.offer_id or "")
            return True, step.consent_action.value, target
        return False, None, ""

    def requires_identity_verification(self) -> bool:
        """Whether identity verification is a required evaluation dimension.

        Explicit configuration wins. Otherwise the task requires verification
        when it allows a protected business tool, because every protected tool
        enforces verification internally.
        """
        configured = self.success_criteria.require_identity_verification
        if configured is not None:
            return configured
        from cm_outbound_bench.evaluator import PROTECTED_TOOLS

        return bool(set(self.allowed_tools) & PROTECTED_TOOLS)

    def canonical_payload(self) -> dict[str, Any]:
        """Return the stable, hashable task payload."""
        return self.model_dump(mode="json")

    def task_hash(self) -> str:
        """Return the SHA-256 of the canonical task payload."""
        return hashlib.sha256(
            json.dumps(
                self.canonical_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()


class BenchmarkTaskFile(DomainModel):
    domain_id: str = Field(min_length=1)
    task_version: str = TASK_VERSION
    tasks: list[BenchmarkTask] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_tasks(self) -> "BenchmarkTaskFile":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Duplicate task_id values in task file")
        scenario_ids = [task.scenario_id for task in self.tasks]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("Duplicate scenario_id values in task file")
        for task in self.tasks:
            if task.domain_id != self.domain_id:
                raise ValueError(
                    f"Task {task.task_id} declares domain {task.domain_id} inside "
                    f"a {self.domain_id} task file"
                )
        return self


def load_task_file(
    path: str | Path,
    *,
    expected_domain: str | None = None,
) -> BenchmarkTaskFile:
    task_file = BenchmarkTaskFile.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    if expected_domain is not None and task_file.domain_id != expected_domain:
        raise ValueError(
            f"Task file {path} declares domain {task_file.domain_id}, "
            f"expected {expected_domain}"
        )
    return task_file


def load_domain_tasks(domain_id: str, filename: str) -> list[BenchmarkTask]:
    """Load a packaged task file for one domain."""
    task_file = load_task_file(
        data_path("tasks", filename), expected_domain=domain_id
    )
    return task_file.tasks


def load_task_by_id(task_id: str, tasks: list[BenchmarkTask]) -> BenchmarkTask:
    for task in tasks:
        if task.task_id == task_id:
            return task
    available = ", ".join(task.task_id for task in tasks)
    raise ValueError(f"Unknown task {task_id!r}. Available tasks: {available}")
