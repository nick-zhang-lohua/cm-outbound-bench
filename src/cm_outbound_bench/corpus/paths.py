"""Resolve corpus research paths without hard-coding a single machine layout.

The corpus database and the de-identified corpus are research artefacts. They
must never be packaged into the distributable wheel, so they live beside the
project checkout instead of inside ``cm_outbound_bench/data``.

Resolution order for every path:

1. explicit command-line value;
2. ``CM_OUTBOUND_CORPUS_DB`` / ``CM_OUTBOUND_CORPUS_DIR``;
3. ``CM_OUTBOUND_DATA_ROOT`` + a conventional subdirectory;
4. the sibling ``data`` directory of the project checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

DATABASE_ENV = "CM_OUTBOUND_CORPUS_DB"
CORPUS_DIR_ENV = "CM_OUTBOUND_CORPUS_DIR"
DATA_ROOT_ENV = "CM_OUTBOUND_DATA_ROOT"

DATABASE_FILENAME = "cm_outbound_corpus.sqlite"


def project_root() -> Path:
    """Return the project checkout root that contains ``src`` and ``tests``."""
    return _PROJECT_ROOT


def data_root() -> Path:
    """Return the shared research data root."""
    override = os.environ.get(DATA_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return _PROJECT_ROOT.parent / "data"


def default_corpus_dir() -> Path:
    """Return the directory holding the final de-identified corpus."""
    override = os.environ.get(CORPUS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return data_root() / "deidentified" / "cm_outbound_v2"


def default_database_path() -> Path:
    """Return the default corpus SQLite location."""
    override = os.environ.get(DATABASE_ENV)
    if override:
        return Path(override).expanduser()
    return data_root() / "corpus_db" / DATABASE_FILENAME


def resolve_database_path(explicit: str | Path | None) -> Path:
    """Resolve the corpus database path, preferring an explicit value."""
    if explicit is not None:
        return Path(explicit).expanduser()
    return default_database_path()


def resolve_corpus_dir(explicit: str | Path | None) -> Path:
    """Resolve the de-identified corpus directory, preferring an explicit value."""
    if explicit is not None:
        return Path(explicit).expanduser()
    return default_corpus_dir()
