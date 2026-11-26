# GitHub Security Audit

Security and data-protection audit of `cm-outbound-bench` before manual publication to
the private GitHub repository. Nothing was staged, committed or pushed during this
audit, and no credential value is reproduced anywhere in this report.

| Property | Value |
|---|---|
| Repository root | `D:/flowchart-baseline-dist/cm-outbound-bench` |
| Files examined (tracked + untracked, not ignored) | 5,071 |
| Git commits in history | 1 (`1d2f5d4`) |
| Files scanned for secret patterns | 5,066 text candidates (scan run before this report was added; report files are markdown and contain no secrets) |
| Content values scanned for residual identifiers | 421,158 |
| Verdict | **No publication blocker found in content. One governance blocker remains: owner confirmation of the private-hosting boundary.** |

---

## 1. Method

| Step | Tool / command | Scope |
|---|---|---|
| 1. Tracked-file inventory | `git ls-files`, `git ls-files --others --exclude-standard` | whole tree |
| 2. Secret pattern scan | independent regex scan (`sk-`, `AKIA`, `ghp_*`, `xox*`, JWT, private-key blocks, DB URIs, `api_key`/`token`/`password` assignments, bearer tokens) | every tracked + untracked not-ignored file |
| 3. Git history scan | `git grep -I -n -E <patterns> $(git rev-list --all)`; `git ls-tree -r` over every commit | all history |
| 4. Field-aware PII scan | JSON walk with identifier/hash keys excluded | all 3,642 JSON/JSONL files under `data/decision`, `outputs/decision`, `data/tasks`, `data/policy`, `data/synthetic` |
| 5. Content-only PII scan | scan restricted to conversation/label fields (`text`, `instruction`, `label`, `rationale`, `visible_context`, …) | 421,158 values |
| 6. Masking-coverage measurement | placeholder and honorific counting over 137,178 reconstructed records | `data/decision/calls/calls.jsonl` |
| 7. Raw-data exclusion | `git check-ignore -v` on the raw/corpus/audio/DB/credential patterns | whole tree |

Gitleaks was not available locally and was **not** downloaded; the independent
regex scan plus the field-aware JSON scan cover the same secret classes and add
structured PII checks that Gitleaks does not perform.

## 2. Secret scan result — working tree

**No real credential, token, private key or database connection string is present in
any file proposed for upload.**

All credential-shaped matches are deliberate test fixtures with obviously synthetic
values in the test suite. They are benign, non-functional placeholders and are listed
for completeness only.

| File | Line(s) | Category | Nature |
|---|---:|---|---|
| `tests/test_providers.py` | 28, 916 | `api_key` assignment | Synthetic `sk-test-…` / `sk-should-not-be-…` fixtures asserting redaction |
| `tests/test_providers.py` | 204 | e-mail pattern | Placeholder address inside a header-binding test |
| `tests/test_benchmark_tools.py` | 30, 155, 169, 207, 223, 273 | `api_key` assignment | Fixtures for secret-redaction assertions |
| `tests/test_benchmark_batch.py` | 104 | bearer token | Synthetic token used to prove error-text redaction |
| `tests/test_decisions_agent.py` | 95 | bearer token | Fake `sk-fake-…` value asserting the `Authorization` header shape |
| `tests/test_decisions_baseline.py` | 53 | `api_key` assignment | Fixture |
| `tests/test_decisions_runner.py` | 41 | `api_key` assignment | Fixture |
| `tests/test_tools.py` | 17 | `api_key` assignment | Fixture |

Non-secret code references (`resolve_api_key`, `ENV_API_KEY = "CM_JUDGE_API_KEY"`) only
name environment variables; they contain no value.

## 3. Git history findings

| Check | Result |
|---|---|
| Commits scanned | 1 |
| Hardcoded key material (`sk-`, `AKIA`, `ghp_*`, `xox*`, private-key block) in any commit | **none** |
| Credential files ever committed (`.env`, `*.pem`, `*.key`, `providers.toml`, `*.sqlite`, `*.p12`) | **none** — only `.env.example`, which contains no value |
| Raw corpus paths ever committed (`data/TXT`, `data/deidentified`, `data/corpus_db`, `data/raw`, `data/rejected`) | **none** |
| History rewrite required | **No** |

Because the repository has exactly one baseline commit, there is no historical
credential exposure to remediate, and no unreachable blobs from deleted secrets.

## 4. Raw transcript exclusion

| Path | Status | Evidence |
|---|---|---|
| `data/TXT/` (original raw export) | **outside the repository**, never committed | `git check-ignore` → `.gitignore:data/TXT/`; not present in history |
| `data/deidentified/` (`cm_outbound_v2` corpus) | **outside the repository**, never committed | ignore rule present; path resolves to `<project-parent>/data/deidentified/` |
| `data/corpus_db/`, `*.sqlite`, `cm_outbound_corpus.sqlite` | ignored | rule verified |
| `data/rejected/` (pre-hardening run) | ignored | rule verified |
| Raw audio (`*.wav`, `*.mp3`, `*.m4a`, `*.amr`, `*.silk`) | ignored | rule verified |

Code-level check: benchmark runtime code does not open the raw export. The only
reader is the protected `data_pipeline/deidentify.py`, and `docs/data_privacy.md` §2
records the requirement. `tests/` contain a guard test asserting the benchmark has no
raw-TXT access path.

## 5. De-identification inspection of the data proposed for upload

### 5.1 Structured identifier scan — content fields only

The scan deliberately excluded opaque-identifier and hash keys (`*_id`, `*_hash`,
`*_sha256`, `*_simhash`, `cache_key`, `evidence_refs`, `run_id`, `call_id`,
`task_id`, …) and inspected only fields that carry language: conversation turns, Task
instructions, Gold Label fields, adjudication rationales and step lists.

