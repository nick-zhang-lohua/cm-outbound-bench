# Channel Semantics Audit — Channels 3, 4 and 5

| Field | Value |
|---|---|
| Milestone | 1.1 |
| Date | 2026-11-26 |
| Dataset | `cm_outbound_v2` (4,527 delivered transcript units, 137,178 records) |
| Production mapping under audit | `1 → historical_agent`, `2 → customer`, others → `other` |
| Status of channels 3/4/5 | **unknown, pending human review** |
| Mapping changed by this audit | **No** |
| Generator | `cm-outbound decision audit-channels` |
| Local review package | `outputs/decision/channel_review/` |

> **Decision required from the researcher.** This audit collects and organises
> evidence. It does not decide what channels 3, 4 and 5 mean, and it does not change
> the production mapping. Until an interpretation is approved in writing, the
> benchmark keeps treating them as `other` and keeps rejecting task cutoffs whose
> visible prefix contains one of them.

---

## 0. Method and evidence boundary

Evidence was drawn from five sources. No source required opening the confidential
raw export.

| # | Source | What it contributed |
|---|---|---|
| 1 | De-identified corpus `data/deidentified/cm_outbound_v2/calls.jsonl` | All record-level statistics, structure and representative examples |
| 2 | `data_pipeline/deidentify.py`, `data_pipeline/PIPELINE.md` | Field semantics, the accepted channel range, event vocabulary, and the source speaker-attribute field |
| 3 | `docs/TXT原始数据格式只读审计报告.md` (outside the checkout, an approved read-only audit report) | Raw-corpus channel and speaker-attribute distributions, aggregated by that audit |
| 4 | `src/cm_outbound_bench/transcripts/*`, `src/cm_outbound_bench/decisions/builder.py` | Current mapping, task-construction rejection rules |
| 5 | Corpus manifest and prior milestone reports | Counts, hashes, previously recorded limitations |

`docs/data_privacy.md` §2.1 forbids benchmark code from opening `data/TXT`. That
rule was respected: this audit does not read, list or parse any raw transcript
file. Source 3 is a **report about** the raw data, not the raw data.

---

## 1. Observed facts

### 1.1 The source format documents a per-record speaker attribute

`data_pipeline/PIPELINE.md` §"Parsing rules" states the raw line format:

```text
[start,end]<TAB>channel<TAB>gender,language<TAB>payload
```

The third field is a **speaker attribute pair** (`男`/`女`/`儿童` and
`普通话`/`方言`), and the read-only audit report records that within one file one
channel always carries exactly one value of that pair — no file mixes genders
inside a channel. The attribute is therefore a *channel-level speaker profile*, not
per-utterance voice-print detection.

The de-identification step deliberately drops this attribute
(`data_pipeline/deidentify.py` writes only `seq`, `start_ms`, `end_ms`,
`channel_id`, `role`, `payload_type`, `event`, `text_masked`, `is_truncated`,
`asr_flags`, `redaction_flags`). **Consequence:** the de-identified corpus cannot be
used to compare the voice profile of channel 3/4/5 against channels 1 and 2. Only
the raw audit report has that information, and it does not break it down per
channel beyond channel 1.

### 1.2 Channel record counts in the delivered corpus

Recomputed from the corpus in this milestone:

| Channel | Records | % of records | Units containing | Units with speech | Units with events | Speech records | Event records |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 104,790 | 76.3898 | 4,526 | 4,515 | 4,473 | 66,387 | 38,403 |
| 2 | 31,867 | 23.2304 | 4,444 | 4,428 | 4,202 | 27,648 | 4,219 |
| **3** | **314** | **0.2289** | **84** | **70** | **40** | **225** | **89** |
| **4** | **205** | **0.1494** | **42** | **33** | **21** | **168** | **37** |
| **5** | **2** | **0.0015** | **1** | **0** | **1** | **0** | **2** |

