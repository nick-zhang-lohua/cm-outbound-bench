"""Command-line entry point for the marketing benchmark."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from cm_outbound_bench.agent import Agent, DemoAgent
from cm_outbound_bench.benchmark import cli as benchmark_cli
from cm_outbound_bench.corpus import cli as corpus_cli
from cm_outbound_bench.decisions import cli as decision_cli
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.evaluator import EvaluationResult, evaluate_task
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.paths import data_path
from cm_outbound_bench.policy import load_marketing_policy
from cm_outbound_bench.providers import (
    ProviderError,
    create_provider_client,
    default_provider_config_path,
    load_provider_profile,
    resolve_provider_profile,
)
from cm_outbound_bench.runner import MarketingRunner
from cm_outbound_bench.tasks import (
    MarketingTask,
    find_task,
    load_marketing_tasks,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "corpus":
        return corpus_cli.dispatch(args)
    if args.command == "benchmark":
        return benchmark_cli.dispatch(args)
    if args.command == "decision":
        return decision_cli.dispatch(args)
    try:
        db = CmOutboundDB.load(data_path("synthetic", "db.json"))
        tasks = load_marketing_tasks(db)
        policy = load_marketing_policy()
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.command == "list-tasks":
        for task in tasks:
            print(f"{task.task_id}\t{task.agent_objective}")
        return 0

    try:
        agent_factory = _build_agent_factory(args)
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    selected = tasks if args.command == "run-all" else _select_task(args.task, tasks)
    if selected is None:
        return 2

    passed = 0
    for task in selected:
        try:
            evaluation = _run_one(task, db, policy, agent_factory)
        except ProviderError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        _print_evaluation(evaluation)
        passed += int(evaluation.success)
        print()

    if args.command == "run-all":
        print(f"Overall: {passed}/{len(selected)} tasks passed")
    return 0 if passed == len(selected) else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cm-outbound",
        description=(
            "China Mobile synthetic outbound benchmark and de-identified corpus "
            "database tooling."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    corpus_cli.add_parser(subparsers)
    benchmark_cli.add_parser(subparsers)
    decision_cli.add_parser(subparsers)

    subparsers.add_parser(
        "list-tasks",
        help="List available marketing tasks.",
    )

    run_parser = subparsers.add_parser("run", help="Run one task.")
    run_parser.add_argument("--task", required=True, help="Marketing task ID.")
    _add_agent_options(run_parser)

    run_all_parser = subparsers.add_parser(
        "run-all",
        help="Run every marketing task.",
    )
    _add_agent_options(run_all_parser)
    return parser


def _add_agent_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        default="demo",
        choices=["demo", "llm"],
        help="Agent implementation.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Arbitrary provider profile label from the provider config file. "
            "Required with --agent llm."
        ),
    )
    parser.add_argument(
        "--provider-config",
        type=Path,
        default=None,
        help=(
            "Path to a local provider TOML profile file. Defaults to "
            "CM_OUTBOUND_PROVIDER_CONFIG or providers.toml."
        ),
    )


def _build_agent_factory(args: argparse.Namespace) -> Callable[[], Agent]:
    if args.agent == "demo":
        return DemoAgent
    if not args.provider:
        raise ProviderError(
            "provider_required",
            "Use --provider with --agent llm.",
        )

    config_path = args.provider_config or default_provider_config_path()
    profile = load_provider_profile(config_path, args.provider)
    resolved = resolve_provider_profile(profile)

    def factory() -> Agent:
        return LLMBackedAgent(create_provider_client(resolved))

    return factory


def _select_task(
    task_id: str, tasks: list[MarketingTask]
) -> list[MarketingTask] | None:
    try:
        return [find_task(task_id, tasks)]
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return None


def _run_one(
    task: MarketingTask,
    db: CmOutboundDB,
    policy: str,
    agent_factory: Callable[[], Agent],
) -> EvaluationResult:
    runner = MarketingRunner(
        initial_db=db,
        policy=policy,
        agent_factory=agent_factory,
    )
    run = runner.run(task)
    return evaluate_task(task, run)


def _print_evaluation(evaluation: EvaluationResult) -> None:
    result = "PASS" if evaluation.success else "FAIL"
    violations = (
        ", ".join(evaluation.policy_violations)
        if evaluation.policy_violations
        else "none"
    )
    print(f"Task: {evaluation.task_id}")
    print(f"Result: {result}")
    print(f"Turns: {evaluation.conversation_turn_count}")
    print(f"Tool calls: {evaluation.tool_call_count}")
    print(f"Policy violations: {violations}")
    print(f"Summary: {evaluation.summary}")


if __name__ == "__main__":
    raise SystemExit(main())
