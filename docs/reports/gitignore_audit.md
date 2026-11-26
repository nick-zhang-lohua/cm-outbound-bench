# .gitignore Audit

Review of the `.gitignore` redesign that makes the approved processed research data
trackable while keeping raw, confidential and local artifacts out of Git.

| Property | Value |
|---|---|
| File | `.gitignore` (123 lines) |
| Previous policy | `data/decision/` and `outputs/decision/` were **blanket-ignored** |
| New policy | Those two blanket rules were removed; narrow exclusion rules retained |
| Tracked files | 1,327 (unchanged from the baseline commit) |
| Untracked but not ignored | 3,744 |
| Decision-dataset files eligible for tracking | 3,641 |
| Decision-dataset files ignored | **0** |
| Tracked files that are ignored | **0** |

---

## 1. Why the old file was wrong for this publication

The previous `.gitignore` contained:

```gitignore
# Real-transcript decision benchmark artifacts: reconstructed conversations,
# task text, split manifests, traces and reports are all local-only.
data/decision/

# Group A baseline traces and reports contain conversation-derived content.
outputs/decision/
```

That was correct under the earlier cleanup-only instruction and wrong for this
publication: it silently excluded exactly the datasets, split artifacts, frozen Gold
Labels and Group A records that the repository must now contain. A blanket rule is also
the dangerous kind of rule, because it keeps working after the policy changes.

## 2. New structure

The file now has six labelled sections and a closing "Tracked by design" list.

