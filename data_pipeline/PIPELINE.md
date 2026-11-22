# cm_outbound_v2 de-identification pipeline

Local, offline, standard-library-only pipeline that turns the raw CM outbound
TXT transcripts into a masked benchmark dataset. It is deliberately separate
from benchmark runtime code. The concise entry point is
[`data_pipeline/README.md`](README.md); this file is the implementation
reference.

v2 (pipeline 2.0.0) fixes the F1-F11 findings of the second-stage audit of
`cm_outbound_v1`. The record schema is still `1.0`; the manifest is `2.0`.
The v1 pipeline (`1.0.0`) is historical: its output is retained under
`<project-parent>/data/deidentified/cm_outbound_v1/`, and the v1-era
protected-mapping design is withdrawn in favour of the no-mapping model below.

## Why v2 exists: repair record (F1–F11)

The independent second-stage audit of `cm_outbound_v1` produced eleven findings.
The original repair report was a one-time Chinese-language delivery document and
was removed during repository cleanup; its engineering evidence is preserved in
this section so the rationale and the verified outcome are not lost.

| ID | v1 finding | v2 repair | Verified evidence |
|---|---|---|---|
| F1 (P0) | Classification used the exact longest-run length, so a mobile embedded in a 12–14 digit run was neither masked nor quarantined. v1 delivered 33 records / 43 `1[3-9]` substrings. | Chinese/ASCII digit normalisation + offset map + 11-digit sliding window; the whole entity is replaced or the unit is quarantined; 2/3-record split merge. | Final v2: 132 embedded-phone replacements, 102 merged split groups; scanner 0 findings. |
| F2 (P0) | 6–14 digit account/QQ/order/broadband numbers were handled only with keyword context; 9–14 digit runs with no context passed through. | 9–14 digit window first; non-phone → `<ACCOUNT>`; 10–12 digit `0/1` phone-like → quarantine; 6–8 digit kept only with business unit/price context, otherwise `<ACCOUNT>` + `short_unclassified_digit_masked`; 15/18 ID; 16–19/≥20 card. | 315 `<ACCOUNT>` occurrences (v1: 26), of which 248 occurrences (243 delivered records) are the short-context mask; 0 phone-shaped 6–8 digit residues in the delivered set. |
| F3 (P0/P1) | The scanner shared the redactor's assumptions, creating a correlated blind spot. | `privacy_scan.py` rewritten as an independent implementation (own digit table, NFKC, run construction, windows, name/address rules; slightly wider gap tolerance). | 15 residual units found before publication and quarantined; final rescan 0 findings. |
| F4 (P1) | Output/input containment was not validated. | `validate_output_path()` resolves real paths and rejects output=input, output inside input, parent inside input, symlinks and paths outside the approved root before any directory is created. | Path tests pass; rejection occurs before any write. |
| F5 (P1) | Key handling could chmod unrelated ancestor directories. | Only tool-created directory levels are chmodded `0700`; new key `0600`; an existing key is reused without chmod; symlinks rejected; key restricted to a dedicated root. | Key tests pass; the v1 key is reused and `transcript_id`s are stable. |
| F6 (P1) | The address window was 40 characters, so building/room tails could survive. | Window extends to the sentence end or 80 characters; structured matches absorb building/room tails; an unprovable boundary quarantines (`ADDRESS_BOUNDARY`). | Long-address test has no digit residue; >80-char boundary quarantined; 121 address replacements. |
| F7 (P2) | Any bracketed text could become an event and drop quoted speech. | Only a fixed vocabulary, the `SSTEM` alias and a strict uppercase tag become `EVENT`; everything else stays `SPEECH`. | Event and quoted-speech tests pass; no `UNKNOWN` events in delivered data. |
| F8 (P1) | The manifest lacked runtime, source hashes and an input fingerprint. | Manifest adds `runtime`, `environment`, `input`, `output.files` and source SHA-256 values. | Recorded Python 3.12.16 and source hashes match the files. |
| F9 (P1) | Count granularity was undeclared. | `granularity` plus occurrence/record/transcript counts and per-rule breakdowns. | Rule-level occurrences compose to the total placeholder count. |
| F10 (P1) | There was no structural assertion before writing. | Per-unit structural gate before staging, then read-back revalidation before atomic promotion. | 4,527 delivered units passed with 0 problems, twice. |
| F11 (P1) | Duplicate IDs relied only on HMAC uniqueness. | Explicit `CALL_[0-9a-f]{24}` format and uniqueness assertion before publication. | 4,527 unique IDs, 0 duplicates. |

### Verified final v2 run (2026-11-19, CPython 3.12.16, offline)

* Input: 4,738 files / 10,136,132 bytes / aggregate SHA-256
  `c6c0b17d…dd9c92f`; identical before and after processing
  (`integrity.unchanged = true`).
