"""Multi-domain benchmark runtime: tasks, state, domains, evaluation, metrics.

The ``benchmark`` package extends the original marketing runtime with:

* declarative benchmark tasks and per-task initial state;
* a scripted, deterministic customer simulator;
* a domain registry with toolkits and evaluation check packs;
* run manifests and aggregate metrics for reproducible comparison.

It never imports ``tau2`` and never reads the corpus database.
"""

from __future__ import annotations

from cm_outbound_bench.benchmark.simulator import (
    ConsentAction,
    Intent,
    ScriptedSimulator,
    SimulatorReply,
    SimulatorScript,
    SimulatorStep,
)
from cm_outbound_bench.benchmark.toolkits import (
    CompositeToolkit,
    DomainToolkit,
    build_toolkit,
)

__all__ = [
    "CompositeToolkit",
    "ConsentAction",
    "DomainToolkit",
    "Intent",
    "ScriptedSimulator",
    "SimulatorReply",
    "SimulatorScript",
    "SimulatorStep",
    "build_toolkit",
]
