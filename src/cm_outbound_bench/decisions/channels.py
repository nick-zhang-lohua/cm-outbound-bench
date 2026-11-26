"""Channel semantics registry for the real-transcript decision benchmark.

The registry is deliberately **conservative**. Only two channel meanings are
confirmed by the research protocol:

======  ===================
channel meaning
======  ===================
1       historical_agent
2       customer
======  ===================

Channels 3, 4 and 5 are **unknown**. Milestone 1.1 investigated them and produced
evidence for human review (``decisions/channel_audit.py`` and
``docs/reports/channel_semantics_audit.md``), but no interpretation has been
approved. ``role_for_channel`` therefore still returns ``"other"`` for them, and
nothing in this module assigns a speaker role to an unknown channel.

The module also carries the **hypothesis catalogue**. A hypothesis is a research
object with an explicit evidence level and an explicit statement of what it is
not; it is never a mapping. :func:`production_channel_mapping` is the single
source of truth for the production mapping, and
:func:`assert_production_mapping_unchanged` fails loudly if a future change
silently promotes an unknown channel.
"""

from __future__ import annotations

from pydantic import Field

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts import models as transcript_models

#: The two channels whose meaning the research protocol confirms.
#:
#: This is a *copy* of the runtime mapping for reporting purposes; the runtime
#: mapping lives in :mod:`cm_outbound_bench.transcripts.models` and the equality
#: of the two is asserted at import time so they cannot drift apart.
CONFIRMED_CHANNEL_ROLES: dict[int, str] = {
    1: "historical_agent",
    2: "customer",
}

if dict(transcript_models.CHANNEL_ROLES) != CONFIRMED_CHANNEL_ROLES:  # pragma: no cover
    raise RuntimeError(
        "the channel semantics registry disagrees with the runtime channel "
        f"mapping: {transcript_models.CHANNEL_ROLES} != {CONFIRMED_CHANNEL_ROLES}"
    )

#: Channels present in the corpus whose meaning is not established.
#: Their status stays ``unknown_pending_human_review`` until a researcher
#: approves an interpretation in writing.
UNKNOWN_CHANNEL_IDS: tuple[int, ...] = (3, 4, 5)

#: Role used for every channel outside :data:`CONFIRMED_CHANNEL_ROLES`.
UNKNOWN_CHANNEL_ROLE = transcript_models.OTHER_ROLE

#: Lifecycle of a channel interpretation. Only ``human_approved`` may ever change
#: the production mapping, and no channel currently reaches it.
STATUS_UNKNOWN = "unknown_pending_human_review"
STATUS_UNREVIEWED = "evidence_collected_not_reviewed"

#: Evidence levels used by the hypothesis catalogue. They are ordered from the
#: weakest to the strongest claim that the evidence can support.
EVIDENCE_CONFIRMED_BY_DOCUMENTATION = "confirmed_by_explicit_documentation"
EVIDENCE_STRONGLY_SUPPORTED = "strongly_supported_by_evidence"
EVIDENCE_PLAUSIBLE_UNVERIFIED = "plausible_but_unverified"
EVIDENCE_UNKNOWN = "unknown"


class ChannelRegistration(DomainModel):
    """One channel's registration state. Contains no conversation content."""

    channel_id: int = Field(ge=1)
    role: str
    confirmation_status: str
    confirmed_by_protocol: bool = False
    automatic_assignment_allowed: bool = False
    note: str = ""

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_by_protocol


class ChannelHypothesis(DomainModel):
    """One evidence-backed hypothesis about an unknown channel.

    A hypothesis is *not* a mapping. ``supports_mapping`` is always ``False``
    until a human approval record exists, and ``counter_evidence`` records what
    the same evidence does not support.
    """

    hypothesis_id: str = Field(min_length=1)
    channel_ids: tuple[int, ...]
    statement: str = Field(min_length=1)
    evidence_level: str
    supporting_evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    supports_mapping: bool = False
    benchmark_implications: list[str] = Field(default_factory=list)