One of the 70 channel-3 units carries only an empty-after-masking speech record, so
69 units contain usable third-channel text.

For comparison, the read-only audit report records the raw (pre-quarantine) speech
line counts as 1: 70,247 in 4,737 files; 2: 29,388 in 4,654 files; 3: 229 in 86
files; 4: 208 in 50 files; 5: 0 speech in 1 file (label rows only). The delivered
numbers are slightly smaller because 211 units were quarantined, and they agree in
order of magnitude and in the rank ordering of the channels.

### 1.3 Text and structure of each unknown channel

| Property | Channel 3 | Channel 4 | Channel 5 |
|---|---|---|---|
| Speech records | 225 | 168 | 0 |
| Non-empty text | 224 | 168 | 0 |
| Empty text | 1 | 0 | 0 |
| Event records | 89 | 37 | 2 |
| Event vocabulary | `UNCLASSIFIED` 84, `ENS` 4, `LAUGH` 1 | `UNCLASSIFIED` 35, `ENS` 1, `SONANT` 1 | `UNCLASSIFIED` 2 |
| Truncated speech (`+`) | 8 | 14 | 0 |
| Records overlapping the previous record | 0 | 0 | 0 |
| Text length min / median / max (chars) | 1 / 8 / 55 | 1 / 5 / 41 | — |
| Position in unit, p10 / p50 / p90 | 0.064 / 0.520 / 0.924 | 0.451 / 0.753 / 0.956 | 0.842 / 0.842 / 0.869 |
| Structural shape | mixed speech + event | mixed speech + event | **event only** |
| ASR flags observed | `short_unclassified_digit_masked` 1, `empty_after_mask` 1 | `short_unclassified_digit_masked` 1 | none |
| Redaction flags observed | `PHONE` 9, `ACCOUNT` 1 | `ACCOUNT` 1 | none |

Three structural facts matter:

1. **Channels 3 and 4 are not event streams.** They carry ordinary conversational
   speech with the same truncation marker, the same redaction placeholders and the
   same non-overlapping timing discipline as channels 1 and 2.
2. **Channel 5 is an event stream.** Its two records are both `UNCLASSIFIED`
   events with no text. Two records cannot characterise anything statistically.
3. **No unknown-channel record ever overlaps a channel 1 or channel 2 record.**
   Overlapping speech is therefore not the phenomenon these channels represent.

### 1.4 Where unknown records sit relative to the two known speakers

Neighbour distribution (the channel of the record immediately before / after):

| Channel | Preceded by | Followed by |
|---|---|---|
| 3 | ch1 161, ch3 91, ch2 42, ch4 17, ch5 1 | ch1 160, ch3 91, ch2 35, ch4 18, ch5 1 |
| 4 | ch1 151, ch4 27, ch3 18, ch2 8, ch5 1 | ch1 145, ch4 27, ch3 17, ch2 9, ch5 1 |
| 5 | ch4 1, ch3 1 | ch4 1, ch3 1 |

Channels 3 and 4 are answered by the channel 1 speaker and also form consecutive
runs of their own, exactly like a participant in a dialogue. Channel 5 appears only
inside an event cluster between channel 3 and channel 4 records.

### 1.5 Unit-level composition

| Channel set in the unit | Units |
|---|---:|
| {1, 2} | 4,328 |
| {1} | 83 |
| {1, 2, 3} | 73 |
| {1, 2, 4} | 31 |
| {1, 2, 3, 4} | 10 |
| {2} | 1 |
| {1, 2, 3, 4, 5} | 1 |
| **Total** | **4,527** |

* Units containing any unknown channel: **115** (2.54%).
* Units containing unknown-channel **speech**: **93**.
* Units with **no channel 1 speech at all**: **11**.

### 1.6 Share of the conversation carried by the unknown channel

Measured as speech records on that channel divided by all speech records in the
unit, within units that have unknown-channel speech:

