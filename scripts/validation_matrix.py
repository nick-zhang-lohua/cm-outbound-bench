"""Build the release validation matrix from persisted run manifests.

Reads one or more run directories (non-recursively, ``RUN-*.json``), verifies
each manifest's trace link and SHA-256, and emits a Markdown table plus a JSON
matrix suitable for the release-readiness report.

Usage::

    python scripts/validation_matrix.py \
        --dir outputs/live_llm_validation/deepseek/final-.../D01 \
        --dir outputs/live_llm_validation/deepseek/final-.../D02 \
        --md-out docs/reports/validation_matrix.md \
        --json-out docs/reports/validation_matrix.json

The script never re-runs a task and never rewrites a run directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from cm_outbound_bench.benchmark.metrics import load_manifests  # noqa: E402
from cm_outbound_bench.benchmark.trace import trace_file_sha256  # noqa: E402


def _answer_rate(manifest) -> float | None:
    results = list(manifest.answer_fact_results or [])
    if not results:
        return None
    passed = sum(1 for item in results if item.get("passed"))
    return round(passed / len(results), 4)


def _trace_integrity(manifest, directory: Path) -> tuple[bool, str]:
    if not manifest.trace_path:
        return False, ""
    trace_path = directory / manifest.trace_path
    if not trace_path.is_file():
        return False, ""
    actual = trace_file_sha256(trace_path)
    return actual == manifest.trace_sha256, actual


def build_rows(directories: list[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for directory in directories:
        for manifest in load_manifests(directory):
            ok, trace_hash = _trace_integrity(manifest, directory)
            rows.append(
                {
                    "run_dir": str(directory),
                    "run_id": manifest.run_id,
                    "domain_id": manifest.domain_id,
                    "task_id": manifest.task_id,
                    "task_version": manifest.task_version,
                    "task_hash": manifest.task_hash,
                    "scenario_id": manifest.scenario_id,
                    "scenario_status": manifest.scenario_status,
                    "rule_provenance_status": manifest.rule_provenance_status,
                    "initial_state_id": manifest.initial_state_id,
                    "initial_state_hash": manifest.initial_state_hash,
                    "initial_tools_hash": manifest.initial_tools_hash,
                    "prompt_version": manifest.prompt_version,
                    "prompt_hash": manifest.prompt_hash,
                    "policy_hash": manifest.policy_hash,
                    "evaluator_version": manifest.evaluator_version,
                    "evaluator_hash": manifest.evaluator_hash,
                    "model_name": manifest.model_name,
                    "provider_name": manifest.provider_name,
                    "run_status": manifest.run_status,
                    "task_success": manifest.task_success,
                    "completion_reason": manifest.completion_reason,
                    "turn_count": manifest.turn_count,
                    "tool_call_count": manifest.tool_call_count,
                    "latency_ms": manifest.latency_ms,
                    "episode_ms": manifest.episode_ms,
                    "total_ms": manifest.total_ms,
                    "token_usage": dict(manifest.token_usage),
                    "answer_fact_rate": _answer_rate(manifest),
                    "policy_violations": list(manifest.policy_violations),
                    "failed_checks": [
                        item["name"]
                        for item in manifest.check_results
                        if not item.get("passed")
                    ],
                    "dimensions": dict(manifest.dimensions),
                    "final_state_hash": manifest.final_state_hash,
                    "state_matches_initial": (
                        manifest.final_state_hash == manifest.initial_state_hash
                    ),
                    "trace_path": manifest.trace_path,
                    "trace_sha256": manifest.trace_sha256,
                    "trace_file_sha256": trace_hash,
                    "trace_integrity_ok": ok,
                    "trace_schema_version": manifest.trace_schema_version,
                    "random_seed": manifest.random_seed,
                    "repeat_index": manifest.repeat_index,
                    "repeat_count": manifest.repeat_count,
                }
            )
    rows.sort(key=lambda row: (str(row["domain_id"]), str(row["task_id"]), str(row["run_id"])))
    return rows


def round_provenance(directories: list[Path]) -> dict[str, object]:
    """Return round identity, seeds and manifest execution window.

    A round directory name is only an output-directory identifier. The seed and
    the execution time are read from the manifests, never inferred from the name.
    """
    rounds: set[str] = set()
    seeds: set[int] = set()
    starts: list[str] = []
    ends: list[str] = []
    for directory in directories:
        try:
            rounds.add(str(directory.resolve().parent.relative_to(PROJECT_ROOT)))
        except ValueError:  # outside the checkout: keep the absolute parent
            rounds.add(str(directory.resolve().parent))
        for manifest in load_manifests(directory):
            if manifest.random_seed is not None:
                seeds.add(manifest.random_seed)
            if manifest.started_at:
                starts.append(manifest.started_at)
            if manifest.completed_at:
                ends.append(manifest.completed_at)
    return {
        "rounds": sorted(rounds),
        "seeds": sorted(seeds),
        "started": min(starts) if starts else None,
        "completed": max(ends) if ends else None,
    }


def render_markdown(
    rows: list[dict[str, object]], provenance: dict[str, object] | None = None
) -> str:
    lines = [
        "# Live Validation Matrix",
        "",
        "Every row is one persisted episode. `Trace OK` means the manifest's",
        "`trace_sha256` equals the SHA-256 of the trace file on disk. `State` is",
        "`unchanged` when the final state hash equals the initial state hash and",
        "`changed` otherwise; a mutation task is expected to change it.",
    ]
    if provenance:
        seeds = ", ".join(f"`{seed}`" for seed in provenance["seeds"]) or "none recorded"
        rounds = ", ".join(f"`{item}`" for item in provenance["rounds"]) or "unknown"
        lines += [
            "",
            "## Provenance",
            "",
            f"* Output directory identifier(s): {rounds}.",
            f"* Random seed(s): {seeds} (`random_seed` and `sampling.seed` in "
            "every manifest).",
            "* Actual execution window (manifest `started_at` to `completed_at`): "
            f"`{provenance['started']}` to `{provenance['completed']}`.",
            "* A numeric token in a round directory name is a run label or a "
            "random seed, **not** an execution date. Execution time is read from "
            "the manifest, never inferred from the directory name.",
        ]
    lines += [
        "",
        "| Domain | Task | Ver | Status | Turns | Tools | Latency ms | Tokens | "
        "Answer facts | State | Violations | Trace OK | Run ID |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|",
    ]
    for row in rows:
        tokens = row["token_usage"] or {}
        answer = row["answer_fact_rate"]
        answer_text = "n/a" if answer is None else f"{answer:.2f}"
        lines.append(
            "| {domain} | `{task}` | {version} | **{status}** | {turns} | {tools} | "
            "{latency} | {tokens} | {answer} | {state} | {violations} | {trace} | "
            "`{run_id}` |".format(
                domain=row["domain_id"],
                task=row["task_id"],
                version=row["task_version"],
                status=row["run_status"],
                turns=row["turn_count"],
                tools=row["tool_call_count"],
                latency=row["latency_ms"],
                tokens=tokens.get("total_tokens", "n/a"),
                answer=answer_text,
                state="unchanged" if row["state_matches_initial"] else "changed",
                violations=", ".join(row["policy_violations"]) or "none",
                trace="yes" if row["trace_integrity_ok"] else "**NO**",
                run_id=row["run_id"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", action="append", required=True, type=Path)
    parser.add_argument("--md-out", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    missing = [str(item) for item in args.dir if not item.is_dir()]
    if missing:
        print(f"error: not a directory: {', '.join(missing)}", file=sys.stderr)
        return 2

    rows = build_rows(list(args.dir))
    if not rows:
        print("error: no manifests found", file=sys.stderr)
        return 2
    markdown = render_markdown(rows, round_provenance(list(args.dir)))
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(markdown, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(markdown)
    bad = [row for row in rows if row["run_status"] != "success"]
    print(f"rows={len(rows)} non_success={len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
