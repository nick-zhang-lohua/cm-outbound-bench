# Conversation identity and dataset verification (generated)

Generated: 2026-11-25T16:42:51+00:00

## Dataset

| Fact | Value |
|---|---|
| Corpus | `cm_outbound_v2` |
| Manifest verified | True |
| Units read | 4527 |
| Records | 137178 |
| Speech records | 94428 |
| Event records | 42750 |
| Data status | `{"invalid": 3, "valid": 4524}` |
| Channel counts | `{"1": 104790, "2": 31867, "3": 314, "4": 205, "5": 2}` |
| Units with both speakers | 4392 |
| Duplicate groups | 12 (30 units) |
| Timing inversions | 0 |
| Clock resets | 0 |
| Overlapping records | 0 |

## Splits

| Split | Units | Actual % | Target % | Deviation (pp) |
|---|---:|---:|---:|---:|
| train_f | 1207 | 26.6799 | 26.6667 | 0.0132 |
| train_r | 1206 | 26.6578 | 26.6667 | -0.0089 |
| dev | 603 | 13.3289 | 13.3333 | -0.0044 |
| test | 1508 | 33.3333 | 33.3333 | 0.0 |

Verification all true: True; seed `20261125`; Test isolated: True.

## Task coverage

| Fact | Value |
|---|---|
| Tasks | 10116 |
| Tasks by split | `{"dev": 1314, "test": 3381, "train_f": 2726, "train_r": 2695}` |
| Units with tasks | 4253 |
| Units with tasks by split | `{"dev": 562, "test": 1422, "train_f": 1134, "train_r": 1135}` |
| Excluded: unknown channel in prefix | 173 |
| Excluded: insufficient context | 1711 |
| Excluded: invalid unit | 3 |
| Excluded: duplicate visible prefix | 266 |

Baseline matches Milestone 1: **True**
