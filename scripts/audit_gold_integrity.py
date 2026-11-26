"""Offline integrity audit of the Milestone 1.2 LLM Gold artefacts.

This script never calls an API. It re-derives everything it can from the frozen
inputs and fails loudly when an artefact does not match them:

* every saved annotation pass is re-hashed from the frozen task prefix and the
  frozen prompt file, so a record can only verify if it was produced from
  exactly this dataset and this prompt wording;
* every frozen Gold Label is checked for the required provenance, the
  ``policy_verified = false`` invariant and in-range evidence references;
* sample sizes, task-id uniqueness and split consistency are re-derived from the
  actual files rather than trusted from a manifest;
* the frozen Ground Truth hashes are re-computed and compared with the manifest.

Usage::

    python scripts/audit_gold_integrity.py [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cm_outbound_bench.decisions.config import load_dataset_config  # noqa: E402
from cm_outbound_bench.decisions.gold.models import PassRecord  # noqa: E402
from cm_outbound_bench.decisions.gold.prompts import (  # noqa: E402
    ANNOTATION_PASS_A,
    ANNOTATION_PASS_B,
    build_annotation_messages,
    prompt_sha256,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference  # noqa: E402
from cm_outbound_bench.decisions.store import TaskStore  # noqa: E402

ANNOTATIONS = ROOT / "outputs" / "decision" / "annotations"
STAGES = (
    ("train_r", "pilot"),
    ("train_r", "feedback"),
    ("dev", "main"),
)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def audit() -> dict[str, object]:
    problems: list[str] = []
    config = load_dataset_config()
    from cm_outbound_bench.decisions import paths

    store = TaskStore.load(paths.tasks_dir(config))

    # 1. Prompt-hash replay for every saved annotation pass.
    pass_checked = 0
    pass_mismatch: list[str] = []
    for split, stage in STAGES:
        for pass_id, prompt_name in (
            ("a", ANNOTATION_PASS_A),
            ("b", ANNOTATION_PASS_B),
        ):
            directory = ANNOTATIONS / split / stage / "records" / f"pass_{pass_id}"
            for path in sorted(directory.glob("*.json")):
                record = PassRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                try:
                    task = store.load_public_task(record.task_id)
                except Exception:
                    pass_mismatch.append(f"{record.task_id}: task not in store")
                    continue
                system, user = build_annotation_messages(
                    task,
                    prompt_name=prompt_name,
                    prompt_version=record.prompt_version,
                )
                expected = prompt_sha256(system + "\n\u0000\n" + user)
                pass_checked += 1
                if expected != record.prompt_hash:
                    pass_mismatch.append(f"{record.task_id}/{pass_id}: prompt hash")
                if record.model != "deepseek-v4-pro":
                    pass_mismatch.append(
                        f"{record.task_id}/{pass_id}: model {record.model!r}"
                    )
    if pass_mismatch:
        problems.extend(pass_mismatch[:10])

    # 2. Frozen gold references.
    frozen = json.loads(
        (ANNOTATIONS / "manifests" / "frozen_annotation_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    reference_report: dict[str, object] = {}
    for split in ("train_r", "dev"):
        path = ANNOTATIONS / split / "final_references.jsonl"
        rows = [
            PrivateTaskReference.model_validate(row) for row in _load_jsonl(path)
        ]
        ids = [row.task_id for row in rows]
        scored = [row for row in rows if row.scoring_status == "scored"]
        answerability: dict[str, int] = {}
        for row in rows:
            answerability[row.task_answerability] = (
                answerability.get(row.task_answerability, 0) + 1
            )
        evidence_out_of_range = 0
        for row in rows:
            task = store.load_public_task(row.task_id)
            visible = len(task.visible_context)
            for ref in row.evidence_refs:
                if not ref.startswith("visible_turn:"):
                    problems.append(f"{row.task_id}: malformed evidence ref {ref!r}")
                    continue
                turn = int(ref.split(":", 1)[1])
                if turn < 1 or turn > visible:
                    evidence_out_of_range += 1
                    problems.append(
                        f"{row.task_id}: evidence turn {turn} outside 1..{visible}"
                    )
            if row.policy_verified:
                problems.append(f"{row.task_id}: policy_verified is true")
            if row.ground_truth_type != "llm_generated_gold":
                problems.append(f"{row.task_id}: ground_truth_type")
            if row.ground_truth_source != "deepseek_v4_pro":
                problems.append(f"{row.task_id}: ground_truth_source")
            if row.ground_truth_status != "frozen":
                problems.append(f"{row.task_id}: ground_truth_status")
            if row.annotation_status != "accepted_as_benchmark_gold":
                problems.append(f"{row.task_id}: annotation_status")
            if row.split != split:
                problems.append(f"{row.task_id}: split {row.split!r} != {split!r}")
            if row.scoring_status == "scored":
                if row.primary_expected_decision is None and not row.required_information:
                    problems.append(f"{row.task_id}: scored but no decision/info")
                if not row.is_scored:
                    problems.append(f"{row.task_id}: scored but !is_scored")
            else:
                if row.primary_expected_decision is not None:
                    problems.append(f"{row.task_id}: unscored but has a decision")
                if not row.unscorable_reason:
                    problems.append(f"{row.task_id}: unscored without a reason")
        expected = frozen[split]
        reference_report[split] = {
            "tasks": len(rows),
            "unique_task_ids": len(set(ids)),
            "scored": len(scored),
            "unscored": len(rows) - len(scored),
            "manifest_scored": expected.get("scored"),
            "manifest_unscored": expected.get("unscored"),
            "sha256": _hash_file(path),
            "manifest_sha256": expected.get("sha256"),
            "hash_matches": _hash_file(path) == expected.get("sha256"),
            "counts_match_manifest": (
                len(rows) == expected.get("tasks")
                and len(scored) == expected.get("scored")
            ),
            "answerability_distribution": dict(sorted(answerability.items())),
            "evidence_ref_out_of_range": evidence_out_of_range,
        }
        if len(set(ids)) != len(ids):
            problems.append(f"{split}: duplicate task ids")
        if not reference_report[split]["hash_matches"]:
            problems.append(f"{split}: frozen hash does not match the manifest")
        if not reference_report[split]["counts_match_manifest"]:
            problems.append(f"{split}: counts do not match the manifest")

    # 3. Dev references must be exactly the 400 Group A traces.
    trace_dir = ROOT / "outputs" / "decision" / "traces" / "20261125T164504Z-group-a-dev-eval"
    trace_ids = {
        json.loads(path.read_text(encoding="utf-8"))["task_id"]
        for path in trace_dir.glob("RUN-*.json")
    }
    dev_ids = {
        row["task_id"]
        for row in _load_jsonl(ANNOTATIONS / "dev" / "final_references.jsonl")
    }
    traces = {
        "traces": len(trace_ids),
        "dev_references": len(dev_ids),
        "identical": trace_ids == dev_ids,
    }
    if trace_ids != dev_ids:
        problems.append("dev references do not match the Group A traces")

    return {
        "ok": not problems,
        "prompt_hash_replay": {
            "pass_records_checked": pass_checked,
            "mismatches": pass_mismatch,
        },
        "references": reference_report,
        "dev_vs_group_a_traces": traces,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ok: {report['ok']}")
        print(f"pass records re-hashed: {report['prompt_hash_replay']['pass_records_checked']}")
        for split, detail in report["references"].items():
            print(
                f"{split}: tasks={detail['tasks']} scored={detail['scored']} "
                f"unscored={detail['unscored']} hash_matches={detail['hash_matches']}"
            )
        print(f"dev == group A traces: {report['dev_vs_group_a_traces']['identical']}")
        if report["problems"]:
            print("problems:")
            for problem in report["problems"][:20]:
                print(f"  - {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
