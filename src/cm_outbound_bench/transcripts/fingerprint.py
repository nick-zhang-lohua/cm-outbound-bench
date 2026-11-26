"""Deterministic, content-based fingerprints for duplicate-call detection.

Two calls must never be distributed across different splits if they are exact or
highly similar duplicates. Exact duplicates are found with a SHA-256 of the
record content. Highly similar calls are found with a 64-bit SimHash over CJK
character bigrams:

* the signature is a deterministic function of the de-identified speech text;
* ``hashlib`` is used rather than the built-in ``hash`` so the value does not
  depend on ``PYTHONHASHSEED``;
* near-duplicate candidates are found with a 4-band pigeonhole filter and then
  confirmed by Hamming distance, so the comparison stays O(n) in practice.

The threshold is deliberately conservative: a group only forms around calls whose
64-bit signatures differ in at most ``max_hamming`` bits.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

_SIGNATURE_BITS = 64
_BAND_BITS = 16
_BANDS = _SIGNATURE_BITS // _BAND_BITS
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def cjk_text(text: str) -> str:
    """Return only CJK ideographs, so punctuation and spacing cannot matter."""
    return "".join(_CJK.findall(text))


def simhash64(text: str) -> int:
    """Return a deterministic 64-bit SimHash of the CJK content of *text*."""
    compact = cjk_text(text)
    if not compact:
        return 0
    if len(compact) == 1:
        tokens = Counter([compact])
    else:
        tokens = Counter(compact[index : index + 2] for index in range(len(compact) - 1))
    if not tokens:
        return 0

    vector = [0] * _SIGNATURE_BITS
    for token, weight in tokens.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(_SIGNATURE_BITS):
            vector[bit] += weight if (value >> bit) & 1 else -weight
    signature = 0
    for bit in range(_SIGNATURE_BITS):
        if vector[bit] > 0:
            signature |= 1 << bit
    return signature


def fingerprint_hex(signature: int) -> str:
    """Render a signature as fixed-width lowercase hex for stable storage."""
    return f"{signature:016x}"


def hamming(a: int, b: int) -> int:
    """Return the Hamming distance between two integer signatures."""
    return (a ^ b).bit_count()


def _bands(signature: int) -> list[int]:
    mask = (1 << _BAND_BITS) - 1
    return [(signature >> (index * _BAND_BITS)) & mask for index in range(_BANDS)]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._rank = [0] * size

    def find(self, item: int) -> int:
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left == root_right:
            return
        if self._rank[root_left] < self._rank[root_right]:
            root_left, root_right = root_right, root_left
        self._parent[root_right] = root_left
        if self._rank[root_left] == self._rank[root_right]:
            self._rank[root_left] += 1


def near_duplicate_pairs(
    signatures: dict[str, int],
    *,
    max_hamming: int = 3,
) -> list[tuple[str, str]]:
    """Return candidate pairs whose fingerprints are within *max_hamming*.

    Calls with a zero signature (no CJK speech at all) are never paired: an empty
    conversation is not evidence of a duplicate conversation.
    """
    if max_hamming < 0:
        raise ValueError("max_hamming must not be negative")

    identifiers = sorted(identifier for identifier, value in signatures.items() if value)
    band_index: dict[tuple[int, int], list[str]] = {}
    for identifier in identifiers:
        for band, value in enumerate(_bands(signatures[identifier])):
            band_index.setdefault((band, value), []).append(identifier)

    pairs: set[tuple[str, str]] = set()
    for members in band_index.values():
        if len(members) < 2:
            continue
        for left_index in range(len(members)):
            for right_index in range(left_index + 1, len(members)):
                left, right = members[left_index], members[right_index]
                if hamming(signatures[left], signatures[right]) <= max_hamming:
                    pairs.add((left, right) if left < right else (right, left))
    return sorted(pairs)


def form_groups(
    identifiers: list[str],
    pairs: list[tuple[str, str]],
) -> dict[str, str]:
    """Assign every identifier a stable group id, merging *pairs* transitively."""
    ordered = sorted(identifiers)
    index = {identifier: position for position, identifier in enumerate(ordered)}
    union = _UnionFind(len(ordered))
    for left, right in pairs:
        union.union(index[left], index[right])

    members: dict[int, list[str]] = {}
    for identifier in ordered:
        members.setdefault(union.find(index[identifier]), []).append(identifier)

    groups: dict[str, str] = {}
    for group_members in members.values():
        digest = hashlib.sha256(
            "\n".join(sorted(group_members)).encode("utf-8")
        ).hexdigest()[:16]
        group_id = f"DUPGROUP_{digest}"
        for member in group_members:
            groups[member] = group_id
    return groups


__all__ = [
    "cjk_text",
    "fingerprint_hex",
    "form_groups",
    "hamming",
    "near_duplicate_pairs",
    "simhash64",
]
