"""Locate benchmark data both in a source checkout and an installed wheel."""

from __future__ import annotations

from pathlib import Path


def data_path(*parts: str) -> Path:
    """Return a path under the benchmark data directory.

    Source checkouts keep data at the project root. Wheels include the same
    files under ``cm_outbound_bench/data``. The packaged copy is preferred when
    it exists.
    """
    package_data = Path(__file__).resolve().parent / "data"
    project_data = Path(__file__).resolve().parents[2] / "data"
    for base in (package_data, project_data):
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Benchmark data not found: {'/'.join(parts)}")
