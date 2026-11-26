# GitHub Release Readiness

Final preparation report for manual publication of `cm-outbound-bench` to the private
GitHub repository on branch `research/milestone-1.2-clean`.

**Status: prepared and locally verified. Not published.** The researcher performs
`git add`, `git commit` and `git push` manually after reviewing §16 and §17.

| | |
|---|---|
| Repository root | `D:/flowchart-baseline-dist/cm-outbound-bench` |
| Remote | `origin` → `https://github.com/nick-zhang-lohua/cm-outbound-bench.git` |
| Original branch | `main` — intact, unmodified |
| Original HEAD | `1d2f5d4789437ba38b5c36bf95feea832399e20c` |
| Target branch | `research/milestone-1.2-clean` |
| Branch state | Already existed locally at the same commit and was already checked out. **Not created, not reset, not overwritten, no commits added.** |
| Commits created | 0 |
| Files staged | 0 |
| Pushes performed | 0 |
| Remote URL / visibility / collaborators changed | No |
| DeepSeek API calls made | 0 |
| Frozen artifacts regenerated | 0 |

---

## 1. Branch preparation

`git rev-parse --abbrev-ref HEAD` already returned `research/milestone-1.2-clean` before
any file was touched, and `git rev-parse HEAD` returned the baseline commit that `main`
also points to. Both `main` and `research/milestone-1.2-clean` referenced
`1d2f5d4 [origin/main]`.

Consequences, stated explicitly because the instruction says to stop if the target
branch exists:

* **No conflict exists.** The branch is not ahead of or behind `main`; it has no commits
  of its own, so this is not the "existing branch with divergent history" case the task
  guards against.
* The branch therefore did **not** need to be created, and creating one would have been
  wrong. Nothing was reset or overwritten.
* Because the branch is checked out, all prepared changes live on it already as
  uncommitted working-tree state. `main` is untouched and still points at `1d2f5d4`.
* The remote branch does not exist (only `origin/main` is present), so the first push
  will create `origin/research/milestone-1.2-clean`. Remote state was inspected with
  `git branch -a -vv` only — no `git fetch`, no `git ls-remote` mutation, no network
  write.

## 2. Files changed in the working tree

| Category | Files |
|---|---:|
| Modified, tracked | 7 |
| New / untracked, not ignored | 3,744 |
| Staged | 0 |
| Deleted | 0 |

The seven modified tracked files were already modified **before** this pass. Four of
them were edited further by this pass; two were left exactly as found.

| File | Pre-existing change | This pass |
|---|---|---|
| `.gitignore` | yes | **redesigned** — blanket data ignores removed, raw guards kept |
| `README.md` | yes | **extended** — separation, datasets, privacy, status, reproduction |
| `docs/CHANGELOG.md` | yes | **extended** — new publication-preparation entry |
| `docs/data_privacy.md` | yes | **rewritten in part** — inclusion model, §6 governance |
| `.env.example` | yes | untouched (already correct: placeholders only) |
| `pyproject.toml` | yes | untouched |
| `src/cm_outbound_bench/cli.py` | yes | untouched |

New documentation created by this pass:

| File | Purpose |
|---|---|
| `docs/reports/repository_inventory.md` | structure, modules, what was cleaned/preserved |
| `docs/reports/github_security_audit.md` | secret scan, PII inspection, history |
| `docs/reports/gitignore_audit.md` | tracked-vs-ignored evidence |
| `docs/reports/github_dataset_upload_manifest.md` | per-file dataset manifest with hashes |
| `docs/reports/github_publishable_manifest.txt` | flat 5,071-entry staging list (regenerated from the actual tree; the earlier partial version listed only the untracked source/doc paths and is superseded) |
| `docs/reports/github_release_readiness.md` | this report |

No source or test logic was changed. `git diff --stat` for the tracked tree:
7 files, 890 insertions, 29 deletions.

## 3. Files cleaned

| Removed | Count |
|---|---:|
| `__pycache__` directories under `src/cm_outbound_bench/` | 9 |
| `tests/__pycache__` | 1 |
| `data_pipeline/__pycache__` | 1 |
| `.ruff_cache/` | 1 |
| `pytest-of-Nick Zhang/` | 1 |
| audit scratch `outputs/tmp/` | removed after the audit |

Nothing else was deleted. `.venv/`, `.venv-win/` and `providers.toml` were kept (local
environment/configuration, git-ignored). No data file, output, report or source file was
removed.

## 4. Files preserved

