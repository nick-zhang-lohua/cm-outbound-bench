"""Legacy Simulator and synthetic-benchmark isolation.

The real-transcript decision benchmark must stay independent of the legacy
τ²-inspired runtime. This module proves it twice over:

* **statically** — an AST scan of every module in ``decisions/`` and ``transcripts/``
  shows no import of the simulator, the synthetic task loader, the synthetic
  toolkit, or the legacy agent/runner;
* **at runtime** — a clean interpreter that executes the whole real pipeline never
  loads a simulator module.

The point is not style. A simulator import would let a synthetic customer turn or a
scripted state leak into a benchmark that claims to evaluate real conversations.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cm_outbound_bench.decisions.builder import DecisionTaskBuilder
from cm_outbound_bench.decisions.config import DatasetConfig
from cm_outbound_bench.decisions.store import TaskStore
from decision_support import (
    dialogue,
    distinct_label,
    load_calls,
    write_corpus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "cm_outbound_bench"

#: Module prefixes the real decision benchmark must never import.
FORBIDDEN_MODULE_PREFIXES: tuple[str, ...] = (
    "cm_outbound_bench.simulator",
    "cm_outbound_bench.benchmark.simulator",
    "cm_outbound_bench.benchmark.state",
    "cm_outbound_bench.benchmark.toolkits",
    "cm_outbound_bench.benchmark.demo_agent",
    "cm_outbound_bench.benchmark.tasks",
    "cm_outbound_bench.benchmark.execution",
    "cm_outbound_bench.benchmark.replay",
    "cm_outbound_bench.tasks",
    "cm_outbound_bench.llm_agent",
    "cm_outbound_bench.environment",
    "cm_outbound_bench.runner",
    "cm_outbound_bench.agent",
    "cm_outbound_bench.evaluator",
)

DEPENDENT_PACKAGES: tuple[str, ...] = ("decisions", "transcripts")


def _modules(package: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / package).rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module)
    return found


@pytest.mark.parametrize("package", DEPENDENT_PACKAGES)
def test_no_real_benchmark_module_imports_the_legacy_runtime(package: str) -> None:
    offending: list[str] = []
    for path in _modules(package):
        for module in _imported_modules(path):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_MODULE_PREFIXES
            ):
                offending.append(f"{path.name} -> {module}")
    assert offending == [], (
        "the real decision benchmark must not import the legacy synthetic runtime: "
        + ", ".join(offending)
    )


def test_no_real_benchmark_module_imports_a_flowchart_or_rsi_module() -> None:
    """Catches a lazy import hidden behind a string, and any flowchart dependency.

    Prose is allowed: the modules legitimately *describe* that they contain no
    flowchart ("Group A only: no flowchart, no workflow"). What is forbidden is an
    import or a dynamic module load of a flowchart, RSI or simulator module.
    """
    forbidden_fragments = ("flowchart", "rsi", "simulator", "synthetic")
    offending: list[str] = []
    for package in DEPENDENT_PACKAGES:
        for path in _modules(package):
            for module in _imported_modules(path):
                lowered = module.casefold()
                if any(fragment in lowered for fragment in forbidden_fragments):
                    offending.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
            dynamic = _dynamic_import_targets(path)
            for target in dynamic:
                lowered = target.casefold()
                if any(fragment in lowered for fragment in forbidden_fragments):
                    offending.append(
                        f"{path.relative_to(PACKAGE_ROOT)} -> dynamic:{target}"
                    )
    assert offending == [], (
        "no flowchart, RSI or simulator module may be imported by the Group A "
        "pipeline: " + ", ".join(offending)
    )


def _dynamic_import_targets(path: Path) -> set[str]:
    """Return string literals passed to importlib.import_module or __import__."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
            name = node.func.attr
        elif isinstance(node.func, ast.Name) and node.func.id == "__import__":
            name = node.func.id
        if not name or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            targets.add(first.value)
    return targets


def test_a_clean_interpreter_runs_the_real_pipeline_without_a_simulator(
    tmp_path: Path,
) -> None:
    corpus = write_corpus(
        tmp_path / "corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": f"CALL_FIXTURE_{index:02d}",
                "duration_ms": 20_000,
                "records": dialogue(
                    [
                        (
                            1 if position % 2 == 0 else 2,
                            f"通话{distinct_label(index)}"
                            f"{'客服' if position % 2 == 0 else '客户'}之{position + 1}",
                        )
                        for position in range(6)
                    ]
                ),
            }
            for index in range(3)
        ],
    )
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(PROJECT_ROOT / "src")!r})
        from pathlib import Path
        from cm_outbound_bench.decisions.builder import DecisionTaskBuilder
        from cm_outbound_bench.decisions.agent_prompt import build_agent_request
        from cm_outbound_bench.transcripts.ingestion import iter_units
        from cm_outbound_bench.transcripts.reconstruction import reconstruct_calls

        calls = list(
            reconstruct_calls(
                iter_units(Path({str(corpus)!r})),
                corpus="fixture",
                corpus_schema_version="1.0",
            )
        )
        builder = DecisionTaskBuilder()
        pairs = []
        for call in calls:
            pairs.extend(builder.add_call(call, split="dev"))
        assert pairs, "the fixture must produce at least one decision point"
        public, _ = pairs[0]
        request = build_agent_request(public)
        assert request.system_prompt and request.user_prompt

        forbidden = [
            name
            for name in sys.modules
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in {FORBIDDEN_MODULE_PREFIXES!r}
            )
        ]
        print("FORBIDDEN=" + ",".join(sorted(forbidden)))
        print("TASKS=%d" % len(pairs))
        """
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    lines = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    assert lines["FORBIDDEN"] == "", (
        "a simulator or synthetic module was imported: " + lines["FORBIDDEN"]
    )
    assert int(lines["TASKS"]) > 0


def test_the_builder_produces_real_tasks_without_any_synthetic_state(
    tmp_path: Path,
) -> None:
    corpus = write_corpus(
        tmp_path / "corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": "CALL_FIXTURE_REAL",
                "duration_ms": 20_000,
                "records": dialogue(
                    [
                        (1, "占位客服一"),
                        (2, "占位客户一"),
                        (1, "占位客服二"),
                        (2, "占位客户二"),
                        (1, "占位客服三"),
                        (2, "占位客户三"),
                        (1, "占位客服四"),
                    ]
                ),
            }
        ],
    )
    calls = load_calls(corpus)
    builder = DecisionTaskBuilder()
    pairs = builder.add_call(calls[0], split="dev")
    assert pairs
    public, reference = pairs[0]
    assert public.visible_context
    assert public.business_domain == "unknown"
    assert reference.historical_next_action_reference
    assert reference.historical_next_action_authority == "observed_reference_only"
    # A simulator would generate a customer reply; here every visible turn is a
    # verbatim prefix of the real conversation.
    real_text = [turn.text for turn in calls[0].speech_turns]
    assert [turn.content for turn in public.visible_context] == real_text[
        : len(public.visible_context)
    ]


def test_the_dataset_config_has_no_simulator_switch() -> None:
    fields = set(DatasetConfig.model_fields)
    assert "simulator" not in " ".join(fields).lower()
    assert "synthetic" not in " ".join(fields).lower()
    assert {"artifact_root", "outputs_root", "task_construction"} <= fields


def test_the_task_store_has_no_synthetic_task_loader() -> None:
    assert not hasattr(TaskStore, "load_synthetic")
    assert not hasattr(TaskStore, "synthetic")
    assert {
        "agent_visible_payload",
        "require_reference",
        "task_ids_for_split",
    } <= set(dir(TaskStore))
