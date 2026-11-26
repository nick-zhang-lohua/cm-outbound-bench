"""Tests for the two-pass annotation pipeline with a fake judge client."""

from __future__ import annotations

import json
import uuid

import pytest

from cm_outbound_bench.decisions.gold.annotator import (
    AnnotationContext,
    AnnotationError,
    run_annotation_stage,
    run_pass,
)
from cm_outbound_bench.decisions.gold.config import load_judge_config
from cm_outbound_bench.decisions.gold.models import (
    PASS_A,
    PASS_B,
)
from cm_outbound_bench.decisions.gold.provider import JudgeResponse
from cm_outbound_bench.decisions.gold.storage import GoldStorage
from cm_outbound_bench.decisions.schema import PublicDecisionTask, VisibleTurn


class FakeClient:
    """Returns canned responses in call order; records every prompt it received."""

    def __init__(
        self, payloads: list[dict] | dict, *, fail: bool = False
    ) -> None:
        self._payloads = list(payloads) if isinstance(payloads, list) else [payloads]
        self._fail = fail
        self.prompts: list[tuple[str, str]] = []

    def with_credentials(self, api_key: str, headers: dict) -> "FakeClient":
        return self

    def complete_json(self, *, system, user, **kwargs) -> JudgeResponse:
        self.prompts.append((system, user))
        if self._fail:
            raise RuntimeError("fake provider failure")
        payload = self._payloads[min(len(self._payloads) - 1, len(self.prompts) - 1)]
        return JudgeResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_reported="deepseek-v4-pro",
            api_request_id=f"req-{uuid.uuid4().hex[:8]}",
            token_usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            response_hash="abc",
            retry_count=0,
        )


def _task(task_id: str = "TASK_test") -> PublicDecisionTask:
    return PublicDecisionTask(
        task_id=task_id,
        visible_context=[
            VisibleTurn(role="historical_agent", content="您好，这里是移动客服。"),
            VisibleTurn(role="customer", content="我想咨询套餐。"),
        ],
    )


def _context(client: FakeClient, storage: GoldStorage, *, dry_run: bool = False) -> AnnotationContext:
    config = load_judge_config()
    return AnnotationContext(
        config=config,
        storage=storage,
        client_factory=lambda: client,
        model="deepseek-v4-pro",
        provider_label="deepseek-v4-pro-judge",
        annotation_config_hash="hash-annotation",
        prompt_version="v1",
        dry_run=dry_run,
    )


def _scored_payload() -> dict:
    return {
        "task_answerability": "sufficient_for_action",
        "customer_intent": "咨询套餐",
        "conversation_state": "客户提出套餐咨询",
        "primary_expected_decision": {
            "category": "explain",
            "action": "介绍套餐内容",
            "reason": "客户想了解套餐",
        },
        "acceptable_alternative_decisions": [],
        "required_information": [],
        "required_steps": ["介绍套餐内容"],
        "optional_steps": [],
        "prohibited_steps": [],
        "branch_conditions": [],
        "expected_branch": None,
        "external_policy_dependency": False,
        "policy_verified": False,
        "unsupported_claims_to_avoid": [],
        "evidence_refs": [{"visible_turn": 2, "quote": "我想咨询套餐"}],
        "assumptions_not_allowed": [],
        "confidence": "high",
        "scoreable": True,
        "unscorable_reason": None,
    }


