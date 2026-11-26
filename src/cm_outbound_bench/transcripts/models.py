"""Normalized conversation models reconstructed from the de-identified corpus.

The source corpus stores one JSON object per *transcript unit*. The format audit
(``docs/TXT原始数据格式只读审计报告.md`` §3.6) establishes, from five independent
checks, that one source file is one phone conversation: timestamps are monotonic,
adjacent segments never overlap, no clock is ever reset to zero, the longest file
is 191.8 s and every file has a distinct content hash. There is no ``call_id``
field and no call metadata in the source, so ``transcript_id`` is the only
reliable conversation identifier available.

Nothing in these models is invented: every field is either copied from the source
record or a deterministic function of copied fields.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from cm_outbound_bench.domain.data_model import DomainModel

#: Channel identifiers of the original corpus. The mapping is the research
#: protocol's original channel mapping; it is not inferred by an LLM.
CHANNEL_HISTORICAL_AGENT = 1
CHANNEL_CUSTOMER = 2

#: Channel id -> speaker role for the two channels the corpus documents.
CHANNEL_ROLES: dict[int, str] = {
    CHANNEL_HISTORICAL_AGENT: "historical_agent",
    CHANNEL_CUSTOMER: "customer",
}

#: Speaker role used for any channel outside the two documented channels.
OTHER_ROLE = "other"

#: Research terminology for the data unit.
#:
#: The corpus has **no** ``call_id``. ``transcript_id`` is
#: ``"CALL_" + hmac_sha256(key, source_file_name)[:24]`` (see
#: ``data_pipeline/deidentify.py::make_transcript_id``), so it identifies a
#: *delivered transcript unit* that was derived from exactly one source file.
#: Calling that a "call" is an inference: the format audit supports it but no
#: external call record confirms it.
#:
#: The public research terms are therefore:
#:
#: * ``transcript_unit_id`` - the corpus identifier of the delivered unit;
#: * ``conversation_id`` - the reconstructed conversation built from that unit.
#:
#: Today both have the same value. ``call_id`` is retained as the internal field
#: name for artifact compatibility and always means *inferred conversation unit*,
#: never a verified telephone call identifier.
CONVERSATION_ID_SEMANTICS = (
    "call_id is an inferred conversation unit identifier derived from a delivered "
    "transcript unit. The corpus contains no externally verified call_id, so this "
    "value must not be presented as a verified telephone call identifier."
)


def role_for_channel(channel_id: int) -> str:
    """Map a channel id to a speaker role without guessing beyond the protocol.

    Only channels 1 and 2 have a protocol-confirmed meaning. Channels 3, 4 and 5
    are under investigation and deliberately map to :data:`OTHER_ROLE`; see
    :mod:`cm_outbound_bench.decisions.channels` for the evidence-backed
    hypothesis catalogue.
    """
    return CHANNEL_ROLES.get(channel_id, OTHER_ROLE)


def format_offset_timestamp(start_ms: int) -> str:
    """Render a millisecond offset from call start as ``HH:MM:SS.mmm``.

    The source corpus contains *offsets from the start of the recording*, not a
    wall-clock time. The rendered value is therefore a relative call clock; the
    authoritative original values remain ``start_ms`` and ``end_ms``.
    """
    if start_ms < 0:
        raise ValueError("start_ms must not be negative")
    hours, remainder = divmod(start_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


class Turn(DomainModel):
    """One original record, placed on the call timeline.

    Event records (``SYSTEM``, ``DTMF``, ``MUSIC``, ...) are preserved because
    the audit report shows they are a real part of the timeline, not file
    metadata. They carry no text, so they are never eligible as a decision point
    or as visible conversation content.
    """

    turn_id: int = Field(ge=1)
    seq: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    timestamp: str
    channel: int
    speaker_role: Literal["historical_agent", "customer", "other"]
    record_type: Literal["speech", "event"]
    event: str | None = None
    text: str
    is_truncated: bool = False
    source_segment_id: str
    overlaps_previous: bool = False

    @property
    def is_speech(self) -> bool:
        return self.record_type == "speech" and bool(self.text.strip())


class SourceRef(DomainModel):
    """Where a reconstructed call came from. Never contains a source path."""

    corpus: str
    corpus_schema_version: str


class NormalizedCall(DomainModel):
    """A complete reconstructed two-channel conversation.

    ``call_id`` is the internal field name and means *inferred conversation
    unit*; see :data:`CONVERSATION_ID_SEMANTICS`. New code should read
    :attr:`conversation_id` so the research term appears in reports and schemas.
    """

    call_id: str
    business_domain: str = "unknown"
    source: SourceRef
    duration_ms: int = Field(ge=0)
    record_count: int = Field(ge=0)
    speech_record_count: int = Field(ge=0)
    event_record_count: int = Field(ge=0)
    channel_counts: dict[str, int] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    data_status: Literal["valid", "suspect", "invalid"] = "valid"
    content_sha256: str
    text_simhash: str = "0000000000000000"
    turns: list[Turn] = Field(default_factory=list)

    @property
    def speech_turns(self) -> list[Turn]:
        """Speech records with non-empty text, in timeline order."""
        return [turn for turn in self.turns if turn.is_speech]

    @property
    def conversation_id(self) -> str:
        """The research-facing identifier of the inferred conversation unit."""
        return self.call_id

    @property
    def transcript_unit_id(self) -> str:
        """The corpus identifier of the delivered transcript unit."""
        return self.call_id


def call_content_sha256(
    records: list[tuple[int, int, int, int, str, str | None, str]],
) -> str:
    """Hash the exact content of a unit for exact-duplicate detection.

    The signature is intentionally independent of ``transcript_id``: two
    identical conversations delivered under different opaque ids must produce
    the same value.
    """
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