* The whole Legacy Synthetic Benchmark: `simulator.py`, `environment.py`, `runner.py`,
  `evaluator.py`, `benchmark/`, `domain/`, `data/tasks/` (14 tasks), `data/policy/`,
  `data/synthetic/`.
* All 1,180 tracked legacy validation artifacts under `outputs/`.
* Every Milestone 1 / 1.1 / 1.2 artifact under `data/decision/` and `outputs/decision/`.
* Both frozen Gold Label files, byte-identical.
* All pre-existing uncommitted user work.

## 5. Dataset directories included

| Directory | Files | Bytes | Classification |
|---|---:|---:|---|
| `data/decision/calls/` | 2 | 43,945,977 | Class 1 |
| `data/decision/tasks/` | 3 | 21,817,408 | Class 1 (tasks) + Class 2 (references) |
| `data/decision/splits/` | 1 | 510,988 | Class 1 |
| `data/decision/samples/` | 6 | 427,465 | Class 1 |
| `data/decision/annotations/` | 7 | 3,483,897 | Class 2 |
| `data/decision/business_sources/` | 1 | 17,724 | Class 1 |
| **`data/decision/` subtotal** | **20** | **70,203,459** | |
| `outputs/decision/annotations/` | 2,367 | 19,379,873 | Class 2 |
| `outputs/decision/evaluation/` | 806 | 3,291,845 | Class 2 |
| `outputs/decision/traces/` | 428 | 1,349,733 | Class 2 |
| `outputs/decision/channel_review/` | 5 | 77,280 | Class 2 |
| `outputs/decision/reports/` | 15 | 227,906 | Class 1 |
| **`outputs/decision/` subtotal** | **3,621** | **24,326,637** | |
| **Total decision datasets** | **3,641** | **94,530,096** | |

Exact per-file and per-set figures: `github_dataset_upload_manifest.md` §1–§4.

## 6. Dataset counts and hashes

| Dataset | Tasks | Status |
|---|---:|---|
| Train-F | 2,726 | built, not annotated (reserved for future Flowchart Generation) |
| Train-R | 2,695 | 400 annotated (100 pilot + 300 feedback) — frozen |
| Dev | 1,314 | 400 annotated — frozen, 400 executed (Group A) |
| Test | 3,381 | **built, not executed, not annotated** |
| **Total catalogue** | **10,116** | unchanged |

Split allocation (call level, seed `20261125`): Train-F 1,207 / Train-R 1,206 / Dev 603
/ Test 1,508 calls = 4,524 valid calls; 4,506 groups (4,494 singleton + 12
near-duplicate groups over 30 calls); maximum deviation 0.013 pp. This matches
`split_manifest.json` and `tasks.manifest.json` exactly.

| Frozen artifact | SHA-256 | Expected | Match |
|---|---|---|---|
| `outputs/decision/annotations/train_r/final_references.jsonl` | `03d2e23bed2e8d771284d3b2a79b88d1f792e5d6fbecff642360f0112c4c4bdd` | same | **YES** |
| `outputs/decision/annotations/dev/final_references.jsonl` | `a7d12662af0745b4800161a1b93bbd0ada988d7a0bc671e3710c482446ba44b6` | same | **YES** |

Ground Truth version `cm-outbound-llm-gold-v1`; source `deepseek_v4_pro`; model
`deepseek-v4-pro`; `policy_verified = false` on all 800 references; Train-R 393 scored
/ 7 unscorable; Dev 391 scored / 9 unscorable; `test_annotated = false`.

## 7. Group A Baseline v1 (unchanged)

| Metric | Value |
|---|---:|
| Condition | A — no flowchart, no workflow, no simulator |
| Ground Truth | `cm-outbound-llm-gold-v1` (`deepseek-v4-pro`) |
| Agent model | `deepseek-flash`, temperature 0.2, max_output_tokens 900 |
| Evaluated / scored / unscorable | 400 / 391 / 9 |
| DSR | 0.83376 (95% CI 0.795918 – 0.868687) |
| DAR | 0.976982 (95% CI 0.961637 – 0.989796) |
| PDR | 0.143223 |
| IDR | 0.023018 (95% CI 0.007692 – 0.038363) |
| Scored coverage | 0.9775 |
| Critical errors | 9 |

Run configuration preserved in
`outputs/decision/traces/20261125T164504Z-group-a-dev-eval/run_set_manifest.json`:
`condition A`, `split dev`, 400 requested / 400 executed / 0 failed, prompt
`baseline-a-v1`, evaluator `0.2.0`, agent `0.1.0`, benchmark `0.2.0`,
`config_hash 0baa3820…`, `split_manifest_sha256 c74e00ed…`. The metrics above were
**read, not recomputed**: no scoring and no API call was run.

