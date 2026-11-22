from __future__ import annotations

import tomllib
from pathlib import Path

from cm_outbound_bench.benchmark.domains import DOMAIN_SPECS
from cm_outbound_bench.paths import data_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_data_resolves_from_the_package_or_project():
    assert data_path("policy", "marketing.md").is_file()
    assert data_path("policy", "core.md").is_file()
    assert data_path("synthetic", "db.json").is_file()
    assert data_path("tasks", "marketing.json").is_file()


def test_every_domain_asset_exists_where_the_registry_expects_it():
    for spec in DOMAIN_SPECS.values():
        assert data_path("tasks", spec.task_file).is_file(), spec.task_file
        assert data_path("synthetic", "states", spec.state_file).is_file()
        assert data_path("policy", spec.policy_file).is_file()


def test_pyproject_packages_every_runtime_data_directory():
    pyproject = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]
    for directory in ("data/synthetic", "data/tasks", "data/policy"):
        assert directory in force_include, directory
        assert force_include[directory].startswith("cm_outbound_bench/data/")


def test_packaged_data_does_not_include_the_corpus_database():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "corpus_db" not in pyproject
    assert ".sqlite" not in pyproject


def test_states_are_declared_data_not_code():
    state_files = sorted(data_path("synthetic", "states").glob("*.json"))
    assert len(state_files) == len(DOMAIN_SPECS)
    for path in state_files:
        assert path.suffix == ".json"
