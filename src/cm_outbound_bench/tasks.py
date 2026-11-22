"""Marketing task definitions and loading."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from cm_outbound_bench.domain.data_model import CmOutboundDB, DomainModel
from cm_outbound_bench.paths import data_path


class Persona(str, Enum):
    DIRECT_REFUSAL = "direct_refusal"
    PRICE_INQUIRY = "price_inquiry"
    NOT_OWNER = "not_owner"
    SCAM_SUSPICION = "scam_suspicion"
    HESITANT_ACCEPTANCE = "hesitant_acceptance"


class SimulatorTaskConfig(DomainModel):
    """Simulator-private scenario configuration.

    This object is never given to the agent.
    """

    persona: Persona = Field(description="Deterministic simulator behavior.")
    verification_token: str = Field(
        description="Synthetic identity token the simulated customer may share."
    )
    may_accept_offer: bool = Field(
        default=False,
        description="Whether this persona may ever accept the offer.",
    )
    may_give_consent: bool = Field(
        default=False,
        description="Whether this persona may eventually state explicit consent.",
    )
    response_behavior: str = Field(
        description="Private summary of expected simulator behavior.",
    )

    @model_validator(mode="after")
    def validate_consent_capability(self) -> "SimulatorTaskConfig":
        if self.may_give_consent and not self.may_accept_offer:
            raise ValueError("A persona cannot consent without being able to accept")
        return self


class EvaluationCriteria(DomainModel):
    enrollment_expected: bool = Field(
        description="Whether exactly one successful enrollment is expected."
    )
    consent_expected: bool = Field(
        description="Whether an explicit simulator consent record is expected."
    )
    do_not_call_expected: bool = Field(
        default=False,
        description="Whether a do-not-call record is expected.",
    )
    identity_verification_required: bool = Field(
        default=False,
        description="Whether successful identity verification is required.",
    )
    confirmation_sms_if_enrolled: bool = Field(
        default=True,
        description="Whether an enrollment requires a simulated confirmation SMS.",
    )
    forbid_protected_disclosure: bool = Field(
        default=True,
        description="Whether protected account data must never be disclosed.",
    )

    @model_validator(mode="after")
    def validate_expected_state(self) -> "EvaluationCriteria":
        if self.enrollment_expected and not self.consent_expected:
            raise ValueError("An expected enrollment requires expected consent")
        if self.do_not_call_expected and self.enrollment_expected:
            raise ValueError("A do-not-call outcome cannot expect an enrollment")
        return self


class MarketingTask(DomainModel):
    task_id: str = Field(min_length=1, description="Unique marketing task ID.")
    description: str = Field(
        description="Benchmark-author description of the scenario."
    )
    agent_objective: str = Field(description="Objective visible to the agent.")
    customer_id: str = Field(description="Synthetic customer involved in the task.")
    phone_number_id: str = Field(description="Target phone line.")
    offer_id: str = Field(description="Target marketing offer.")
    simulator: SimulatorTaskConfig = Field(
        description="Simulator-private scenario state."
    )
    evaluation: EvaluationCriteria = Field(
        description="Deterministic expectations used by the evaluator."
    )

    def agent_view(self) -> "AgentTaskView":
        """Return only the information the agent is allowed to see."""
        return AgentTaskView(
            domain="marketing",
            objective=self.agent_objective,
            phone_number_id=self.phone_number_id,
            offer_id=self.offer_id,
        )


class AgentTaskView(DomainModel):
    """Agent-visible task context with all simulator-private fields removed.

    ``domain`` and ``offer_id`` were widened for the multi-domain benchmark.
    The legacy marketing tasks still emit exactly the same four keys, so the
    original privacy test remains valid.
    """

    domain: str = "marketing"
    objective: str
    phone_number_id: str
    offer_id: str | None = None


class MarketingTaskFile(DomainModel):
    domain: Literal["marketing"]
    tasks: list[MarketingTask] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_task_ids(self) -> "MarketingTaskFile":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Duplicate task_id values in marketing task file")
        return self


def load_marketing_tasks(
    db: CmOutboundDB,
    path: str | Path | None = None,
) -> list[MarketingTask]:
    """Load task definitions and validate them against the synthetic DB."""
    task_path = Path(path) if path is not None else data_path("tasks", "marketing.json")
    task_file = MarketingTaskFile.model_validate_json(
        task_path.read_text(encoding="utf-8")
    )
    customers = {customer.customer_id: customer for customer in db.customers}
    phones = {phone.phone_number_id: phone for phone in db.phone_numbers}
    offers = {offer.offer_id: offer for offer in db.offers}

    for task in task_file.tasks:
        customer = customers.get(task.customer_id)
        if customer is None:
            raise ValueError(
                f"Task {task.task_id} references unknown customer {task.customer_id}"
            )
        phone = phones.get(task.phone_number_id)
        if phone is None:
            raise ValueError(
                f"Task {task.task_id} references unknown line {task.phone_number_id}"
            )
        if phone.owner_customer_id != task.customer_id:
            raise ValueError(
                f"Task {task.task_id} customer does not own {task.phone_number_id}"
            )
        if task.offer_id not in offers:
            raise ValueError(
                f"Task {task.task_id} references unknown offer {task.offer_id}"
            )
        if task.simulator.verification_token != customer.identity_token:
            raise ValueError(
                f"Task {task.task_id} simulator token does not match the customer"
            )

    return task_file.tasks


def find_task(task_id: str, tasks: list[MarketingTask]) -> MarketingTask:
    """Return one task by ID or raise a clear error."""
    for task in tasks:
        if task.task_id == task_id:
            return task
    available = ", ".join(task.task_id for task in tasks)
    raise ValueError(f"Unknown task {task_id!r}. Available tasks: {available}")