| Channel | n | min | p25 | median | p90 | max |
|---|---:|---:|---:|---:|---:|---:|
| 3 | 69 | 0.022 | 0.050 | 0.091 | 0.526 | 0.750 |
| 4 | 33 | 0.021 | 0.105 | 0.195 | 0.267 | 0.486 |

In most affected units the unknown channel contributes a small fraction of the
speech — it is an incidental participant, not a parallel conversation. But in a few
units it carries most of the speech, which is what makes a single global mapping
unsafe.

### 1.7 Everything in channels 3 and 4 is structurally identical

The 314 channel-3 records and the 205 channel-4 records do **not** split into
sub-populations: each channel has one record-type distribution, one event
vocabulary, one truncation rate order of magnitude, the same absence of overlapping
records, and the same lead-in/lead-out pattern (mostly adjacent to channel 1). There
is no evidence of a distinct "channel 3 events are something else" subgroup beyond
the fact that roughly 28% of channel-3 records and 18% of channel-4 records are
events. Channel 5, with two event records, is the only structural outlier.

---

## 2. Representative evidence

Twelve records per channel were exported with three records of real surrounding
context on each side (all records for channel 5, which has only two). The full set —
including sequence, timestamps, ASR flags, redaction flags and neighbour turns — is
in the local package:

```text
outputs/decision/channel_review/channel_3_samples.jsonl
outputs/decision/channel_review/channel_4_samples.jsonl
outputs/decision/channel_review/channel_5_samples.jsonl
outputs/decision/channel_review/channel_review_summary.csv
```

Each record carries `conversation_reference` (the opaque HMAC transcript id),
`evidence_source` (`deidentified_corpus::<unit>::seq=<n>`) and its timing offset, so
a reviewer can trace any excerpt back to the exact corpus record.

The excerpts below are **short, de-identified fragments** quoted only as far as the
channel decision requires. `<PHONE>` and `<NAME>` are the corpus placeholders.

> **Privacy note.** This document is tracked in version control, while the review
> package is git-ignored. Only the minimum text needed to support a channel
> hypothesis appears here: each fragment is truncated, already de-identified by the
> published pipeline, and used at most twice. The complete record — full text,
> sequence, timestamps, ASR flags, redaction flags and three turns of context on each
> side — lives only in the local package. No bulk conversation text is committed.

### 2.1 Channel 3 evidence

| # | Unit | Observation |
|---|---|---|
| 3-a | `CALL_24aca65…` | Channel 3 delivers the outbound agent script: `喂你好，我是移动公司的客服代表` then `请问是<PHONE>的机主是吗？`. The channel 2 speaker answers as the customer. Channel 1 holds only `SYSTEM`/`DTMF`/`ENS` events plus one pre-call utterance. |
| 3-b | `CALL_05c87fa…` | Channel 3 answers the agent's questions as the respondent: `你什么事`, `我就用这个号码`, `是啊是是`; the agent asks `是不是您本人啊`, `不是您本人是吗？`, `那是您家人吗？`. |
| 3-c | `CALL_07098a8…` | Two speakers talk **before** the call connects — channel 3: `这最长的是六分钟…才成功你知道吗？`, channel 1: `我打的都吐血…`, then `MUSIC`, then an IVR line, then the called party answers on channel 2 at 39.9 s. |
| 3-d | `CALL_11b5938…` | Channel 3 holds a domestic exchange (`我婆婆还叫我跑去永定吃酒…`, `那我带小的去，你早一点下班去接大的`) before the agent's greeting and before the channel 2 speaker says `喂你好` at 22.3 s. |
| 3-e | `CALL_1c408ec…` | Channel 3 alternates with `DTMF` inside a rapid event cluster at 0.0–5.7 s and then carries one speech record `对啊，赶紧呀`. |
| 3-f | `CALL_11ed2bf…` | Channel 3 records are interleaved with channel 1 `SYSTEM` events across the middle of the unit and carry no text at all. |

### 2.2 Channel 4 evidence

