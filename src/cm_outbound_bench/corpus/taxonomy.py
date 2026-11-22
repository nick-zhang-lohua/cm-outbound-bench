"""Versioned domain taxonomy for the corpus.

The taxonomy is data, not code: it is seeded into ``domain_taxonomy`` and every
classification row references it. This module holds the Python-side catalogue
used by the classifier and the reports.

Status semantics:

* ``PROPOSED`` — seeded, not yet validated against corpus evidence;
* ``ACTIVE`` — validated and used for benchmark coverage claims;
* ``RETIRED`` — superseded, kept for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from cm_outbound_bench.corpus.schema import DOMAIN_SEEDS, TAXONOMY_VERSION

PRIMARY_BUSINESS = "PRIMARY_BUSINESS"
CROSS_CUTTING = "CROSS_CUTTING"


@dataclass(frozen=True)
class DomainDefinition:
    domain_id: str
    name: str
    domain_class: str
    description: str
    taxonomy_version: str = TAXONOMY_VERSION
    status: str = "PROPOSED"


TAXONOMY: tuple[DomainDefinition, ...] = tuple(
    DomainDefinition(
        domain_id=domain_id,
        name=name,
        domain_class=domain_class,
        description=description,
    )
    for domain_id, name, domain_class, description in DOMAIN_SEEDS
)

DOMAIN_IDS: tuple[str, ...] = tuple(domain.domain_id for domain in TAXONOMY)
PRIMARY_DOMAIN_IDS: tuple[str, ...] = tuple(
    domain.domain_id
    for domain in TAXONOMY
    if domain.domain_class == PRIMARY_BUSINESS
)
CROSS_CUTTING_DOMAIN_IDS: tuple[str, ...] = tuple(
    domain.domain_id
    for domain in TAXONOMY
    if domain.domain_class == CROSS_CUTTING
)

UNCLASSIFIED = "UNCLASSIFIED"


def domain_by_id(domain_id: str) -> DomainDefinition:
    for domain in TAXONOMY:
        if domain.domain_id == domain_id:
            return domain
    raise KeyError(f"Unknown domain: {domain_id}")


def domain_name(domain_id: str) -> str:
    return domain_by_id(domain_id).name


def taxonomy_summary() -> list[dict[str, str]]:
    """Return a JSON-serialisable taxonomy description."""
    return [
        {
            "domain_id": domain.domain_id,
            "name": domain.name,
            "domain_class": domain.domain_class,
            "description": domain.description,
            "taxonomy_version": domain.taxonomy_version,
            "status": domain.status,
        }
        for domain in TAXONOMY
    ]
