"""Stage 6: reproducibility metadata in manifests and traces."""

from __future__ import annotations

import json
import time
from pathlib import Path

from cm_outbound_bench.agent import AgentContext
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import (
    BENCHMARK_VERSION,
    SEED_UNUSED_DETERMINISTIC,
    execute_task,
)
from cm_outbound_bench.benchmark.metrics import build_manifest
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall
from cm_outbound_bench.providers.config import (
    ProviderProfile,
    ResolvedProviderConfig,
    resolve_provider_profile,
)


def test_manifest_records_real_start_and_end_times() -> None:
    class SlowAgent(BenchmarkDemoAgent):
        def act(self, context: AgentContext):
            time.sleep(0.02)
            return super().act(context)

    outcome = execute_task(
        find_task("d01_plan_comparison"), agent_factory=SlowAgent
    )
    manifest = build_manifest(outcome, agent_type="benchmark_demo")

    assert manifest.started_at
    assert manifest.completed_at
    assert manifest.completed_at > manifest.started_at
    assert manifest.setup_ms >= 0
    assert manifest.episode_ms >= 15  # the agent slept at least once
    assert manifest.evaluation_ms >= 0
    assert manifest.total_ms >= manifest.episode_ms + manifest.evaluation_ms
    assert manifest.latency_ms == manifest.episode_ms + manifest.evaluation_ms


def test_stage_durations_are_separate_fields() -> None:
    outcome = execute_task(
        find_task("d02_offer_benefit_inquiry_accept"), agent_factory=BenchmarkDemoAgent
    )
    assert outcome.total_ms >= outcome.setup_ms + outcome.episode_ms


def test_task_policy_prompt_and_evaluator_hashes_are_recorded() -> None:
    outcome = execute_task(
        find_task("d04_plan_upgrade_with_consent"), agent_factory=BenchmarkDemoAgent
    )
    manifest = build_manifest(outcome, agent_type="benchmark_demo")

    for value in (
        manifest.task_hash,
        manifest.policy_hash,
        manifest.prompt_hash,
        manifest.evaluator_hash,
    ):
        assert len(value) == 64, value
    assert manifest.prompt_version == "scripted-v1"

    second = build_manifest(
        execute_task(
            find_task("d04_plan_upgrade_with_consent"),
            agent_factory=BenchmarkDemoAgent,
        ),
        agent_type="benchmark_demo",
    )
    assert second.task_hash == manifest.task_hash
    assert second.policy_hash == manifest.policy_hash
    assert second.evaluator_hash == manifest.evaluator_hash
    assert second.prompt_hash == manifest.prompt_hash


def test_policy_hash_differs_between_domains() -> None:
    d01 = build_manifest(
        execute_task(
            find_task("d01_plan_comparison"), agent_factory=BenchmarkDemoAgent
        ),
        agent_type="benchmark_demo",
    )
    d03 = build_manifest(
        execute_task(
            find_task("d03_bill_explanation"), agent_factory=BenchmarkDemoAgent
        ),
        agent_type="benchmark_demo",
    )
    assert d01.policy_hash != d03.policy_hash
    assert d01.task_hash != d03.task_hash


class _UsageClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="call-1", tool_name="list_plans", arguments={}
                    )
                ],
                provider_usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            )
        return ProviderResponse(
            finish_requested=True,
            provider_usage={
                "prompt_tokens": 20,
                "completion_tokens": 1,
                "total_tokens": 21,
            },
        )


def test_token_usage_is_propagated_end_to_end() -> None:
    outcome = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=lambda: LLMBackedAgent(_UsageClient()),
        agent_name="llm",
    )
    assert outcome.token_usage == {
        "prompt_tokens": 32,
        "completion_tokens": 4,
        "total_tokens": 36,
    }
    manifest = build_manifest(outcome, agent_type="llm")
    assert manifest.token_usage["total_tokens"] == 36


class _SilentClient:
    def complete(self, *, messages, tools):
        return ProviderResponse(finish_requested=True)