| # | Unit | Observation |
|---|---|---|
| 4-a | `CALL_03751e4…` | Channel 4 answers as the respondent: `谁呀？干嘛？`; the agent replies `是这样的，张天生是您家人吗？` and channel 4 answers `对啊，干嘛？`. |
| 4-b | `CALL_185bb02…` | A hand-over is described explicitly. Channel 4: `哦她给我听啊，她不想听，她很烦`; the agent asks `请问您是这号码机主，还是之前的女士是这号码的机主呢？`. |
| 4-c | `CALL_140b9a1…` | Channel 2 says `呃，等一下好吗`, then channel 4 takes over (`喂`, `呃我用`); the agent asks `是您用还是前面<NAME>女士用呢`. |
| 4-d | `CALL_0493bc7…` | Channel 4 carries a full customer role across the unit: `多少钱一个月？`, `才一百兆流量了`, `讲完了你就改吧`, `好再见`; channel 3 also speaks in the same unit. |
| 4-e | `CALL_0a82772…` | A single channel 4 `ENS` event closes the unit, with no text. |
| 4-f | `CALL_1736496…` | Channel 4 `UNCLASSIFIED` event sits between a channel 2 event and the agent's opening question. |

### 2.3 Channel 5 evidence

Both records exist in one unit (`CALL_e5b763b…`), at 2:06.358 and 2:10.431, both
`UNCLASSIFIED` with no text, embedded in a cluster of channel 3 and channel 4 events.
This is the **entire** channel-5 population in the corpus.

---

## 3. Hypotheses, with confidence

Confidence vocabulary used below:

* **Confirmed by explicit documentation** — a document states it.
* **Strongly supported by evidence** — multiple independent records agree and no
  counter-evidence survives.
* **Plausible but unverified** — consistent with the data, not demonstrated.
* **Unknown** — the data cannot distinguish between possibilities.

### H1 — An unknown channel is an additional distinct human speaker (strongly supported)

**Statement.** A channel beyond 1 and 2 identifies another person in the recorded
audio — typically a third party who takes over or joins the call.

**Supporting evidence**
1. Channels 3 and 4 carry conversational turns that interleave with channels 1 and 2
   in source order and are answered by the channel 1 speaker (§1.3, §1.4).
2. Two units contain an explicit hand-over where the agent asks whether the current
   speaker is the line owner or "the lady before" (4-b, 4-c).
3. Channels 3 and 4 carry their own event records (`UNCLASSIFIED`, `ENS`, `SONANT`,
   `LAUGH`), so the vendor modelled them like a participant, not like a system
   stream.
4. The source format attaches a `gender,language` speaker attribute to every record
   and that attribute is constant per channel within a file — the field that makes
   channel 3/4 a *speaker profile* in the first place (§1.1).

**Counter-evidence**
* The corpus stores `role = UNKNOWN` everywhere, so "distinct speaker" is a
  structural inference from turn-taking, not a labelled fact.
* Channel 5 has no speech, so under H1 it is not a speaker in the observed data.

**Effect on the benchmark if adopted.** Third-party turns can carry business-relevant
information (who the owner is, what the owner wants). Mapping them to `customer`
would merge two different people into one role; mapping them to `historical_agent`
would put customer-side words in the agent's mouth.

### H2 — The channel number is a per-unit speaker slot, not a fixed role (strongly supported)

**Statement.** Channel 1 is not *guaranteed* to be the agent and channel 2 is not
*guaranteed* to be the customer in every unit. The `1 = agent, 2 = customer` mapping
is a corpus-level regularity, not a per-unit invariant.

**Supporting evidence**
1. In at least three valid units the agent's opening script and product pitch are
   delivered on **channel 3**, while channel 1 carries only `SYSTEM`/`DTMF`/`ENS`
   events and one pre-call utterance (3-a), and channel 2 answers as the customer.
2. Across units the same channel number acts as agent-side in one unit and
   customer-side in another (3-a vs 3-b vs 3-c), which a fixed physical-leg reading
   does not predict.