def role_for_channel(channel_id: int) -> str:
    """Return the production role for *channel_id*.

    Delegates to the runtime mapping in
    :func:`cm_outbound_bench.transcripts.models.role_for_channel`, so there is one
    behavioural definition. Confirmed channels keep their protocol role. Every
    other channel returns :data:`UNKNOWN_CHANNEL_ROLE`; no unknown channel is ever
    assigned a speaker meaning here.
    """
    return transcript_models.role_for_channel(channel_id)


def is_unknown_channel(channel_id: int) -> bool:
    """True when *channel_id* is one of the channels under review."""
    return channel_id in UNKNOWN_CHANNEL_IDS


def production_channel_mapping() -> dict[int, str]:
    """Return the production channel -> role mapping.

    The mapping is exactly the confirmed protocol mapping. Unknown channels are
    reported separately by :func:`unregistered_channel_ids`, never folded in with
    an invented role.
    """
    return dict(CONFIRMED_CHANNEL_ROLES)


def unregistered_channel_ids() -> tuple[int, ...]:
    """Return the channels whose meaning is not approved."""
    return tuple(sorted(UNKNOWN_CHANNEL_IDS))


def assert_production_mapping_unchanged(
    mapping: dict[int, str] | None = None,
) -> None:
    """Raise if a proposed mapping promotes an unknown channel to a role.

    Called by the channel audit and by the test suite. It converts "do not assign
    a meaning without approval" from a convention into a checked invariant.
    """
    candidate = production_channel_mapping() if mapping is None else dict(mapping)
    if candidate != CONFIRMED_CHANNEL_ROLES:
        raise ValueError(
            "the production channel mapping must stay exactly "
            f"{CONFIRMED_CHANNEL_ROLES}; got {candidate}. Channels 3/4/5 remain "
            "unknown until a researcher approves an interpretation in writing."
        )


def channel_registry() -> list[ChannelRegistration]:
    """Return the registration state of every channel the corpus contains."""
    registrations = [
        ChannelRegistration(
            channel_id=channel_id,
            role=role,
            confirmation_status="confirmed_by_research_protocol",
            confirmed_by_protocol=True,
            automatic_assignment_allowed=True,
            note=(
                "Channel meaning is fixed by the research protocol. The corpus "
                "itself stores role=UNKNOWN, so this is a protocol decision, not "
                "a corpus label."
            ),
        )
        for channel_id, role in sorted(CONFIRMED_CHANNEL_ROLES.items())
    ]
    registrations.extend(
        ChannelRegistration(
            channel_id=channel_id,
            role=UNKNOWN_CHANNEL_ROLE,
            confirmation_status=STATUS_UNKNOWN,
            confirmed_by_protocol=False,
            automatic_assignment_allowed=False,
            note=(
                "Meaning investigated in Milestone 1.1 and presented for human "
                "review. No speaker meaning is assigned and the 'other' role is "
                "preserved."
            ),
        )
        for channel_id in UNKNOWN_CHANNEL_IDS
    )
    return registrations


def channel_registry_payload() -> dict[str, object]:
    """Return a JSON-safe registry payload for reports and CLI output."""
    mapping = production_channel_mapping()
    return {
        "production_mapping": {str(key): value for key, value in mapping.items()},
        "mapping_semantics": {
            "unit": "transcript_unit_id (one delivered unit; inferred conversation)",
            "determined_by": "research protocol, not by the corpus",
            "corpus_role_field": "UNKNOWN for every record",
        },
        "unknown_channels": list(unregistered_channel_ids()),
        "unknown_channel_role": UNKNOWN_CHANNEL_ROLE,
        "human_approval_required": True,
        "registry": [
            registration.model_dump(mode="json")
            for registration in channel_registry()
        ],
    }


