"""Canonical version labels shared by the benchmark runtime.

The evaluator version identifies the evaluator implementation that produced an
evaluation, manifest, trace or metric. It must not be duplicated in task
metadata or test fixtures.

The value is defined in :mod:`cm_outbound_bench.evaluator`, next to the
implementation whose behaviour it labels, and re-exported here as the single
shared import point for consumers such as the declarative task model. The
definition deliberately stays in ``evaluator.py``: ``evaluator_hash()`` hashes
that module, so moving the literal would change the recorded evaluator hash
without changing any evaluation behaviour.
"""

from cm_outbound_bench.evaluator import EVALUATOR_VERSION

__all__ = ["EVALUATOR_VERSION"]
