"""Bootstrap confidence intervals for the Group A business metrics.

The independent sampling unit is the conversation unit. Every Dev sample task
comes from a distinct conversation unit (one task per conversation), so task
resampling and unit resampling coincide; the code still resamples units
explicitly so the assumption is enforced rather than assumed.

Confidence intervals measure sampling uncertainty only — not the systematic
uncertainty of the LLM-generated Ground Truth. Every report states this.
"""

from __future__ import annotations

import random
from typing import Mapping, Sequence

DEFAULT_BOOTSTRAP_REPLICATIONS = 2000
DEFAULT_BOOTSTRAP_SEED = 20261126


def bootstrap_rate_ci(
    *,
    numerators: Mapping[str, Sequence[int]],
    denominators: Sequence[int],
    metric_names: Sequence[str],
    unit_ids: Sequence[str],
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, dict[str, float]]:
    """Percentile bootstrap for rates over conversation units.

    ``numerators[metric]`` is the 0/1 outcome per unit; ``denominators`` is the
    0/1 scored indicator per unit (units outside a metric's denominator carry
    numerator 0 but denominator 0 so they drop out naturally).
    """
    rng = random.Random(seed)
    units = len(unit_ids)
    result: dict[str, dict[str, float]] = {}
    for metric in metric_names:
        numerator = numerators.get(metric, [])
        estimates: list[float] = []
        for _ in range(replications):
            sample_num = 0
            sample_den = 0
            for _ in range(units):
                index = rng.randrange(units)
                sample_num += numerator[index]
                sample_den += denominators[index]
            estimates.append(sample_num / sample_den if sample_den else 0.0)
        estimates.sort()
        lower = estimates[int(0.025 * (replications - 1))]
        upper = estimates[int(0.975 * (replications - 1))]
        result[metric] = {
            "ci_lower": round(lower, 6),
            "ci_upper": round(upper, 6),
            "level": 0.95,
            "method": "percentile",
            "replications": replications,
            "seed": seed,
        }
    return result


def describe(
    *,
    executed_tasks: int,
    scored_tasks: int,
    independent_sampling_unit: str,
    bootstrap: dict[str, dict[str, float]],
) -> dict[str, object]:
    return {
        "executed_tasks": executed_tasks,
        "scored_tasks": scored_tasks,
        "excluded_tasks": executed_tasks - scored_tasks,
        "scored_fraction": (
            round(scored_tasks / executed_tasks, 6) if executed_tasks else None
        ),
        "bootstrap_procedure": (
            "conversation-unit-level percentile bootstrap; each Dev sample task "
            "comes from a distinct conversation unit, so the effective sampling "
            "unit equals the number of independent conversations"
        ),
        "bootstrap_replications": DEFAULT_BOOTSTRAP_REPLICATIONS,
        "bootstrap_seed": DEFAULT_BOOTSTRAP_SEED,
        "independent_sampling_unit": independent_sampling_unit,
        "confidence_intervals": bootstrap,
        "limitation": (
            "Confidence intervals measure sampling uncertainty only; they do not "
            "capture systematic error of the LLM-generated Ground Truth or of the "
            "LLM judge."
        ),
    }


__all__ = [
    "DEFAULT_BOOTSTRAP_REPLICATIONS",
    "DEFAULT_BOOTSTRAP_SEED",
    "bootstrap_rate_ci",
    "describe",
]
