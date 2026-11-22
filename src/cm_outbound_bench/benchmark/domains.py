"""Domain registry for the multi-domain benchmark.

A domain contributes metadata, a task file, a state file, a policy appendix and
an evaluation check pack. Shared infrastructure (agent protocol, environment,
runner, conversation trace, evaluator core, metrics) stays in the runtime and is
never duplicated per domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from cm_outbound_bench.benchmark.checks import pack_for_domain
from cm_outbound_bench.benchmark.state import StateStore
from cm_outbound_bench.benchmark.tasks import BenchmarkTask, load_domain_tasks
from cm_outbound_bench.evaluator import CheckPack
from cm_outbound_bench.paths import data_path


@dataclass(frozen=True)
class DomainSpec:
    domain_id: str
    name: str
    domain_class: str
    task_file: str
    state_file: str
    policy_file: str
    max_steps: int = 32
    description: str = ""


DOMAIN_SPECS: dict[str, DomainSpec] = {
    "D01": DomainSpec(
        domain_id="D01",
        name="Product & Package",
        domain_class="PRIMARY_BUSINESS",
        task_file="d01_product.json",
        state_file="d01_product.json",
        policy_file="d01_product.md",
        description=(
            "Plan and package contents, allowances, pricing and comparison."
        ),
    ),
    "D02": DomainSpec(
        domain_id="D02",
        name="Promotion & Offer",
        domain_class="PRIMARY_BUSINESS",
        task_file="d02_promotion.json",
        state_file="d02_promotion.json",
        policy_file="d02_promotion.md",
        description=(
            "Promotion inquiry, eligibility, terms disclosure, consent and "
            "enrollment."
        ),
    ),
    "D03": DomainSpec(
        domain_id="D03",
        name="Billing & Recharge",
        domain_class="PRIMARY_BUSINESS",
        task_file="d03_billing.json",
        state_file="d03_billing.json",
        policy_file="d03_billing.md",
        description=(
            "Balance, arrears, charges, billing explanation and simulated top-up."
        ),
    ),
    "D04": DomainSpec(
        domain_id="D04",
        name="Subscription & Account Lifecycle",
        domain_class="PRIMARY_BUSINESS",
        task_file="d04_lifecycle.json",
        state_file="d04_lifecycle.json",
        policy_file="d04_lifecycle.md",
        description=(
            "Plan change, upgrade, downgrade, cancellation and lifecycle status."
        ),
    ),
    "D05": DomainSpec(
        domain_id="D05",
        name="Identity & Ownership",
        domain_class="CROSS_CUTTING",
        task_file="d05_identity.json",
        state_file="d05_identity.json",
        policy_file="d05_identity.md",
        description=(
            "Identity verification, real-name state and ownership transfer "
            "conditions."
        ),
    ),
    "D06": DomainSpec(
        domain_id="D06",
        name="Notification & Fulfillment",
        domain_class="CROSS_CUTTING",
        task_file="d06_notification.json",
        state_file="d06_notification.json",
        policy_file="d06_notification.md",
        description=(
            "Simulated notification, confirmation, delivery status and follow-up."
        ),
    ),
}

CORE_POLICY_FILE = "core.md"


def domain_ids() -> list[str]:
    return sorted(DOMAIN_SPECS)


def get_domain(domain_id: str) -> DomainSpec:
    spec = DOMAIN_SPECS.get(domain_id)
    if spec is None:
        raise KeyError(
            f"Unknown domain {domain_id!r}. Known domains: "
            + ", ".join(domain_ids())
        )
    return spec


def policy_for_domain(domain_id: str) -> str:
    """Return the shared core policy plus the domain appendix."""
    spec = get_domain(domain_id)
    core = data_path("policy", CORE_POLICY_FILE).read_text(encoding="utf-8")
    appendix = data_path("policy", spec.policy_file).read_text(encoding="utf-8")
    return f"{core.rstrip()}\n\n{appendix.lstrip()}"


@lru_cache(maxsize=None)
def tasks_for_domain(domain_id: str) -> tuple[BenchmarkTask, ...]:
    spec = get_domain(domain_id)
    return tuple(load_domain_tasks(domain_id, spec.task_file))


@lru_cache(maxsize=None)
def states_for_domain(domain_id: str) -> StateStore:
    spec = get_domain(domain_id)
    return StateStore.from_package("synthetic", "states", spec.state_file)


def check_pack_for_domain(domain_id: str) -> CheckPack | None:
    return pack_for_domain(domain_id)


def all_tasks() -> list[BenchmarkTask]:
    result: list[BenchmarkTask] = []
    for domain_id in domain_ids():
        result.extend(tasks_for_domain(domain_id))
    return result


def find_task(task_id: str) -> BenchmarkTask:
    for task in all_tasks():
        if task.task_id == task_id:
            return task
    available = ", ".join(task.task_id for task in all_tasks())
    raise ValueError(f"Unknown benchmark task {task_id!r}. Available: {available}")


def domain_of_task(task_id: str) -> DomainSpec:
    return get_domain(find_task(task_id).domain_id)
