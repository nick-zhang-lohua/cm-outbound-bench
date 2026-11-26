# Sampling plan SAMPLE-76037752d53b162d

| Field | Value |
|---|---|
| Purpose | `dev_baseline_evaluation_sample` |
| Split | `dev` |
| Seed | 20261126 |
| Target size | 400 |
| Pool tasks | 1314 |
| Pool conversations | 562 |
| Selected tasks | 400 |
| Selected conversations | 400 |
| One task per conversation | True |
| Guideline version | annotation-guidelines-v1 |
| Selection sha256 | `76037752d53b162d02d262fcbd8cac19edeff5c344ad49fbced42f4905f0171d` |

## Strata (selected / pool)

| Stratum | Selected | Pool |
|---|---:|---:|
| `visible=15+|customer=3+|agent=2-3|position=late` | 1 | 1 |
| `visible=15+|customer=3+|agent=2-3|position=mid_late` | 2 | 3 |
| `visible=15+|customer=3+|agent=4+|position=late` | 242 | 340 |
| `visible=15+|customer=3+|agent=4+|position=mid_early` | 1 | 1 |
| `visible=15+|customer=3+|agent=4+|position=mid_late` | 8 | 12 |
| `visible=2-4|customer=3+|agent=2-3|position=late` | 2 | 3 |
| `visible=2-4|customer=3+|agent=2-3|position=mid_late` | 4 | 5 |
| `visible=2-4|customer=3+|agent=4+|position=early` | 1 | 1 |
| `visible=2-4|customer=3+|agent=4+|position=late` | 5 | 7 |
| `visible=2-4|customer=3+|agent=4+|position=mid_early` | 3 | 4 |
| `visible=2-4|customer=3+|agent=4+|position=mid_late` | 11 | 16 |
| `visible=5-8|customer=3+|agent=2-3|position=late` | 1 | 2 |
| `visible=5-8|customer=3+|agent=4+|position=late` | 22 | 31 |
| `visible=5-8|customer=3+|agent=4+|position=mid_early` | 1 | 1 |
| `visible=5-8|customer=3+|agent=4+|position=mid_late` | 13 | 18 |
| `visible=9-14|customer=3+|agent=4+|position=late` | 68 | 96 |
| `visible=9-14|customer=3+|agent=4+|position=mid_late` | 15 | 21 |

## Notes

- One task per conversation unit is selected, so the effective sample size equals the number of independent conversations.
- Selection inside a stratum is ordered by sha256(seed:task_id), so the sample is reproducible from the seed alone.