* Delivered: 4,527 transcript units / 137,178 records (94,428 speech + 42,750
  event); quarantined: 211 units / 7,545 records.
* Quarantine reasons (213 occurrences over 211 units; two units carry two
  reasons): `AMBIGUOUS_PHONE` 128, `ADDRESS_AMBIGUOUS` 53, `PRIVACY_RESIDUAL`
  15, `SPLIT_PHONE_AMBIGUOUS` 8, `CHILD_CHANNEL` 6, `AMBIGUOUS_PLUS` 3.
* The independent scanner examined 4,542 candidate units, found 15 residual
  units (`ADDRESS_RESIDUAL` 2, `NAME_RESIDUAL` 1, `PHONE_SPLIT_RESIDUAL` 6,
  `PLACEHOLDER_BOUNDARY_RESIDUAL` 6), quarantined all 15, and reported 0
  findings on the final `calls.jsonl` (second pass also 0).
* Tests: 101/101 pipeline unit tests passed under Python 3.12.16; no network,
  API, cloud service, NER or online model was used.
* Hardening iteration: the first v2 publication (4,521 delivered / 217
  quarantined) was archived unmodified at
  `<project-parent>/data/rejected/cm_outbound_v2_pre_hardening_20261119/` after
  an extra residual audit found 226 units with unclassified 6–8 digit entities
  (122 phone-shaped).
  The rule above was added and the final v2 (4,527 / 211) was reprocessed from
  the original source. The archived dataset is not a deliverable and must not be
  used.

## Safety model

| Control | Implementation |
|---|---|
| Source access | `open(path, "rb")`, strict UTF-8, never opened for writing |
| Raw text propagation | Stays in process memory; never logged, printed, or serialized |
| Error messages | Stable reason codes only (`PARSE_*`, `AMBIGUOUS_*`, ...) |
| Output identifiers | `transcript_id = CALL_` + HMAC-SHA256(file name)[:24] |
| HMAC key | `~/.local/share/cm-outbound-bench/keys/transcript_id_hmac.key`; existing key reused, never regenerated |
| Key permissions | tool-created directories `0700`, new key file `0600`; existing directories are never chmodded; symlinks rejected |
| Mapping table | None is produced; de-identification is not reversible |
| Network | No HTTP client, no model, no third-party package |
| Original files | Snapshot (name, size, per-file SHA-256) in memory before and after; aggregate fingerprint must match |
| Output path | resolved and checked before any directory creation: not input, not inside input, parent not inside input, and inside the approved `<project-parent>/data/deidentified` root |
| Partial output | Written to a staging directory, read back and revalidated, then atomically promoted |

## Usage

```bash
cd /mnt/d/flowchart-baseline-dist/cm-outbound-bench
./.venv/bin/python -B data_pipeline/deidentify.py \
  --input  /mnt/d/flowchart-baseline-dist/data/TXT \
  --output /mnt/d/flowchart-baseline-dist/data/deidentified/cm_outbound_v2 \
  --expect-baseline-sha256 c6c0b17d2199cf98a8822766f9714d549a93e2cee27e193ae242de438dd9c92f
```

`--dry-run` computes and prints the manifest without writing the dataset.
The interpreter must be CPython **3.12.16**; any other version exits with
code 8 before reading the source.

Exit codes: `0` success, `2` missing/invalid input, `3` output exists, `4`
baseline fingerprint mismatch, `5` source changed during run, `6` residual
privacy findings remain after quarantine, `7` output path rejected, `8`
interpreter mismatch, `9` structural gate failed, `10` publish/readback gate
failed, `11` source read failure, `12` key path rejected.

## Parsing rules

* One line = `[start,end]` `TAB` channel `TAB` gender,language `TAB` payload.
* Time values are parsed as `Decimal` seconds and rounded half-up to integer
  milliseconds; `end_ms >= start_ms` is enforced.
* Records must be non-decreasing in start time and must not overlap the
  previous record (checked on the original `Decimal` values).
* Only CRLF line endings and exactly 4 tab-separated fields are accepted.
* Any parse failure quarantines the whole transcript unit; the exception
  message never contains the offending line.

## Digit normalisation (F1/F2)

`iter_digit_runs()` converts Chinese spoken digits (`零〇洞一幺二两三四五六七八九
七拐`), ASCII and full-width digits into a digit stream and keeps an offset map
back to the original characters. Up to six consecutive punctuation/space
characters may separate digits of one entity; sentence-ending punctuation ends
the entity. This is a controlled normalisation, not an unbounded regex.

Every digit entity is classified in this order:

| Entity | Action |
|---|---|
| `>= 20` digits | `<CARD_NO>` + `long_digit_run_masked` |
| 15 or 18 digits | `<ID_NO>` (date sanity recorded as `id_pattern_masked`) |
| 16-19 digits | `<CARD_NO>` |
| 11 digits `1[3-9]` | `<PHONE>` |
| 9-14 digits containing any 11-digit `1[3-9]` window | whole entity → `<PHONE>` + `phone_embedded_long_run` |
| 9-14 digits, no phone window, 10-12 digits starting `0`/`1` | quarantine `AMBIGUOUS_PHONE` |
| 9-14 digits, no phone window, otherwise | `<ACCOUNT>` + `account_long_run_masked` |
| 7-8 digits after 电话/座机/手机/号码/联系/回拨/拨打 | `<PHONE>` |
| 6-8 digits after account/order/verification/password context | matching placeholder |
| 6-8 digits with an explicit business unit/period/price context | preserved (business amount/period data) |
| 6-8 digits with no context at all | `<ACCOUNT>` + `short_unclassified_digit_masked` |

The whole digit entity is always replaced, so leading/trailing digits of a
12-14 digit run can never survive next to `<PHONE>`.

For 6-8 digit entities, "business context" means an immediately following
unit/period token (`元`, `GB`, `个月`, `分钟`, ...) or an immediately preceding
plan/price token (`套餐`, `月租`, `流量`, `合约`, ...). Everything else is an
unclassified identifier and is masked; this closes the v1 residue of short
phone/QQ/account fragments that had no keyword nearby.

### Cross-record splits

Only adjacent `SPEECH` records in the same transcript unit and the same
channel are considered. The trailing fragment of one record and the leading
fragment of the next must compose exactly one valid 11-digit mobile, with a
gap in `[-50, 1500]` ms and no `EVENT` between. Two- and three-record splits
are supported; both sides become `<PHONE>` with `phone_split_merged`.

A plausible composition that fails the channel/gap constraints is quarantined
as `SPLIT_PHONE_AMBIGUOUS` rather than kept. A split separated by a long gap
(over 3 s) is treated as unrelated fragments.

## Other redaction rules

| Entity | Detection | Placeholder |
|---|---|---|
| Name | surname (single/compound) + 0-2 chars + 先生/女士/小姐/师傅/阿姨/叔叔/老师/经理/主任/总经理/总; `姓X`; `我叫X` | `<NAME>` (honorific kept) |
| Address | trigger word + address-marker window up to the sentence end or 80 chars; structured 省/市/区/路/号 chain; community + building/room chain; structured matches are extended through an immediately following building/door/room tail | `<ADDRESS>` |
| Account | 6-24 digits after 账号/账户/卡号/银行卡/户号/工号/订单/流水号/会员号/宽带/QQ/微信 | `<ACCOUNT>` |
| Verification code | 4-8 digits after 验证码/校验码/动态码/短信码 | `<VERIFICATION_CODE>` |
| Password | 4-20 digits after 密码/口令; Latin password after 密码/口令 | `<PASSWORD>` |
| E-mail / URL / IM / plate | dedicated regular expressions | `<EMAIL>` `<URL>` `<CONTACT>` `<PLATE>` |
| Unexplained `￥` | leading full-width yen sign, semantics unknown | `<ASR_TOKEN>` + `yen_token` flag |

Trailing `+` is stripped and converted to `is_truncated`; a `+` elsewhere
quarantines the unit (`AMBIGUOUS_PLUS`). Event payloads are never redacted.

## Event labels (F7)

Only these payloads become `EVENT` records:

* `[SYSTEM]`, `[ENS]`, `[DTMF]`, `[MUSIC]`, `[SONANT]`, `[LAUGH]`,
  `[UNCLASSIFIED]` (via `[*]`), `[UNKNOWN]`;
* `[SSTEM]` → `SYSTEM` + `event_spelling_variant`;
* a strict uppercase vendor tag matching `^[A-Z][A-Z0-9_]{1,23}$` →
  `UNKNOWN` + `malformed_event_tag`.

Anything else (`[MU SIC]`, `[你好]`, `[system]`, ...) stays `SPEECH` text and
is preserved, so quoted speech is never silently dropped.

## Fail-closed quarantine

`quarantine.jsonl` entries contain only `transcript_id`, `reason_codes`,
`affected_seq`, `status`. Whole units are quarantined for:

* parse/decode/line-ending/field/timestamp/profile failures;
* any channel whose speaker attribute is `儿童` (`CHILD_CHANNEL`);
* 10-12 digit entities that look phone-like but are not a valid mobile
  (`AMBIGUOUS_PHONE`);
* an address boundary that cannot be proven inside the 80-char window
  (`ADDRESS_BOUNDARY`);
