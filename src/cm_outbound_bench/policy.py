"""Load the marketing policy that is shown to agents."""

from __future__ import annotations

from pathlib import Path

from cm_outbound_bench.paths import data_path


def load_marketing_policy(path: str | Path | None = None) -> str:
    """Return the Markdown policy text."""
    policy_path = (
        Path(path) if path is not None else data_path("policy", "marketing.md")
    )
    return policy_path.read_text(encoding="utf-8")
