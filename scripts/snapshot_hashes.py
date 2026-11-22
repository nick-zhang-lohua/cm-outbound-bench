"""Capture SHA-256 snapshots of the project tree for audit and change tracking.

The project is not a Git repository, so an explicit file-hash manifest is the
auditable record of what a given delivery contained.

Usage::

    python scripts/snapshot_hashes.py --output docs/development/tree_hashes.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRECTORY_NAMES = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    "node_modules",
    "dist",
    ".mypy_cache",
}

EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

# The development log and report directories are the change record itself, and
# ``outputs/`` holds generated run manifests. None of them are source, so they
# are excluded to keep the pre/post-implementation snapshots comparable.
EXCLUDED_RELATIVE_PREFIXES = (
    "docs/development/",
    "docs/reports/",
    "outputs/",
)


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts[:-1]):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        if relative.as_posix().startswith(EXCLUDED_RELATIVE_PREFIXES):
            continue
        files.append(path)
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_snapshot() -> dict[str, object]:
    entries: dict[str, dict[str, object]] = {}
    for path in _iter_files(PROJECT_ROOT):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        entries[relative] = {
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    aggregate = hashlib.sha256()
    for relative, entry in sorted(entries.items()):
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(str(entry["sha256"]).encode("ascii"))
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "file_count": len(entries),
        "aggregate_sha256": aggregate.hexdigest(),
        "files": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "docs" / "development" / "tree_hashes.json",
    )
    args = parser.parse_args()
    snapshot = build_snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"files={snapshot['file_count']}")
    print(f"aggregate_sha256={snapshot['aggregate_sha256']}")
    print(f"written={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
