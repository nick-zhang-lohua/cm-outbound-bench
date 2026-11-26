"""DeepSeek V4 Pro Ground Truth construction and adjudication (Milestone 1.2).

This package implements the official Benchmark Gold Label pipeline:

* two independent V4 Pro annotation passes on the same agent-visible prefix;
* semantic pass comparison with the fixed disagreement taxonomy;
* V4 Pro adjudication for disagreements;
* checkpointed, resumable, idempotent storage with deterministic cache keys;
* frozen, hash-pinned Gold Label artifacts for Train-R and Dev.

The historical agent response is never shown to the annotator, the adjudicator or
the scorer, and is never used as a Gold Label. Test data, the Simulator, and the
unresolved channels 3/4/5 stay untouched.
"""

from __future__ import annotations

GROUND_TRUTH_GENERATOR = "deepseek-v4-pro"

__all__ = ["GROUND_TRUTH_GENERATOR"]
