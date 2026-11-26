"""Static, real-transcript decision-point benchmark (Group A baseline).

Public entry points:

* :mod:`cm_outbound_bench.decisions.schema` - public and private task models;
* :mod:`cm_outbound_bench.decisions.builder` - decision-point construction;
* :mod:`cm_outbound_bench.decisions.store` - agent-safe task loading;
* :mod:`cm_outbound_bench.decisions.agent` - provider-independent agent;
* :mod:`cm_outbound_bench.decisions.runner` - the benchmark runner;
* :mod:`cm_outbound_bench.decisions.evaluator` - technical and business metrics;
* :mod:`cm_outbound_bench.decisions.trace` - execution traces.

Milestone 1.1 additions:

* :mod:`cm_outbound_bench.decisions.channels` - channel semantics registry; channels
  3/4/5 stay unknown pending human review;
* :mod:`cm_outbound_bench.decisions.channel_audit` - channel statistics, samples and
  benchmark impact;
* :mod:`cm_outbound_bench.decisions.identity_audit` - conversation identity and
  dataset verification;
* :mod:`cm_outbound_bench.decisions.annotation` - the two-tier business annotation
  schema;
* :mod:`cm_outbound_bench.decisions.annotation_io` - packet export, submission
  import, adjudication and application;
* :mod:`cm_outbound_bench.decisions.sampling` - reproducible annotation samples;
* :mod:`cm_outbound_bench.decisions.agreement` - inter-annotator agreement;
* :mod:`cm_outbound_bench.decisions.business_sources` - business reference inventory;
* :mod:`cm_outbound_bench.decisions.business_report` - the Group A business baseline
  report.

There is no customer simulator and no workflow/flowchart integration in this
package. Groups B and C are deliberately absent.
"""

from __future__ import annotations

from cm_outbound_bench.decisions.agent_prompt import PROMPT_VERSION
from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_GUIDELINE_VERSION,
    AnnotationProvenance,
    AnnotationSubmission,
    DecisionReference,
)
from cm_outbound_bench.decisions.builder import (
    BUILDER_VERSION,
    BuilderConfig,
    DecisionTaskBuilder,
)
from cm_outbound_bench.decisions.channels import (
    CONFIRMED_CHANNEL_ROLES,
    UNKNOWN_CHANNEL_IDS,
    assert_production_mapping_unchanged,
    role_for_channel,
)
from cm_outbound_bench.decisions.evaluator import (
    EVALUATOR_VERSION,
    aggregate_evaluations,
    evaluate_invocation,
)
from cm_outbound_bench.decisions.sampling import (
    SamplingPlan,
    SamplingSpec,
    build_sampling_plan,
    verify_sampling_plan,
)
from cm_outbound_bench.decisions.schema import (
    BENCHMARK_VERSION,
    AgentDecisionOutput,
    AgentInvocation,
    AgentRequest,
    PrivateTaskReference,
    PublicDecisionTask,
)
from cm_outbound_bench.decisions.store import TaskStore

__all__ = [
    "ANNOTATION_GUIDELINE_VERSION",
    "BENCHMARK_VERSION",
    "BUILDER_VERSION",
    "CONFIRMED_CHANNEL_ROLES",
    "EVALUATOR_VERSION",
    "PROMPT_VERSION",
    "UNKNOWN_CHANNEL_IDS",
    "AgentDecisionOutput",
    "AgentInvocation",
    "AgentRequest",
    "AnnotationProvenance",
    "AnnotationSubmission",
    "BuilderConfig",
    "DecisionReference",
    "DecisionTaskBuilder",
    "PrivateTaskReference",
    "PublicDecisionTask",
    "SamplingPlan",
    "SamplingSpec",
    "TaskStore",
    "aggregate_evaluations",
    "assert_production_mapping_unchanged",
    "build_sampling_plan",
    "evaluate_invocation",
    "role_for_channel",
    "verify_sampling_plan",
]