#: Evidence-backed hypotheses for the unknown channels.
#:
#: The catalogue is the *research* output of the Milestone 1.1 investigation. It
#: exists so a reviewer can see what the evidence supports before deciding. No
#: entry is a mapping and every entry carries its counter-evidence.
CHANNEL_HYPOTHESES: tuple[ChannelHypothesis, ...] = (
    ChannelHypothesis(
        hypothesis_id="H1_distinct_speaker",
        channel_ids=(3, 4, 5),
        statement=(
            "A channel beyond 1 and 2 identifies an additional distinct human "
            "speaker in the recorded audio, most often a third party who takes "
            "over or joins the call."
        ),
        evidence_level=EVIDENCE_STRONGLY_SUPPORTED,
        supporting_evidence=[
            "Channel 3 and channel 4 both carry conversational turns that "
            "interleave with channel 1 and channel 2 in source order and are "
            "answered by the channel 1 speaker.",
            "Several units contain an explicit hand-over: the channel 1 speaker "
            "asks whether the person talking is the line owner or 'the lady "
            "before', which only makes sense with at least three participants.",
            "Channels 3 and 4 each carry their own non-speech event records "
            "(UNCLASSIFIED / SONANT / LAUGH), i.e. the vendor tracked them like "
            "any other speaker, not like a system stream.",
            "Record timestamps never overlap channel 1 or channel 2 records, the "
            "same non-overlapping rule that holds between channels 1 and 2.",
        ],
        counter_evidence=[
            "The corpus carries no speaker attribute, so 'distinct speaker' is a "
            "structural inference from turn-taking, not a verified label.",
            "Channel 5 carries only two event records and no speech at all, so it "
            "cannot be a speaker under this hypothesis in the observed data.",
        ],
        benchmark_implications=[
            "Third-party turns may contain business-relevant information (who is "
            "the owner, what the owner wants).",
            "Treating the third party as 'customer' would silently merge two "
            "different people into one speaker role.",
            "Treating the third party as 'historical_agent' is worse: it would "
            "put customer-side words into the agent's mouth.",
        ],
    ),
    ChannelHypothesis(
        hypothesis_id="H2_channel_number_is_not_a_fixed_role",
        channel_ids=(3, 4, 5),
        statement=(
            "The channel number is a per-unit speaker slot assigned by the "
            "transcription vendor, so channel 1 is not *guaranteed* to be the "
            "agent and channel 2 is not *guaranteed* to be the customer in every "
            "unit; the 1=agent, 2=customer mapping is a corpus-level regularity."
        ),
        evidence_level=EVIDENCE_STRONGLY_SUPPORTED,
        supporting_evidence=[
            "At least three valid units have no channel 1 speech at all while the "
            "channel 3 speaker delivers the agent's opening script and product "
            "pitch, and the channel 2 speaker answers as the customer.",
            "In those units the channel 1 records are SYSTEM / DTMF / ENS events "
            "plus pre-call room audio, which is exactly the role channel 1 plays "
            "elsewhere when the agent speaks on channel 1.",
            "Across units the same channel can act as agent-side or "
            "customer-side, which a fixed physical-leg interpretation does not "
            "predict.",
        ],
        counter_evidence=[
            "The effect is rare: only 3 of 4,527 units lack channel 1 speech, "
            "and 99.8% of units start on channel 1.",
            "No external call record exists to confirm which speaker is the "
            "agent, so this rests on content reading, not on ground truth.",
        ],
        benchmark_implications=[
            "The current task builder rejects any cutoff whose visible prefix "
            "contains an 'other' channel, which is the conservative response and "
            "needs no change before human review.",
            "A future approved change that maps an unknown channel to a role must "
            "be applied per context, not as a global constant, and must be "
            "validated unit by unit.",
        ],
    ),
    ChannelHypothesis(
        hypothesis_id="H3_system_or_ivr_announcement",
        channel_ids=(3, 4, 5),
        statement=(
            "An unknown channel carries IVR prompts, ring-back or recorded system "
            "announcements rather than a live speaker."
        ),
        evidence_level=EVIDENCE_PLAUSIBLE_UNVERIFIED,
        supporting_evidence=[
            "The corpus already models non-speech audio as explicit events "
            "(SYSTEM / MUSIC / DTMF) and those events are attached to channels "
            "1 and 2, so a channel is not intrinsically a speech-only stream.",
            "Channel 5 carries only two UNCLASSIFIED events and no speech.",
        ],
        counter_evidence=[
            "Channels 3 and 4 carry ordinary conversational Chinese that is "
            "answered by the channel 1 speaker, which a recorded announcement "
            "would not produce.",
            "The corpus has no channel value whose records are exclusively system "
            "events except channel 5, and even there the sample is 2 records.",
        ],
        benchmark_implications=[
            "Even if true for channel 5, the effect on the benchmark is zero: "
            "event records are already excluded from visible context.",
        ],
    ),
    ChannelHypothesis(
        hypothesis_id="H4_recording_artifact",
        channel_ids=(3, 4, 5),
        statement=(
            "Unknown-channel records are a recording or transcription artifact "
            "(echo, duplicate capture, overlapping-speech split) rather than new "
            "content."
        ),
        evidence_level=EVIDENCE_UNKNOWN,
        supporting_evidence=[
            "Some channel 4 records duplicate the immediately preceding channel 1 "
            "or channel 2 wording, which is consistent with an ASR boundary "
            "artifact.",
        ],
        counter_evidence=[
            "The corpus contains zero overlapping records, so an unknown channel "
            "never coincides in time with channel 1 or 2.",
            "Most channel 3 and channel 4 speech contains content that does not "
            "appear on any other channel.",
            "Unknown-channel records carry their own event records and their own "
            "truncation markers, which a pure duplication artifact would not.",
        ],
        benchmark_implications=[
            "If duplication were confirmed, deduplicating would change turn counts "
            "but not the visible prefix semantics.",
        ],
    ),
    ChannelHypothesis(
        hypothesis_id="H5_supervisor_or_consultation_leg",
        channel_ids=(3, 4),
        statement=(
            "An unknown channel is a second agent-side leg, such as a supervisor "
            "monitoring or a colleague consulted during the call."
        ),
        evidence_level=EVIDENCE_PLAUSIBLE_UNVERIFIED,
        supporting_evidence=[
            "One unit opens with two alternating speakers complaining about "
            "calling customers before the called party answers, which reads as "
            "two colleagues in the same room.",
            "One unit has a channel 3 speaker delivering the agent script while "
            "channel 1 holds only events.",
        ],
        counter_evidence=[
            "In most units the unknown-channel speaker answers the agent's "
            "questions in the customer's role, not the agent's.",
            "No supervisor or consultation event marker exists in the corpus.",
        ],
        benchmark_implications=[
            "A second agent leg would break the assumption that exactly one "
            "historical agent turn follows a customer turn.",
        ],
    ),
)


def hypothesis_catalogue() -> list[dict[str, object]]:
    """Return the hypothesis catalogue as JSON-safe data."""
    return [hypothesis.model_dump(mode="json") for hypothesis in CHANNEL_HYPOTHESES]


__all__ = [
    "CHANNEL_HYPOTHESES",
    "CONFIRMED_CHANNEL_ROLES",
    "EVIDENCE_CONFIRMED_BY_DOCUMENTATION",
    "EVIDENCE_PLAUSIBLE_UNVERIFIED",
    "EVIDENCE_STRONGLY_SUPPORTED",
    "EVIDENCE_UNKNOWN",
    "STATUS_UNKNOWN",
    "STATUS_UNREVIEWED",
    "UNKNOWN_CHANNEL_IDS",
    "UNKNOWN_CHANNEL_ROLE",
    "ChannelHypothesis",
    "ChannelRegistration",
    "assert_production_mapping_unchanged",
    "channel_registry",
    "channel_registry_payload",
    "hypothesis_catalogue",
    "is_unknown_channel",
    "production_channel_mapping",
    "role_for_channel",
    "unregistered_channel_ids",
]
