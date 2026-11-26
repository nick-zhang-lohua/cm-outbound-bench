"""Inventory of business reference material available to the benchmark.

The inventory answers one question honestly: *is there anything in this workspace
that can support a policy-verified business claim?* It scans a bounded, explicit
list of locations, hashes every document it finds, classifies its authority with
documented rules, and never opens the confidential raw ``data/TXT`` export.

Classification rules are intentionally strict:

``authoritative``
    A document that states it is an official operating policy or procedure for
    China Mobile and is supplied by the business. **No such document exists in
    this workspace**, so this level is currently empty.
``synthetic``
    A document written for the legacy synthetic benchmark. It is useful for
    pipeline mechanics only and must never be cited as a China Mobile rule.
``recovered_partial``
    A reconstructed note from an earlier conversation. It is not a complete
    annotation set and cannot be merged into a label store.
``unverified``
    An audit, report or analysis written by this project. It describes the data,
    not the business rules.

A source that cannot be classified with certainty is reported as ``unverified`` and
``supports_policy_verification`` stays ``False``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

from cm_outbound_bench.decisions.annotation import (
    BusinessRuleCitation,
    BusinessSourceReference,
)

BUSINESS_SOURCE_VERSION = "1.0"

#: Glob patterns searched, relative to the repository root. The raw confidential
#: export is deliberately absent: project policy forbids reading ``data/TXT``.
REPOSITORY_PATTERNS: tuple[str, ...] = (
    "data/policy/*.md",
    "data/policy/*.txt",
    "docs/*.md",
    "docs/reports/*.md",
    "docs/annotation/*.md",
)

#: Glob patterns searched in the workspace *parent* directory (research material
#: that lives outside the checkout).
WORKSPACE_PATTERNS: tuple[str, ...] = (
    "docs/*.md",
    "data/analysis/*.json",
)

#: Marker phrases that identify a synthetic document.
SYNTHETIC_MARKERS: tuple[str, ...] = (
    "synthetic",
    "合成",
    "模拟数据",
    "虚构",
)

#: Marker phrases that identify a recovered, incomplete annotation note.
RECOVERED_MARKERS: tuple[str, ...] = (
    "partial_not_import_ready",
    "recovered_summary",
    "reconstruction_status",
)

#: Marker phrases that would identify an official document. Kept explicit so the
#: inventory can be audited: a document is only ``authoritative`` if it carries one
#: of these *and* a declared issuing organisation.
OFFICIAL_MARKERS: tuple[str, ...] = (
    "official policy",
    "official business rule",
    "中国移动通信集团正式文件",
    "业务规范（正式发布）",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _classify(path: Path, text: str) -> tuple[str, str]:
    """Return ``(authority_level, reason)`` for one document.

    Classification is primarily **path-driven**, because a document that merely
    mentions the word "synthetic" is not itself a synthetic policy. Content
    markers are used to detect a self-declared official document and a self-declared
    partial recovery, and to confirm that a policy file is synthetic.
    """
    parts = set(path.parts)
    lowered = text.casefold()

    if "analysis" in parts and path.suffix == ".json":
        return (
            "recovered_partial",
            "recovered analysis artifact, not a complete annotation set and not a "
            "business rule source",
        )
    if "policy" in parts:
        if any(marker.casefold() in lowered for marker in SYNTHETIC_MARKERS):
            return (
                "synthetic",
                "legacy synthetic benchmark policy; it is not a China Mobile rule "
                "document",
            )
        return (
            "unverified",
            "policy-shaped file without a declared official issuer",
        )
    if any(marker.casefold() in lowered for marker in RECOVERED_MARKERS):
        return (
            "recovered_partial",
            "the file states that it is a partial recovery and is not import ready",
        )
    if any(marker.casefold() in lowered for marker in OFFICIAL_MARKERS):
        if "中国移动" in text and any(
            marker.casefold() in lowered for marker in OFFICIAL_MARKERS
        ):
            return (
                "authoritative",
                "the document describes itself as official China Mobile material",
            )
        return (
            "unverified",
            "official-sounding wording without a declared China Mobile issuer",
        )
    return (
        "unverified",
        "project documentation or audit material; it describes the data and the "
        "benchmark, not the business rules",
    )


def _topics(path: Path, text: str) -> list[str]:
    """Return coarse topic hints. Never a business-domain label."""
    topics: list[str] = []
    lowered = text.casefold()
    keyword_topics = {
        "identity": ("身份", "实名", "机主", "identity"),
        "billing": ("资费", "话费", "扣费", "billing", "recharge"),
        "package": ("套餐", "package", "plan"),
        "promotion": ("活动", "赠送", "promotion", "offer"),
        "notification": ("短信", "通知", "notification", "sms"),
        "privacy": ("隐私", "脱敏", "privacy", "de-identif"),
    }
    for topic, keywords in keyword_topics.items():
        if any(keyword.casefold() in lowered for keyword in keywords):
            topics.append(topic)
    if path.suffix == ".json":
        topics.append("analysis_artifact")
    return topics


def discover_business_sources(
    repository_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
    max_bytes: int = 4 * 1024 * 1024,
) -> list[BusinessSourceReference]:
    """Scan the bounded locations and return a classified inventory.

    The scan is read-only, size-bounded and never leaves the declared roots. Raw
    transcript files are never opened; only the patterns above are considered.
    """
    root = Path(repository_root)
    sources: list[BusinessSourceReference] = []
    roots: list[tuple[Path, Sequence[str]]] = [(root, REPOSITORY_PATTERNS)]
    if workspace_root is not None:
        roots.append((Path(workspace_root), WORKSPACE_PATTERNS))

    for base, patterns in roots:
        for pattern in patterns:
            for path in sorted(base.glob(pattern)):
                if not path.is_file():
                    continue
                if "TXT" in path.parts:
                    # Defence in depth: the confidentiality boundary is absolute.
                    continue
                try:
                    if path.stat().st_size > max_bytes:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                authority, reason = _classify(path, text)
                try:
                    relative = path.relative_to(root.parent).as_posix()
                except ValueError:
                    relative = path.as_posix()
                sources.append(
                    BusinessSourceReference(
                        source_id="SRC-" + sha256_text(relative)[:12],
                        source_type=(
                            "official_policy_or_documentation"
                            if authority == "authoritative"
                            else "project_or_synthetic_documentation"
                        ),
                        source_path=relative,
                        version=_declared_version(text),
                        authority_level=authority,  # type: ignore[arg-type]
                        applicable_topics=_topics(path, text),
                        content_hash=sha256_text(text),
                        notes=reason,
                    )
                )
    sources.sort(key=lambda item: (item.authority_level, item.source_path))
    return sources


def _declared_version(text: str) -> str:
    """Extract a version marker if the document declares one."""
    for line in text.splitlines()[:20]:
        lowered = line.casefold()
        if "version" in lowered or "版本" in line:
            cleaned = " ".join(line.split())
            if len(cleaned) <= 120:
                return cleaned
    return ""


def authoritative_sources(
    sources: Iterable[BusinessSourceReference],
) -> list[BusinessSourceReference]:
    """Return only sources that may support a policy-verified claim."""
    return [item for item in sources if item.supports_policy_verification]


def approved_rule_citations(
    sources: Sequence[BusinessSourceReference],
) -> list[BusinessRuleCitation]:
    """Return citable rules taken from authoritative sources.

    Currently empty: no authoritative source exists, so no rule can be cited and the
    ``policy_verified`` tier stays unpopulated. The function returns an empty list
    rather than inventing a rule statement, because a citation needs a real quoted
    rule and an issuing authority to be verifiable.
    """
    if not authoritative_sources(sources):
        return []
    raise NotImplementedError(
        "extracting a citable rule requires a human-approved official source and an "
        "explicit rule statement; add the extraction only when such a source exists"
    )


def inventory_payload(
    sources: Sequence[BusinessSourceReference],
) -> dict[str, object]:
    """Return a JSON-safe inventory summary for reports and CLI output."""
    by_level: dict[str, int] = {}
    for source in sources:
        by_level[source.authority_level] = by_level.get(source.authority_level, 0) + 1
    authoritative = authoritative_sources(sources)
    return {
        "inventory_version": BUSINESS_SOURCE_VERSION,
        "sources": [source.model_dump(mode="json") for source in sources],
        "counts_by_authority": dict(sorted(by_level.items())),
        "authoritative_sources": len(authoritative),
        "policy_verification_supported": bool(authoritative),
        "statement": (
            "No authoritative China Mobile business policy or verified operation "
            "record exists in this workspace. policy_verified_decision_success_rate "
            "is therefore unavailable, and no business rule was invented."
            if not authoritative
            else "Authoritative sources are present and may support the "
            "policy-verified tier."
        ),
        "raw_transcript_export_read": False,
    }


def save_inventory(path: str | Path, sources: Sequence[BusinessSourceReference]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(inventory_payload(sources), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


__all__ = [
    "BUSINESS_SOURCE_VERSION",
    "OFFICIAL_MARKERS",
    "RECOVERED_MARKERS",
    "REPOSITORY_PATTERNS",
    "SYNTHETIC_MARKERS",
    "WORKSPACE_PATTERNS",
    "approved_rule_citations",
    "authoritative_sources",
    "discover_business_sources",
    "inventory_payload",
    "save_inventory",
    "sha256_text",
]