| Class checked | Findings |
|---|---:|
| CN mobile numbers `1[3-9]\d{9}` | 0 |
| CN resident ID numbers (15/18) | 0 |
| CN landline numbers | 0 |
| Bank/account card numbers (16–19 digits) | 0 |
| E-mail addresses | 0 |
| URLs containing credentials | 0 |
| API-key shapes (`sk-…`) | 0 |
| **Total** | **0 across 421,158 values** |

### 5.2 Apparent matches in a naive whole-file scan

A whole-file scan reports 1,641 apparent hits (1,343 landline-shaped, 203
mobile-shaped, 81 ID-card-shaped, 11 `assigned_secret`, 2 bearer, 1 e-mail). Every
one of them was resolved:

| Group | Count | Resolution |
|---|---:|---|
| Inside `CALL_<24 hex>`, `TASK_<20 hex>`, `RUN-<16 hex>`, `DUPGROUP_<hex>` identifiers | majority | Hex digit substrings, not telephone numbers |
| Inside `content_sha256`, `text_simhash`, `prompt_hash`, `config_hash`, `response_hash`, `cache_key` | majority | Hex digests |
| `evidence_refs` / `incorrect_tasks` / `excluded_conversation_ids` lists | 44 | Lists of the identifiers above |
| Dict keys of `split_by_call` / `group_of_call` | 10 | Call identifiers |
| Test fixtures | 13 | Synthetic placeholders (§2) |

None of them is a customer identifier.

### 5.3 Masking coverage measured on the reconstructed corpus

137,178 records (4,527 conversations). Placeholder counts:

| Placeholder | Count |
|---|---:|
| `<PHONE>` | 4,734 |
| `<ACCOUNT>` | 315 |
| `<ADDRESS>` | 121 |
| `<NAME>` | 118 |
| `<ASR_TOKEN>` | 921 |
| `<ID_NO>` | 18 |
| `<CARD_NO>` | 6 |
| `<PASSWORD>` | 1 |

These agree with the recorded v2 pipeline results (`<ACCOUNT>` 315, addresses 121),
which is independent confirmation that the delivered corpus is the one the pipeline
documented and not a partially-masked variant.

### 5.4 Residual personal-name check

226 honorific sequences survive (`先生` 176, `老师` 28, `经理` 15, `女士` 3, `小姐` 2,
`阿姨` 1, `叔叔` 1). Cross-checking the preceding characters against a 100+ surname
list yields **0** plausible surname + honorific personal names: the preceding text is
politeness or role wording, and honorifics attached to a masked name appear as
`<NAME>先生`. Role nouns such as 安装师傅 / 装维师傅 explain the `师傅` family.

### 5.5 Residual risk that inspection cannot close

| Risk | Status |
|---|---|
| Names without an honorific | Not detectable by pattern; may survive. Documented limitation of the v2 pipeline. |
| Addresses without a marker | 879 `路/街/道/小区/…` sequences remain, expected to be public business locations (e.g. 营业厅) plus possible personal addresses reaching a non-trigger sentence. **Requires human sampling before any distribution beyond owner + supervisor.** |
| Re-identification from surrounding context | A combination of plan, region, timing and complaint details can narrow a customer down even with identifiers masked. Not measurable automatically. |
| Speaker-role attribution | Channel mapping is a protocol decision; channels 3/4/5 unresolved. Not an identity leak by itself, but it limits what can be inferred confidently. |

No file was found that must be withheld for containing a residual identifier. The
§5.5 risks are properties of the whole dataset, not of individual bad files, so
excluding a subset would not remove them.

## 6. Tracked sensitive files

| Candidate | Verdict |
|---|---|
| `.env.example` | Safe — placeholders only, no value |
| `providers.example.toml` | Safe — host labels and `api_key_env` names, no credential |
| `providers.toml` | **Ignored, not tracked** — local configuration |
| `outputs/decision/traces/**` | Contains agent outputs and de-identified prefixes; no credential. `TraceManager` refuses to write a trace containing a configured secret. |
| `outputs/live_llm_validation/**/*.err`, `*.log` | 146 `.err` files totalling 10 KB, max 1.4 KB; scanned, no credential. Synthetic conversations only. |
| `outputs/decision/annotations/**` | Model annotation records; no credential; Class 2 |

## 7. Remaining security concerns

1. **Governance, not code (blocker before push).** No written data-use authorisation
   naming private GitHub hosting exists in the repository; the earlier privacy
   documentation treated the processed data as non-publishable. The owner must confirm
   the boundary before the datasets are pushed — see
   `github_release_readiness.md` §16 and `docs/data_privacy.md` §6.
2. **Residual re-identification risk** in the de-identified text (§5.5). Private
   hosting and a two-person audience are the mitigations relied on here; they are not
   a technical guarantee.
3. **Test fixtures contain credential-shaped strings.** Acceptable and intentional;
   they are non-functional placeholders. Anyone running a history scanner will see
   them and should read §2 first.
4. **Provider base URL in artifacts.** Group A traces record `base_url_host:
   api.deepseek.com` — a public provider host, not a credential or private URL.
5. **No CI secret scanning.** Deliberate: no `.github/` workflow was added, so the
   secret scan is a manual step. Re-run it after any future data refresh.

## 8. Conclusion

* No real secret is tracked, and none exists in Git history.
* No raw transcript, corpus database, audio file or credential file is tracked.
* The processed datasets contain no residual structured personal identifier in any
  content field, and no surviving surname + honorific name.
* The single outstanding blocker is the **data-governance confirmation**, which only
  the repository owner can give.
