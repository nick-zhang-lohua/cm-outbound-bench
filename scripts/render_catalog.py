"""Render the scenario catalogue from the packaged benchmark tasks.

The catalogue is generated from metadata so it can never drift from the tasks,
and it contains opaque evidence identifiers only — never transcript text.
"""

from __future__ import annotations

from pathlib import Path

from cm_outbound_bench.benchmark.domains import DOMAIN_SPECS, all_tasks
from cm_outbound_bench.benchmark.state import StateStore
from cm_outbound_bench.paths import data_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _state_hash(domain_id: str, state_id: str) -> str:
    spec = DOMAIN_SPECS[domain_id]
    store = StateStore.from_file(data_path("synthetic", "states", spec.state_file))
    return store.get(state_id).state_hash


def render() -> str:
    lines = [
        "# Scenario Catalogue",
        "",
        "This catalogue is generated from the packaged benchmark tasks by",
        "`scripts/render_catalog.py`. It contains opaque evidence identifiers only:",
        "no transcript text, and no customer content of any kind.",
        "",
        "## Status semantics",
        "",
        "| Field | Value | Meaning |",
        "|---|---|---|",
        "| `rule_provenance_status` | `OBSERVED_PATTERN` | A corpus rule found this business pattern in the referenced units. |",
        "| | `SYNTHETIC_BENCHMARK_ASSUMPTION` | The dialogue shape is an explicit synthetic benchmark assumption, not an observed transcript. |",
        "| | `SAFETY_STRESS_SCENARIO` | Included because a safety behaviour must be tested even where corpus evidence is ambiguous. |",
        "| `validation_status` | `CANDIDATE` | Not human-adjudicated. All tasks are candidates: no scenario has been human-reviewed yet. |",
        "",
        "Evidence references point at transcript units where the deterministic",
        "classifier found the named pattern. They document *where the phenomenon",
        "appears*, not what was said, and they do not prove official business",
        "policy.",
        "",
        "## Summary",
        "",
        "| Domain | Tasks | Candidate | Human-reviewed |",
        "|---|---:|---:|---:|",
    ]
    tasks = all_tasks()
    for domain_id in sorted(DOMAIN_SPECS):
        domain_tasks = [task for task in tasks if task.domain_id == domain_id]
        candidates = sum(
            1
            for task in domain_tasks
            if task.rule_provenance_status
            in {"OBSERVED_PATTERN", "SYNTHETIC_BENCHMARK_ASSUMPTION"}
        )
        lines.append(
            f"| {domain_id} {DOMAIN_SPECS[domain_id].name} | {len(domain_tasks)} | "
            f"{candidates} | 0 |"
        )
    lines.append(f"| **Total** | **{len(tasks)}** | **{len(tasks)}** | **0** |")

    for domain_id in sorted(DOMAIN_SPECS):
        spec = DOMAIN_SPECS[domain_id]
        domain_tasks = [task for task in tasks if task.domain_id == domain_id]
        lines.extend(
            [
                "",
                f"## {domain_id} — {spec.name}",
                "",
                spec.description,
                "",
            ]
        )
        for task in domain_tasks:
            lines.extend(
                [
                    f"### `{task.task_id}`",
                    "",
                    f"- **Scenario:** `{task.scenario_id}` (version {task.task_version})",
                    f"- **Task version:** {task.task_version}",
                    f"- **Provenance:** `{task.rule_provenance_status}`",
                    "- **Validation:** `CANDIDATE` (no human review yet)",
                    f"- **Initial state:** `{task.initial_state_id}`",
                    f"- **State hash:** `{_state_hash(domain_id, task.initial_state_id)}`",
                    f"- **Target line:** `{task.phone_number_id}`",
                    f"- **Target offer:** `{task.offer_id or '-'}`",
                    f"- **Simulator persona:** `{task.simulator.persona}`",
                    f"- **Allowed tools:** {', '.join(f'`{n}`' for n in task.allowed_tools)}",
                    "- **Required tools:** "
                    + (", ".join(f"`{n}`" for n in task.required_tools) or "none"),
                    "",
                    "**Scenario description.** " + task.description,
                    "",
                ]
            )
            if task.expected_actions:
                lines.append("**Expected final state**")
                lines.append("")
                lines.append("| Collection | Expected | Match |")
                lines.append("|---|---:|---|")
                for expectation in task.expected_actions:
                    match = ", ".join(
                        f"{key}={value}" for key, value in expectation.match.items()
                    )
                    lines.append(
                        f"| `{expectation.collection}` | "
                        f"{expectation.expected_count} | `{match}` |"
                    )
                lines.append("")
            lines.append("**Evidence references**")
            lines.append("")
            lines.append("| Evidence | Transcript | Seq range | Pattern | Provenance |")
            lines.append("|---|---|---|---|---|")
            for reference in task.evidence_refs:
                seq_range = (
                    f"{reference.seq_start}–{reference.seq_end}"
                    if reference.seq_start is not None
                    and reference.seq_end is not None
                    else "-"
                )
                lines.append(
                    f"| `{reference.evidence_id}` | `{reference.transcript_id}` | "
                    f"{seq_range} | `{reference.pattern_type}` | "
                    f"`{reference.provenance_status}` |"
                )
            lines.append("")

    lines.extend(
        [
            "## Explicit limitations",
            "",
            "1. No scenario has been human-reviewed. Every entry is a candidate.",
            "2. Evidence references are aggregate rule hits, not semantic proof.",
            "3. Corpus speaker roles are `UNKNOWN`; the simulated customer is a",
            "   synthetic construct and never a transcript reconstruction.",
            "4. Business rules used by the tasks are synthetic benchmark assumptions",
            "   unless marked `AUTHORITATIVE_POLICY_VERIFIED` (none are, currently).",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    output = PROJECT_ROOT / "docs" / "scenario_catalog.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(), encoding="utf-8")
    print(f"written={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