## 8. Sensitive files excluded

| Excluded | Enforced by |
|---|---|
| `data/TXT/` raw export | `.gitignore:data/TXT/`, outside the repo, absent from history |
| `data/deidentified/` corpus | `.gitignore:data/deidentified/`, outside the repo |
| `data/corpus_db/`, `*.sqlite*` | ignore rules; zero SQLite files in the tree |
| `data/rejected/` pre-hardening run | ignore rule |
| Raw audio (`*.wav`, `*.mp3`, `*.m4a`, `*.amr`, `*.silk`) | ignore rules |
| `.env`, `.env.*`, `providers.toml`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.enc` | ignore rules, `.env.example` negated |
| `.venv/`, `.venv-win/`, caches, coverage, `outputs/tmp/` | ignore rules |

Verified: 0 of the 3,641 decision-dataset files is ignored; 0 tracked files became
ignored; no raw corpus path has ever been committed.

## 9. Security scan results

| Check | Result |
|---|---|
| Files scanned for secrets | 5,066 text candidates |
| Real credentials in the tree | **none** |
| Credential-shaped matches | 13, all synthetic test fixtures (listed by file and line in `github_security_audit.md` §2) |
| Commits scanned | 1 |
| Key material in Git history | **none** |
| Credential/DB files ever committed | **none** |
| Raw corpus paths ever committed | **none** |
| Content values scanned for CN mobile / ID / landline / card / e-mail / credential-URL / API-key | 421,158 |
| Residual identifiers in those values | **0** |
| Surviving surname + honorific names | **0** |
| Blocking security issue | none in content |

## 10. Test and lint results

| Check | Command | Result |
|---|---|---|
| Import smoke | `python -c "import cm_outbound_bench, cm_outbound_bench.cli, cm_outbound_bench.decisions, cm_outbound_bench.transcripts"` | **ok** |
| CLI help | `python -m cm_outbound_bench.cli --help` | **ok** — `corpus`, `benchmark`, `decision` + legacy `list-tasks`/`run`/`run-all` |
| Lint | `python -m ruff check src tests` | **All checks passed!** |
| Byte-compile | `python -m compileall -q src tests` | exit 0 |
| Unit + integration suite | `python -m pytest -q` | **1,520 passed, 8 failed, 25 subtests passed** in 13.6 s |

The result reproduces the Milestone 1.2 record exactly (1,520 / 8 / 25). Failures were
**not** weakened:

| Failing test | Root cause |
|---|---|
| `test_deidentify.py::KeyTests` (4) + `::PathSafetyTests::test_symlink_into_input_rejected` | POSIX `0600`/`0700` assertions and `os.symlink` need privileges Windows lacks (`WinError 1314`). These are privacy guarantees; relaxing them would reduce assurance. |
| `test_deidentify.py::EndToEndTests::test_run_publishes_valid_dataset`, `::test_path_rejection_happens_before_writes` | `deidentify.py` requires CPython **exactly 3.12.16** and exits `8` on this machine's 3.12.14. The pin keeps de-identification output byte-reproducible. (The earlier milestone record described these two as permission failures; the accurate cause is the interpreter pin.) |
| `test_benchmark_lifecycle.py::test_wall_clock_deadline_is_enforced_between_actions` | `1e-9 s` budget vs Windows `time.monotonic()` resolution; production deadlines are seconds. |

No cross-platform code change was made, because each failure is an intentional
environment guard rather than a defect. The decision is recorded here rather than
hidden by an `xfail`.

## 11. Build result

**Not executed — toolchain unavailable offline.** `uv`, `build` and `hatchling` are all
absent from the available interpreters, and installing them would require a network
download that the task forbids without good reason.

What was verified instead:

* `pyproject.toml` parses and its `force-include` sources (`data/synthetic`,
  `data/tasks`, `data/policy`) all exist;
* `tests/test_packaging.py` passes within the 1,520 green tests, covering the
  "no corpus data in the wheel" guarantee;
* `compileall` succeeds over `src` and `tests`.

Manual step before tagging a release: `uv build` (or `python -m build`) in an
environment that has the build backend.

## 12. Frozen artifact integrity

`scripts/audit_gold_integrity.py --json` — exit code 0.

| Check | Result |
|---|---|
| Overall | `ok: true` |
| Annotation passes re-hashed from frozen Tasks + prompts | **1,600** (400 Dev + 300 feedback + 100 pilot, ×2 passes), 0 mismatches |
| Train-R Gold Labels | 400 tasks, 393 scored, 7 unscorable, hash matches manifest |
| Dev Gold Labels | 400 tasks, 391 scored, 9 unscorable, hash matches manifest |
| Dev references vs Group A traces | **identical sets** (400 = 400) |
| Task catalogue | 10,116 public tasks = 10,116 private references |
| Split verification | ok, 0 cross-check errors |
| Prefix audit (future-information isolation) | 10,116 tasks audited, **0 failures** |
| Agent-visible private-field leaks | **0** across 10,116 tasks |
| Group A traces inside Dev | yes (400 of 1,314), 0 foreign IDs |
| Annotation model on every pass | `deepseek-v4-pro` |
| `policy_verified` | `false` on all 800 frozen references |

No Ground Truth, Group A output, annotation pass or split assignment was modified.

## 13. Git status

```text
## research/milestone-1.2-clean
 M .env.example
 M .gitignore
 M README.md
 M docs/CHANGELOG.md
 M docs/data_privacy.md
 M pyproject.toml
 M src/cm_outbound_bench/cli.py