def test_unreported_token_usage_is_absent_not_zero() -> None:
    outcome = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=lambda: LLMBackedAgent(_SilentClient()),
        agent_name="llm",
    )
    assert outcome.token_usage == {}
    manifest = build_manifest(outcome, agent_type="llm")
    assert manifest.token_usage == {}
    assert "total_tokens" not in json.dumps(manifest.model_dump(mode="json"))


def test_llm_prompt_hash_reflects_the_real_prompt() -> None:
    demo = execute_task(
        find_task("d01_plan_comparison"), agent_factory=BenchmarkDemoAgent
    )
    llm = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=lambda: LLMBackedAgent(_SilentClient()),
        agent_name="llm",
    )
    assert demo.prompt_hash
    assert llm.prompt_hash
    assert demo.prompt_hash != llm.prompt_hash
    assert llm.prompt_version == "system-v4"
    assert demo.prompt_version == "scripted-v1"


def test_manifest_records_provenance_status() -> None:
    outcome = execute_task(
        find_task("d05_ownership_transfer_conditions"),
        agent_factory=BenchmarkDemoAgent,
    )
    manifest = build_manifest(outcome, agent_type="benchmark_demo")
    assert manifest.rule_provenance_status == "SYNTHETIC_BENCHMARK_ASSUMPTION"
    assert manifest.scenario_status == "CANDIDATE"
    assert manifest.benchmark_version == BENCHMARK_VERSION


def test_seed_status_is_explicit(tmp_path: Path) -> None:
    outcome = execute_task(
        find_task("d01_plan_comparison"),
        agent_factory=BenchmarkDemoAgent,
        seed=7,
    )
    assert outcome.seed == 7
    assert outcome.seed_status == SEED_UNUSED_DETERMINISTIC
    manifest = build_manifest(outcome, agent_type="benchmark_demo")
    assert manifest.random_seed == 7
    assert manifest.seed_status == SEED_UNUSED_DETERMINISTIC


def test_provider_sampling_is_recorded_and_secrets_are_not(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "providers.toml"
    config.write_text(
        """
[providers.lab]
api_protocol = "openai_compatible"
base_url = "https://example.invalid/v1"
api_key_env = "LAB_API_KEY"
model = "lab-model"
[providers.lab.extra_params]
temperature = 0.0
top_p = 0.9
max_tokens = 512
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LAB_API_KEY", "secret-value-that-must-not-appear")

    from argparse import Namespace

    from cm_outbound_bench.benchmark import cli as benchmark_cli

    args = Namespace(agent="llm", provider="lab", provider_config=config)
    _factory, agent_name, info = benchmark_cli._build_agent(args, seed=99)

    assert agent_name == "llm"
    assert info["model"] == "lab-model"
    assert info["sampling"]["temperature"] == 0.0
    assert info["sampling"]["top_p"] == 0.9
    assert info["sampling"]["max_tokens"] == 512
    assert info["seed_component"] == "provider"
    serialized = json.dumps(info)
    assert "secret-value-that-must-not-appear" not in serialized
    assert "api_key" not in serialized.lower()


def test_provider_parameters_are_allow_listed() -> None:
    profile = ProviderProfile(
        base_url="https://example.invalid/v1",
        api_key_env="LAB_API_KEY",
        model="m",
        extra_params={
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "unreviewed_vendor_flag": "x",
        },
    )
    resolved = resolve_provider_profile(profile, environ={"LAB_API_KEY": "k"})
    assert isinstance(resolved, ResolvedProviderConfig)
    assert resolved.public_parameters == {
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }


def test_manifest_never_contains_secrets(tmp_path: Path) -> None:
    from cm_outbound_bench.benchmark.metrics import load_manifests
    from cm_outbound_bench.cli import main

    assert (
        main(
            [
                "benchmark",
                "run",
                "--task",
                "d02_offer_benefit_inquiry_accept",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    manifest = load_manifests(tmp_path)[0]
    payload = json.dumps(manifest.model_dump(mode="json"))
    assert "TEST_IDENTITY" not in payload
    assert "api_key" not in payload.lower()
    assert "bearer" not in payload.lower()
    assert '"conversation"' not in payload
    assert "trace_path" in payload