| Section | Purpose |
|---|---|
| Python environments | `.venv/`, `.venv*/`, `venv/`, `env/` |
| Python build, cache, coverage | `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage*`, `coverage.xml`, `htmlcov/`, `pytest-of-*/`, `build/`, `dist/`, `*.egg-info/` |
| Local secrets | `.env`, `.env.*` with `!.env.example`, `providers.toml`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.enc` |
| Raw and unapproved corpus | `data/TXT/`, `data/raw/`, `data/raw_txt/`, `data/interim/`, `data/private/`, `data/secure/`, `data/entity_maps/`, `data/deidentified/`, `data/rejected/`, `data/corpus_db/`, `data/audio/`, raw audio extensions, SQLite files and journals |
| Local runtime scratch | `logs/`, `/*.log`, `outputs/tmp/`, `outputs/local/`, `outputs/review/`, `outputs/corpus/`, `outputs/debug/` |
| OS and editor metadata | `.DS_Store`, `Thumbs.db`, `desktop.ini`, `.idea/`, `.vscode/`, `*.swp`, `*.swo`, `*.tmp` |
| **Tracked by design** | Comment block listing every approved dataset path so a future broad pattern is an obvious mistake |

## 3. Verification — approved data is NOT ignored

Method: enumerate every file under `data/decision/` and `outputs/decision/` and pipe
the list through a single batched `git check-ignore --stdin`.

```bash
Get-ChildItem -Recurse -File data\decision,outputs\decision \
  | ForEach-Object { $_.FullName.Substring(...).Replace('\','/') } \
  | git check-ignore --stdin
```

| Result | Count |
|---|---:|
| Decision-dataset files examined | 3,641 |
| Files reported as ignored | **0** |

Spot checks covering each dataset family, all reported as *not ignored*:

| Path | Status |
|---|---|
| `data/decision/tasks/tasks.jsonl` | not ignored |
| `data/decision/tasks/references.jsonl` | not ignored |
| `data/decision/tasks/tasks.manifest.json` | not ignored |
| `data/decision/calls/calls.jsonl` | not ignored |
| `data/decision/splits/split_manifest.json` | not ignored |
| `data/decision/samples/dev_baseline_evaluation_sample.json` | not ignored |
| `data/decision/annotations/*/annotation_packets.jsonl` | not ignored |
| `data/decision/business_sources/inventory.json` | not ignored |
| `outputs/decision/annotations/train_r/final_references.jsonl` | not ignored |
| `outputs/decision/annotations/dev/final_references.jsonl` | not ignored |
| `outputs/decision/annotations/manifests/frozen_annotation_manifest.json` | not ignored |
| `outputs/decision/evaluation/group_a/adjudicated_results.jsonl` | not ignored |
| `outputs/decision/traces/20261125T164504Z-group-a-dev-eval/run_set_manifest.json` | not ignored |
| `outputs/decision/traces/…/RUN-*.json` (400 files) | not ignored |
| `outputs/decision/channel_review/*` | not ignored |
| `outputs/decision/reports/*` | not ignored |
| `data/tasks/`, `data/policy/`, `data/synthetic/` (legacy) | not ignored |
| `outputs/live_llm_validation/`, `outputs/runs/` (legacy evidence) | not ignored |
| `.env.example` | not ignored (negation rule works) |

## 4. Verification — excluded data IS ignored

`git check-ignore -v` rule attribution for every exclusion class:

| Path tested | Matching rule (file:line) |
|---|---|
| `data/TXT/x.txt` | `.gitignore:68:data/TXT/` |
| `data/deidentified/x/calls.jsonl` | `.gitignore:75:data/deidentified/` |
| `data/corpus_db/x.sqlite` | `.gitignore:77:data/corpus_db/` |
| `data/rejected/x.jsonl` | `.gitignore:76:data/rejected/` |
| `data/raw/x.txt` | `.gitignore:69:data/raw/` |
| `data/interim/x`, `data/private/x`, `data/secure/x`, `data/entity_maps/x` | lines 71–74 |
| `data/audio/a.silk` | `.gitignore:78:data/audio/` |
| `x.sqlite`, `x.sqlite-wal` | `.gitignore:90` / `:93` |
| `a.wav`, `a.mp3` | `.gitignore:81` / `:82` |
| `a.pem`, `a.key`, `a.p12`, `a.pfx`, `a.enc` | lines 53–57 |
| `.env`, `.env.prod` | `.gitignore:49` / `:50` |
| `providers.toml` | `.gitignore:52` |
| `outputs/tmp/x`, `outputs/debug/x`, `logs/x.log` | `.gitignore:106` / `:110` / `:104` |
| `x.pyc`, `x.pyo`, `__pycache__/x.pyc` | `.gitignore:31` / `:30` |
| `.ruff_cache/x`, `.pytest_cache/x`, `.mypy_cache/x` | `.gitignore:33` / `:32` / `:34` |
| `.coverage`, `.coverage.abc`, `coverage.xml`, `htmlcov/x` | `.gitignore:35–38` |
| `build/x`, `dist/x`, `x.egg-info/x` | `.gitignore:42–44` |
| `.DS_Store`, `Thumbs.db`, `.idea/x`, `.vscode/x`, `x.swp`, `x.tmp` | `.gitignore:115–122` |
| `pytest-of-a/x` | `.gitignore:41` |
| `.venv/x`, `venv/x`, `env/x` | `.gitignore:23–25` |

Every exclusion class is covered by a **narrow, path- or extension-specific** rule.
No rule excludes `data/` or `outputs/` as a whole, no rule excludes `*.jsonl`, and the
only `*.json`-adjacent rules are none at all.

## 5. Consistency checks

| Check | Command | Result |
|---|---|---|
| No tracked file became ignored | `git ls-files -ci --exclude-standard` | empty — **PASS** |
| Negation for the template works | `git check-ignore .env.example` | not ignored — **PASS** |
| No intended dataset file is ignored | batched `git check-ignore --stdin` over 3,641 files | 0 ignored — **PASS** |
| Local copies preserved when eligibility changed | file system | all files remain on disk; nothing was deleted to satisfy Git |

`.gitignore` does not untrack anything, and nothing needed untracking: no decision
dataset file had ever been committed, so removing the blanket rules simply exposed
previously-invisible files to `git status`.

## 6. SQLite rule — inspected, then kept

The previous file had a blanket `*.sqlite`. The audit of its role:

* the corpus database lives at `<project-parent>/data/corpus_db/cm_outbound_corpus.sqlite`,
  **outside** this repository;
* no SQLite file exists anywhere in the tracked tree or in the proposed upload set;
* every approved decision artifact is JSON or JSONL.

The rule therefore blocks a confidential derived database and no legitimate artifact,
so it was kept — and narrowed by name for the corpus database and extended to
journal/WAL/SHM companions. If a small, approved SQLite fixture is ever needed, add an
explicit `!path` negation rather than weakening the rule.

## 7. Residual risk

`.gitignore` is a guard, not an enforcement boundary. It cannot stop a deliberate
`git add -f`. The controls that actually matter are the batched verification above,
the manifest in `github_publishable_manifest.txt`, and the researcher staging files
from that list rather than running a blanket `git add -A`. The recommended staging
command in `github_release_readiness.md` §17 does exactly that.