3. Eleven units have no channel 1 speech at all (§1.5).

**Counter-evidence**
* The effect is rare: 3 of 4,527 units get the agent's script on a non-1 channel.
* No external call record exists to confirm who is the agent; this rests on reading
  the content.

**Effect on the benchmark if adopted.** None yet. The task builder already rejects
any cutoff whose visible prefix contains an `other` channel, which is the
conservative response to exactly this uncertainty. What must **not** happen is a
global "channel 3 = agent" constant: the evidence says the role varies per unit.

### H3 — An unknown channel is IVR or a system announcement (plausible, but only for channel 5)

**Supporting evidence**
* Non-speech audio is already modelled as explicit events attached to channels 1 and
  2, so a channel is not intrinsically a speech-only stream.
* Channel 5 carries only two `UNCLASSIFIED` events and no speech.

**Counter-evidence**
* Channels 3 and 4 carry ordinary conversational Chinese answered by the channel 1
  speaker, which a recorded announcement does not produce.
* Only channel 5 is event-only, and its whole population is two records.

**Effect on the benchmark if adopted.** Zero for channel 5: event records are already
excluded from visible context and are never eligible as a decision point.

### H4 — An unknown channel is a recording or transcription artifact (unknown)

**Supporting evidence**
* A few channel 4 records restate wording that also appears on channel 1 or 2, which
  is consistent with an ASR boundary artifact.

**Counter-evidence**
* Zero overlapping records across the whole corpus, so an unknown channel never
  coincides in time with channel 1 or 2.
* Most channel 3/4 speech contains content that appears on no other channel.
* Unknown records carry their own event records and their own truncation markers,
  which pure duplication would not produce.

**Effect on the benchmark if adopted.** Deduplication would change turn counts but
not the meaning of a visible prefix.

### H5 — An unknown channel is a second agent-side leg (supervisor/consultation) (plausible, unverified)

**Supporting evidence**
* One unit opens with two speakers complaining about calling customers before the
  called party answers (3-c), which reads as two colleagues in the same room.
* One unit has the agent's script on channel 3 while channel 1 holds only events
  (3-a).

**Counter-evidence**
* In most units the unknown speaker answers in the customer's role, not the agent's.
* There is no supervisor or consultation marker anywhere in the corpus.

**Effect on the benchmark if adopted.** A second agent leg would break the assumption
that exactly one historical-agent turn follows a customer turn.

### Confidence summary

| Hypothesis | Confidence | Basis |
|---|---|---|
| H1 distinct additional speaker | Strongly supported by evidence | Multiple independent content readings plus the source speaker-attribute field |
| H2 channel number is not a fixed role | Strongly supported by evidence | Three units with the agent script on channel 3; 11 units without channel 1 speech |
| H3 IVR / system announcement for channel 5 | Plausible but unverified | Only channel 5 is event-only; population is two records |
| H4 recording/transcription artifact | Unknown | Zero overlaps and mostly unique content argue against it, but the sample of duplicated-looking records is too small |
| H5 supervisor / consultation leg | Plausible but unverified | Two suggestive units, no marker in the corpus |
| Official vendor channel specification | **Absent** | No such document exists in the workspace |

---

## 4. What this means for the benchmark

### 4.1 The production mapping is unchanged

```python
1: "historical_agent"
2: "customer"
# 3, 4, 5 → "other"   (unknown, pending human review)
```

`cm_outbound_bench.decisions.channels.assert_production_mapping_unchanged()` runs
inside the audit and in the test suite, so a future change cannot promote an unknown
channel silently. Every unknown-channel registration reports
`automatic_assignment_allowed = False` and `approved_mapping = "none"`.

### 4.2 Quantified impact of each candidate interpretation

Measured by rebuilding the task set under each hypothetical mapping, one corpus pass,
same builder configuration (`max_tasks_per_call=3`, `min_visible_turns=2`,
`min_cutoff_gap=4`, `even_spread`):