* a personal-address trigger with no extractable address
  (`ADDRESS_AMBIGUOUS`);
* a plausible cross-record phone split that fails channel/gap constraints
  (`SPLIT_PHONE_AMBIGUOUS`);
* `+` in the middle of a speech payload (`AMBIGUOUS_PLUS`);
* any structural validation failure (`SCHEMA_INVALID`);
* any residual finding from the independent scanner (`PRIVACY_RESIDUAL`).

## Verification

`data_pipeline/privacy_scan.py` is a second, independent implementation. It
does not import `deidentify.py`; it has its own digit table, NFKC
normalisation, run construction, sliding-window logic and surname/address
checks. Its normalisation gap tolerance is deliberately slightly larger than
the redactor's.

It is invoked three times:

1. in-process, on every candidate call before writing;
2. a second in-process pass after residual units are removed;
3. a read-back pass over the written `calls.jsonl` before promotion.

Classes checked: 11-digit mobile windows (including inside longer entities and
punctuation-separated runs), 9-14 digit account entities, 15/18-digit IDs,
16-19 digit cards, 7-8 digit phone-context numbers, 6-8 digit account-context
numbers, verification/password contexts, names (`X先生/老师/经理/主任/总`,
`姓X`, `我叫X`), addresses, e-mail/URL/IM, placeholder-adjacent digits, and
2-/3-record split candidates. Findings are emitted only as code,
`transcript_id`, `seq` and counts; matched text is never returned.

Standalone rescan:

```bash
./.venv/bin/python -B data_pipeline/privacy_scan.py \
  --calls ../data/deidentified/cm_outbound_v2/calls.jsonl \
  --findings-json /tmp/v2_scan_findings.json
```

Exit code is non-zero if anything is found.

## Publication gate (F10/F11)

Before `calls.jsonl` is written, every unit is checked for: non-empty and
well-formed unique `transcript_id`; `seq` 0..n-1; integer times with
`end_ms >= start_ms`, non-decreasing starts and no overlap; channel in 1-5;
role in the allowed enum; correct SPEECH/EVENT field combination; event in the
allowed enum; `text_masked` free of raw/forbidden fields and control
characters; placeholders in the allowed whitelist with no stray angle
brackets; boolean `is_truncated`; only allowed ASR/redaction flags; and
`duration_ms == max(end_ms)`. Empty text must be truncated and flagged
`empty_after_mask`.

After writing to staging, `calls.jsonl` is read back and revalidated, the
quarantine file is schema-checked, and the independent scanner runs again.
Only a clean read-back is atomically promoted with `os.replace`.

## Manifest (F8/F9)

`manifest.json` records:

* `runtime`: CPython version, platform, pipeline/scanner source SHA-256,
  schema and manifest versions;
* `environment`: required/actual Python and match flag;
* `input`: file count, total bytes, aggregate fingerprint, baseline result,
  record/speech/event counts (no file names, no per-file hashes);
* `output`: delivered/quarantined units, record counts, file sizes and hashes;
* `granularity`: explicit occurrence / record / transcript definitions;
* `processing.entities`: per entity occurrences, affected records and affected
  transcripts, for delivered and all-processed sets;
* `processing.phone|account|name|address|identity`: rule-level breakdowns
  (11-digit, embedded-long-run, split-merge, landline; account context vs
  long run; name sub-rules; address trigger vs structured; ID/card);
* `processing.quarantine`: units, records, speech/event records, reasons;
* `processing.event_distribution`, `empty_text`, `asr_flags`;
* `privacy_scan`: scanned calls, findings, codes, residual units quarantined,
  second-pass result;
* `integrity`: pre/post file count, bytes and aggregate SHA-256, unchanged flag.

## Output

`calls.jsonl`, `quarantine.jsonl`, `manifest.json`, `README.md` under
`<project-parent>/data/deidentified/cm_outbound_v2/`. `calls.jsonl` contains
only masked text and opaque ids; no original file names, gender/language
profiles, raw fields or per-record source paths.

## Known limitations

* One file is treated as one transcript unit; the source has no `call_id`, so
  this is an engineering assumption, not a verified fact.
* `role` is `UNKNOWN` for every record; channel 1/2 semantics are not
  confirmed by the business.
* Names without a honorific and addresses without markers may survive; the
  independent scanner quarantines only patterns it can recognise. A sampled
  human review is still required before external use.
* Business numbers of 9-14 digits are over-masked as `<ACCOUNT>`; 6-8 digit
  entities without an explicit business unit/price context are likewise
  over-masked. This is the safe direction but reduces business fidelity.
* Vendor semantics of `+`, `[*]`, `ENS`, `SONANT` remain inferred.
* Gender/language attributes are used only to detect the child channel and are
  never written to the output.