?? (3,744 new files, including configs/, prompts/, docs/annotation/,
    docs/reports/*, scripts/audit_gold_integrity.py,
    src/cm_outbound_bench/{decisions,transcripts}/, 24 new tests,
    data/decision/, outputs/decision/)
nothing staged
```

| Check | Result |
|---|---|
| `git diff --check` | clean — no whitespace errors |
| `git diff --stat` | 7 files, 890 insertions(+), 29 deletions(−) |
| `git diff --cached --stat` | empty — nothing staged |
| `git status --porcelain=v2 --branch` | branch `research/milestone-1.2-clean`, HEAD `1d2f5d4` |

Observation, not a blocker: Git warns that LF in the working copy of the seven modified
files will be replaced by CRLF on the next checkout, i.e. `core.autocrlf` is enabled
locally. Committed content is normalised to LF regardless. No `.gitattributes` was added,
because introducing one now would renormalise the whole 1,327-file tracked tree and
create a large unrelated diff.

## 14. Large-file and storage audit

| Question | Answer |
|---|---|
| Files > 100 MB (hard GitHub limit) | **none** |
| Files > 50 MB (GitHub warning) | **none** |
| Largest file | `data/decision/calls/calls.jsonl` — **41.91 MB** |
| Second / third | `tasks.jsonl` 13.03 MB, `references.jsonl` 7.78 MB |
| Total proposed upload | 5,071 files / ~113 MB |
| Duplicate datasets | none — `references.jsonl` is the base artifact, `final_references.jsonl` are the frozen annotated derivatives |
| Git LFS required | **No** |
| LFS configured | No |
| Unnecessary intermediates | none identified; legacy validation evidence is retained deliberately |
| Truncation or record removal to fit a limit | **none performed** |

Headroom: the largest file is 42% of the 100 MB limit and 84% of the 50 MB warning
threshold, so no special handling is needed. If the corpus is ever extended to the full
4,527-conversation scale with more turns, re-check `calls.jsonl` before pushing.

## 15. Outstanding data-governance issue — **owner confirmation required**

This is the only item that should block the `git push`.

| Item | Detail |
|---|---|
| What | Processed de-identified conversation data (`data/decision/`, `outputs/decision/`) is included in the repository |
| Why it is sensitive | Conversation-derived text plus model annotations over that text |
| Existing documentation before this pass | Treated these paths as confidential and non-publishable; only aggregates were allowed out |
| New basis | Explicit owner decision: private repository, supervisor-only access |
| Written data-use / institutional authorisation naming private GitHub hosting | **Not found in this workspace** |
| Automated residual-identifier findings | 0 (§9) |
| Residual re-identification risk | Not closable by automation: names without honorifics, addresses without markers, context narrowing; 879 address-marker sequences warrant human sampling |
| Decision required | Owner confirms that private GitHub hosting is inside the permitted research-use boundary |

**Do not fabricate approval.** If the owner cannot confirm, use the fallback in
§17-B: stage everything except `data/decision/` and `outputs/decision/`. No code change
is needed for that fallback, because no shipped test requires those directories to
exist (the suites that touch them skip or use their own fixtures — verified by the
1,520 green tests, which pass with the data present, and by `decision check`, which is
read-only).

## 16. Manual publication instructions

Nothing below has been executed.

### 16-A. Recommended path (owner confirms the data boundary)

```bash
cd cm-outbound-bench

# 0. Confirm the branch and clean state
git rev-parse --abbrev-ref HEAD      # research/milestone-1.2-clean
git rev-parse HEAD                   # 1d2f5d4789437ba38b5c36bf95feea832399e20c
git status --short --branch          # 7 modified, ~3744 untracked, nothing staged

# 1. Re-verify the two frozen hashes AFTER any staging, to catch accidents
sha256sum outputs/decision/annotations/train_r/final_references.jsonl
sha256sum outputs/decision/annotations/dev/final_references.jsonl
# expect 03d2e23b...4bdd  and  a7d12662...44b6

# 2. Stage deliberately, from the manifest -- not with `git add -A`
git add src tests configs prompts scripts docs data outputs .env.example \
        .gitignore README.md pyproject.toml providers.example.toml uv.lock

# 3. Review what is actually staged before committing
git status --short
git diff --cached --stat
git diff --cached --check
git ls-files --others --exclude-standard     # must be EMPTY before committing

# 4. Commit
git commit -m "docs: prepare private research publication of Milestone 1.2

Include the processed Real-Transcript Decision Benchmark datasets, split
artifacts, frozen cm-outbound-llm-gold-v1 Gold Labels and Group A evaluation
records; redesign .gitignore to track approved data while excluding raw corpus
and credentials; add publication review reports."

# 5. Push the new remote branch (first push creates it)
git push -u origin research/milestone-1.2-clean
```

### 16-B. Fallback path (owner cannot confirm the data boundary)

```bash
git add src tests configs prompts scripts docs .env.example .gitignore \
        README.md pyproject.toml providers.example.toml uv.lock
git add data/tasks data/policy data/synthetic
git status --short          # confirm no data/decision or outputs/decision entries
git commit -m "docs: prepare private publication (code + docs only)"
git push -u origin research/milestone-1.2-clean
```

Then revert the two "included" statements in `README.md` §Data Privacy,
`docs/data_privacy.md` §4.1/§6 and `docs/decision_benchmark.md` §11 so the public
description matches the actual contents. The manifests already mark those files as
"requires owner confirmation", so the documentation change is the only edit needed.

### 16-C. Before and after pushing

1. Confirm the GitHub repository is **private** in Settings → General → Danger Zone.
2. Add the supervisor as a collaborator with **read** access only.
3. After the push, verify on GitHub that `data/TXT/` is absent, that
   `providers.toml` is absent, and that `.env` is absent.
4. Do not create a release, do not change visibility, do not mirror the repository.

## 17. Acceptance criteria check

| Criterion | Result |
|---|---|
| Only `cm-outbound-bench` was modified | Yes — no path outside the repository root was touched |
| Target local branch prepared | Yes — already existed and checked out; no commit created |
| Original branch intact | Yes — `main` still at `1d2f5d4` |
| Existing user changes preserved | Yes — all 7 modified + pre-existing untracked files retained |
| Legacy and Real-Transcript architectures intact | Yes — nothing deleted; decision pipeline has no simulator import path |
| Documentation accurate | Yes — the repository-wide "no simulator" claim is corrected to "the Real-Transcript Decision Benchmark does not invoke the Legacy Simulator"; the Git-ignored claims are corrected; the eight offline failures are explained by their real causes |
| Train-F / Train-R / Dev / Test Tasks included | Yes — 2,726 / 2,695 / 1,314 / 3,381 |
| Dataset manifests included | Yes — `github_dataset_upload_manifest.md` |
| Frozen Ground Truth included | Yes — both files, hashes unchanged |
| Group A artifacts included where safe | Yes — 400 traces, 800 scoring records, adjudications, reports |
| Dataset hashes verified | Yes |
| No raw source TXT uploaded | Yes — excluded, never committed |
| No credentials uploaded | Yes — verified in tree and history |
| De-identification inspection done | Yes — 0 findings in 421,158 content values |
| Unapproved data excluded | Yes |
| Historical credential exposure documented | Yes — none exists (1 commit) |
| Group A Baseline unchanged | Yes |
| Ground Truth frozen | Yes |
| Split assignments unchanged | Yes |
| Test unexecuted and unannotated | Yes — built only |
| No Simulator introduced into the Real-Transcript pipeline | Yes |
| Complete upload manifest | Yes — 5,071 entries |
| Complete security audit | Yes |
| Release Readiness Report | Yes — this file |
| No commit created | Yes |
| No push performed | Yes |

**Stop point: local preparation complete. Waiting for the researcher to review and
perform the manual GitHub publication.**