def test_pass_records_and_comparison_flow(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    client = FakeClient(_scored_payload())
    context = _context(client, storage)
    task = _task()

    record_a = run_pass(context, "dev", "main", task, PASS_A)
    assert record_a.ok is True
    assert record_a.annotation is not None
    assert record_a.model == "deepseek-v4-pro"
    assert record_a.model_reported == "deepseek-v4-pro"

    record_b = run_pass(context, "dev", "main", task, PASS_B)
    assert record_b.ok is True
    assert client.prompts[1][0] != client.prompts[0][0]  # different prompt wording

    finals = run_annotation_stage(context, "dev", "main", [task])
    assert finals[task.task_id].scoring_status == "scored"
    assert finals[task.task_id].final is not None


def test_pass_b_prompt_never_contains_pass_a_annotation(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    client = FakeClient(_scored_payload())
    context = _context(client, storage)
    task = _task()
    run_pass(context, "dev", "main", task, PASS_A)
    run_pass(context, "dev", "main", task, PASS_B)
    pass_a_prompt = client.prompts[0][1]
    pass_b_prompt = client.prompts[1][1]
    # The pass A annotation text (its decision action) must not appear inside the
    # pass B request.
    assert "介绍套餐内容" not in pass_b_prompt
    # Both passes receive exactly the same agent-visible prefix.
    assert pass_a_prompt.split("【任务】")[0] == pass_b_prompt.split("【任务】")[0]


def test_cached_record_is_reused_and_stale_key_refetches(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    client = FakeClient(_scored_payload())
    context = _context(client, storage)
    task = _task()
    first = run_pass(context, "dev", "main", task, PASS_A)
    second = run_pass(context, "dev", "main", task, PASS_A)
    assert first.cache_key == second.cache_key
    assert len(client.prompts) == 1  # second run reused the cached record

    stale_storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="other-hash"
    )
    stale_context = _context(client, stale_storage)
    run_pass(stale_context, "dev", "main", task, PASS_A)
    assert len(client.prompts) == 2  # changed task artifact hash forces a refetch


def test_dry_run_makes_no_api_calls_and_writes_nothing(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    client = FakeClient(_scored_payload())
    context = _context(client, storage, dry_run=True)
    task = _task()
    finals = run_annotation_stage(context, "dev", "main", [task])
    assert len(client.prompts) == 0
    assert not storage.finals_path("dev", "main").exists()
    assert not storage.comparisons_path("dev", "main").exists()
    assert finals[task.task_id].scoring_status == "unscored"


def test_adjudication_is_cached(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    payload_a = _scored_payload()
    payload_b = {
        **_scored_payload(),
        "primary_expected_decision": {
            "category": "recommend",
            "action": "推荐一个不同的套餐",
            "reason": "备选",
        },
    }
    payload_adjudication = {
        "outcome": "accept_pass_a",
        "reason": "pass A matches the evidence better",
    }
    client = FakeClient([payload_a, payload_b, payload_adjudication])
    context = _context(client, storage)
    task = _task()
    run_annotation_stage(context, "dev", "main", [task])
    first_calls = len(client.prompts)
    assert first_calls == 3  # pass A, pass B, adjudication
    # Re-running must reuse the passes AND the adjudication.
    run_annotation_stage(context, "dev", "main", [task])
    assert len(client.prompts) == first_calls


def test_failed_record_is_retried_not_reused(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    failing = FakeClient(_scored_payload(), fail=True)
    context = _context(failing, storage)
    task = _task("TASK_retry")
    with pytest.raises(AnnotationError):
        run_pass(context, "dev", "main", task, PASS_A)
    # A failed record was persisted; a retry with a working client must call the
    # API again instead of treating the failure as a cached annotation.
    working = FakeClient(_scored_payload())
    fixed_context = _context(working, storage)
    record = run_pass(fixed_context, "dev", "main", task, PASS_A)
    assert record.ok is True
    assert len(working.prompts) == 1


def test_provider_failure_leaves_tasks_unfinalized(tmp_path) -> None:
    storage = GoldStorage(
        tmp_path, dataset_version="cm_outbound_v2", tasks_artifact_sha256="tasks-hash"
    )
    client = FakeClient(_scored_payload(), fail=True)
    context = _context(client, storage)
    task_a = _task("TASK_a")
    task_b = _task("TASK_b")

    # A batch does not abort on a per-task provider failure: the failed tasks are
    # left without finals so a resume can retry them, and no fabricated success
    # is recorded.
    finals = run_annotation_stage(
        context, "dev", "main", [task_a, task_b], concurrency=1
    )
    assert finals == {}
    assert not storage.finals_path("dev", "main").exists()
    failed_a = storage.record_path("dev", "main", "TASK_a", PASS_A)
    assert failed_a.is_file()
    assert '"parsing_status": "failed"' in failed_a.read_text(encoding="utf-8")
