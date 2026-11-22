"""Emit the frozen release configuration and every component hash.

The report generator, the release dossier and a reviewer all need the same
answer to "which exact components produced this result?". This script computes
that answer from the installed catalogue instead of copying values out of a
document::

    python scripts/release_config.py --output docs/development/release_config.json

It never reads an API key. Provider profiles are summarised by label, protocol,
model and the public sampling parameters only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from cm_outbound_bench.benchmark.domains import (  # noqa: E402
    DOMAIN_SPECS,
    all_tasks,
    domain_ids,
    policy_for_domain,
)
from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION  # noqa: E402
from cm_outbound_bench.benchmark.metrics import MANIFEST_VERSION  # noqa: E402
from cm_outbound_bench.benchmark.toolkits import tool_surface_hash  # noqa: E402
from cm_outbound_bench.benchmark.trace import TRACE_SCHEMA_VERSION  # noqa: E402
from cm_outbound_bench.evaluator import (  # noqa: E402
    EVALUATOR_VERSION,
    evaluator_hash,
)
from cm_outbound_bench.llm_agent import (  # noqa: E402
    PROMPT_VERSION,
    build_system_prompt,
)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provider_summary() -> dict[str, object]:
    """Return a secret-free summary of the local provider profiles."""
    path = PROJECT_ROOT / "providers.toml"
    if not path.is_file():
        return {"config_present": False, "profiles": {}}
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return {"config_present": True, "profiles": {}, "note": "tomllib unavailable"}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    profiles: dict[str, object] = {}
    for label, profile in sorted((data.get("providers") or {}).items()):
        profiles[label] = {
            "api_protocol": profile.get("api_protocol", ""),
            "model": profile.get("model", ""),
            "tool_mode": profile.get("tool_mode", ""),
            "timeout": profile.get("timeout"),
            "max_retries": profile.get("max_retries"),
            "extra_params": profile.get("extra_params", {}),
            # The credential itself is never recorded; only the env-var name.
            "api_key_env": profile.get("api_key_env", ""),
        }
    return {"config_present": True, "profiles": profiles}


def build_config() -> dict[str, object]:
    tasks = all_tasks()
    task_entries: list[dict[str, object]] = []
    for task in tasks:
        policy = policy_for_domain(task.domain_id)
        prompt = build_system_prompt(task.agent_view(task.domain_id.lower()), policy)
        task_entries.append(
            {
                "task_id": task.task_id,
                "domain_id": task.domain_id,
                "scenario_id": task.scenario_id,
                "task_version": task.task_version,
                "task_hash": task.task_hash(),
                "scenario_status": task.scenario_status,
                "rule_provenance_status": task.rule_provenance_status,
                "initial_state_id": task.initial_state_id,
                "prompt_version": PROMPT_VERSION,
                "prompt_hash": hash_text(prompt),
                "policy_hash": hash_text(policy),
                "initial_tools_hash": tool_surface_hash(task.allowed_tools),
                "allowed_tools": list(task.allowed_tools),
                "required_tools": list(task.required_tools),
            }
        )
    policies = {
        domain_id: hash_text(policy_for_domain(domain_id))
        for domain_id in domain_ids()
    }
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "manifest_schema_version": MANIFEST_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_hash": evaluator_hash(),
        "prompt_version": PROMPT_VERSION,
        "task_count": len(task_entries),
        "domain_count": len(DOMAIN_SPECS),
        "domain_ids": domain_ids(),
        "domain_policy_hashes": policies,
        "tasks": task_entries,
        "provider": provider_summary(),
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "docs" / "development" / "release_config.json",
    )
    args = parser.parse_args()
    config = build_config()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"benchmark={config['benchmark_version']}")
    print(f"evaluator={config['evaluator_version']} {config['evaluator_hash']}")
    print(f"prompt={config['prompt_version']}")
    print(f"tasks={config['task_count']} domains={config['domain_count']}")
    print(f"written={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