| Interpretation | Tasks | Units with tasks | Δ tasks | Δ units | Changes production mapping | Approved |
|---|---:|---:|---:|---:|---|---|
| I0 keep `other` (production) | 10,116 | 4,253 | 0 | 0 | no | — |
| I1 unknown → `customer` | 10,256 | 4,297 | **+140** | +44 | yes | **no** |
| I2 unknown → `historical_agent` | 10,224 | 4,296 | +108 | +43 | yes | **no** |
| I3 channel 3 → `historical_agent` only | 10,218 | 4,295 | +102 | +42 | yes | **no** |
| I4 exclude affected units entirely | 10,002 | 4,188 | **−114** | −65 | no | **no** |

Affected population in the current construction:

* 115 units contain an unknown channel; 93 contain unknown-channel speech.
* 71 of the 10,116 tasks live in a unit that also has unknown-channel speech
  (dev 14, test 15, train-f 26, train-r 16).
* 173 candidate cutoffs were rejected with reason `other_speaker_in_prefix`.

The current construction is the most conservative of the measured options: it is
only 114 tasks away from dropping the affected units entirely, and it never guesses.

### 4.3 Risks to keep in view

| Concern | Assessment |
|---|---|
| Conversation reconstruction | Low. Unknown records are on the timeline in correct order; excluding or including them does not change record order. |
| Agent-visible context | **Present.** A third party's words may be business-relevant. Today they are simply absent from every prefix, so the agent may miss information a human would have heard. |
| Decision-point identification | **Present but contained.** 173 cutoffs are rejected rather than guessed. If channel 3/4 turn out to be legitimately customer-side, some of those cutoffs are currently lost. |
| Business decision correctness | **Present.** For the ~3 units where the agent speaks on channel 3, a cutoff built on the channel-1/channel-2 assumption could attribute the wrong words to the wrong side. Those units are exactly the ones the `other` rejection protects against today. |
| Response quality | Low. Response quality is judged on the agent output, not on channel composition. |
| Future full-duplex evaluation | **Material.** If the benchmark later models a live conversation, the number of simultaneous participants per call must be known. H1 and H5 disagree on whether an unknown channel is a customer-side or agent-side participant, and that distinction decides whether a full-duplex simulator needs a third-party role. |

---

## 5. What a reviewer is asked to decide

1. **Is H1 accepted, that channels 3 and 4 are additional distinct speakers?** If
   yes, is the correct role `customer` (an additional respondent), a separate
   `third_party` role, or context-dependent?
2. **Is H2 accepted, that the channel number is not a fixed role?** If yes, the
   production mapping needs a documented per-unit validation step before it may be
   called exact, and the `1 = agent` assumption must be stated as a regularity rather
   than a guarantee.
3. **What is channel 5?** Two `UNCLASSIFIED` events cannot support any claim. The
   options are `unresolved`, `system_event_stream`, or `ignore_the_channel`.
4. **Which interpretation should the benchmark use?** I0 (keep `other`) is the
   current behaviour. I1/I2/I3 require approval and a re-run of task construction.
   I4 (drop affected units) is available if the researcher prefers a smaller but
   cleaner benchmark.
5. **Is a vendor channel specification obtainable?** That single document would
   settle H1–H5. It does not exist in this workspace.

No answer is assumed here. Until an answer is recorded, channels 3, 4 and 5 remain
`unknown` and the benchmark keeps its conservative behaviour.

---

## 6. Reproduction

```bash
export CM_OUTBOUND_CORPUS_DIR=../data/deidentified/cm_outbound_v2
uv run cm-outbound decision audit-channels
# writes outputs/decision/channel_review/ plus a JSON summary
```

`decision audit-channels` recomputes every number in this report from the corpus,
rewrites the review package deterministically, verifies the production mapping is
unchanged, and exits non-zero if it is not.
