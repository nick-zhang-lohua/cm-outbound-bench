# Domain Catalog

The benchmark organises work into four primary business domains and two
cross-cutting capabilities. The taxonomy is versioned (`1.0`) and seeded into
the corpus database as `PROPOSED`: it has not been validated by human review and
therefore never claims `ACTIVE`.

| Domain | Class | Scope |
|---|---|---|
| D01 Product & Package | Primary business | Plans, allowances, package contents, pricing, comparison, availability |
| D02 Promotion & Offer | Primary business | Promotions, gifts, points, eligibility, enrollment, validity, renewal, restrictions |
| D03 Billing & Recharge | Primary business | Monthly fees, charges, balance, arrears, recharge, payment, billing explanation |
| D04 Subscription & Account Lifecycle | Primary business | Upgrade, downgrade, plan change, cancellation, suspension, reactivation, closure, ownership transfer |
| D05 Identity & Ownership | Cross-cutting | Identity verification, real-name registration, ownership, authorised representatives, transfer conditions |
| D06 Notification & Fulfillment | Cross-cutting | SMS notification, confirmation, delivery status, reminders, follow-up, fulfilment |

The taxonomy is stored in `domain_taxonomy` (`docs/corpus_database.md`) and used
by both the corpus classifier and the runtime domain registry
(`benchmark/domains.py`).

---

## 1. Classification method (Phase 2)

Classification is a local, deterministic, explainable rule pipeline over the
corpus database. No external model and no network access is involved.

1. **Eligibility.** Only `SPEECH` records with non-empty masked text of at least
   four characters are eligible. Event records carry no text and stay
   unclassified; that is a method limit, not an absence of business content.
2. **Lexical evidence.** Each domain has curated *strong* and *weak* term
   groups. Terms are matched as whole phrases of at least two characters. Before
   counting a match, a short negation window is checked: `没有优惠` does not
   evidence a promotion, `不取消` does not evidence a cancellation.
3. **Threshold.** A record receives a domain when it has at least one strong
   group **or** at least two distinct weak term matches. Weak evidence alone
   never assigns a domain.
4. **Multi-label.** Domains are scored independently. Cancelling a promotion is
   genuinely both D02 and D04, and the pipeline keeps both labels.
5. **Confidence.** `min(0.95, 0.45 + 0.15 × score)`, where a strong group scores
   1.0 and a weak group scores 0.5, strong capped at 3 and weak at 2.
6. **Unit rollout.** A transcript unit inherits every domain supported by its
   records; the domain with the most supporting records becomes `is_primary`.
7. **Ambiguity.** Records with two or more labels whose confidence difference is
   below 0.10 are counted as ambiguous. They keep every label and are never
   collapsed to one.

Every stored assignment carries `label_method = RULE_CANDIDATE` and
`review_status = CANDIDATE`. Nothing in this pipeline produces ground truth.

### Reproducibility

```bash
cm-outbound corpus classify
cm-outbound corpus coverage-report --out outputs/corpus/DOMAIN_COVERAGE_REPORT.md \
    --json-out outputs/corpus/domain_coverage.json
```

The corpus database itself is not distributed with this repository, so these
commands require the authorised local corpus import described in
[corpus_database.md](corpus_database.md).

The complete rule set is dumped by `classification.lexicon_manifest()`, so a
classification run can be reproduced exactly from the database plus that file.

---

## 2. Coverage (rule-based candidates, not prevalence)

Verified on the imported corpus (4,527 units / 137,178 records):

| Metric | Value |
|---|---:|
| Speech records | 94,428 |
| Eligible speech records (text ≥ 4 chars) | 68,184 |
| Skipped speech records | 26,244 |
| — empty after masking | 3,328 |
| — text too short | 22,916 |
| Records with at least one domain label | 32,666 |
| Eligible records left unclassified | 35,518 |
| Multi-label records | 11,260 |
| Ambiguous records | 9,803 |
| Transcript units | 4,527 |
| Units with at least one label | 4,382 |
| Units left unclassified | 145 |
| Units with two or more domains | 4,096 |

Per-domain:

| Domain | Record labels | % of eligible | Unit labels | % of units | Mean confidence |
|---|---:|---:|---:|---:|---:|
| D01 Product & Package | 13,446 | 19.72 | 2,977 | 65.76 | 0.611 |
| D02 Promotion & Offer | 9,479 | 13.90 | 2,777 | 61.34 | 0.620 |
| D03 Billing & Recharge | 6,237 | 9.15 | 2,966 | 65.52 | 0.604 |
| D04 Subscription & Lifecycle | 7,728 | 11.33 | 2,820 | 62.29 | 0.618 |
| D05 Identity & Ownership | 7,559 | 11.09 | 3,672 | 81.11 | 0.606 |
| D06 Notification & Fulfillment | 2,939 | 4.31 | 1,719 | 37.97 | 0.633 |

These percentages measure **lexical evidence coverage**, not business-intent
prevalence. A unit labelled D02 means the rules found promotion vocabulary in
it; it does not mean the call was a promotion call, and it says nothing about
how common promotion calls are.

Top co-occurring unit pairs: D01+D02, D03+D05, D04+D05, D03+D04, D01+D05. The
full co-occurrence matrix is emitted by `corpus coverage-report`.

---

## 3. Candidate versus reviewed labels

| Status | Count |
|---|---:|
| Candidate (`RULE_CANDIDATE` / `CANDIDATE`) | 64,319 assignments |
| Human-reviewed | **0** |
| Adjudicated | **0** |

**No human review has been performed.** The review workflow exists and is
local-only:

```bash
# 1. Export a stratified sample (no text by default).
cm-outbound corpus export-review-sample --out outputs/review/sample.json

# 2. To inspect samples you must opt in explicitly; the file is written
#    owner-only and must stay in the authorised local environment.
cm-outbound corpus export-review-sample --out outputs/review/sample.json \
    --include-text

# 3. Apply the completed decisions.
cm-outbound corpus apply-review --review outputs/review/sample.json \
    --reviewer "<reviewer-id>"
```

Stratification covers D01–D06, multi-label, ambiguous and unclassified cases,
with a recorded seed (default `20261120`) so the sample is reproducible.

Review decisions never overwrite each other: a re-classification preserves any
target that already carries a human decision.

---

## 4. Known limitations

1. **Speaker roles are unknown** for all 137,178 records. Channel identifiers are
   preserved verbatim and are never interpreted as speakers.
2. **Single language, single lexicon.** The rules are Chinese-language and
   curated by hand; recall outside their vocabulary is zero by construction.
3. **Event records are never classified.** 42,750 records (31 %) carry no text.
4. **No validated business intent labels.** Intent and speech-act labelling is
   not part of this release; the tasks use explicit scenario metadata instead.
5. **No statistical representativeness claim.** The review sample is stratified
   for coverage, not designed as a probability sample.
