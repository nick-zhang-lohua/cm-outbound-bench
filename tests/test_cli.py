from pathlib import Path

from cm_outbound_bench.cli import _build_agent_factory, _build_parser, main
from cm_outbound_bench.llm_agent import LLMBackedAgent


def test_cli_accepts_arbitrary_provider_label_and_builds_llm_agent(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "providers.toml"
    config_path.write_text(
        """
[providers.my-relay]
api_protocol = "openai_compatible"
base_url = "https://relay.example.test/v1"
api_key_env = "MY_RELAY_KEY"
model = "relay-model"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_RELAY_KEY", "sk-test-relay-key")

    args = _build_parser().parse_args(
        [
            "run",
            "--task",
            "marketing_direct_refusal",
            "--agent",
            "llm",
            "--provider",
            "my-relay",
            "--provider-config",
            str(config_path),
        ]
    )
    factory = _build_agent_factory(args)
    assert isinstance(factory(), LLMBackedAgent)


def test_cli_requires_provider_for_llm_agent(capsys):
    result = main(
        [
            "run",
            "--task",
            "marketing_direct_refusal",
            "--agent",
            "llm",
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "provider_required" in captured.err
    assert "sk-" not in captured.err


def test_cli_demo_path_is_unchanged(capsys):
    result = main(
        [
            "run",
            "--task",
            "marketing_direct_refusal",
            "--agent",
            "demo",
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "Task: marketing_direct_refusal" in captured.out
    assert "Result: PASS" in captured.out
    assert "Overall" not in captured.out
