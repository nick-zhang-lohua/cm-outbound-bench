from __future__ import annotations

from pathlib import Path

import pytest

from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.policy import load_marketing_policy
from cm_outbound_bench.tasks import MarketingTask, load_marketing_tasks
from corpus_support import build_corpus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "synthetic" / "db.json"


@pytest.fixture
def database() -> CmOutboundDB:
    return CmOutboundDB.load(DB_PATH)


@pytest.fixture
def marketing_tasks(database: CmOutboundDB) -> list[MarketingTask]:
    return load_marketing_tasks(database)


@pytest.fixture
def policy() -> str:
    return load_marketing_policy()


@pytest.fixture
def synthetic_corpus(tmp_path: Path) -> Path:
    """A tiny synthetic corpus directory with a valid manifest."""
    return build_corpus(tmp_path / "corpus")


@pytest.fixture
def corpus_database(tmp_path: Path, synthetic_corpus: Path) -> Path:
    """An imported corpus database for query tests."""
    from cm_outbound_bench.corpus.importer import import_corpus

    target = tmp_path / "corpus.sqlite"
    import_corpus(database_path=target, corpus_dir=synthetic_corpus)
    return target
