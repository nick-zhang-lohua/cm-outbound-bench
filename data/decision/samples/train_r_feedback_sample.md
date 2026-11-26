# Sampling plan SAMPLE-a5fe689664aea85d

| Field | Value |
|---|---|
| Purpose | `train_r_feedback_sample` |
| Split | `train_r` |
| Seed | 20261126 |
| Target size | 300 |
| Pool tasks | 2695 |
| Pool conversations | 1135 |
| Selected tasks | 300 |
| Selected conversations | 300 |
| One task per conversation | True |
| Guideline version | annotation-guidelines-v1 |
| Selection sha256 | `a5fe689664aea85d19740f5e98f312d286f2bd3fa33eb5659b08db5141b87d76` |

## Strata (selected / pool)

| Stratum | Selected | Pool |
|---|---:|---:|
| `visible=15+|customer=3+|agent=2-3|position=late` | 1 | 4 |
| `visible=15+|customer=3+|agent=2-3|position=mid_late` | 2 | 8 |
| `visible=15+|customer=3+|agent=4+|position=late` | 180 | 620 |
| `visible=15+|customer=3+|agent=4+|position=mid_early` | 1 | 3 |
| `visible=15+|customer=3+|agent=4+|position=mid_late` | 7 | 25 |
| `visible=2-4|customer=3+|agent=2-3|position=late` | 1 | 4 |
| `visible=2-4|customer=3+|agent=2-3|position=mid_early` | 2 | 5 |
| `visible=2-4|customer=3+|agent=2-3|position=mid_late` | 4 | 15 |
| `visible=2-4|customer=3+|agent=4+|position=late` | 6 | 20 |
| `visible=2-4|customer=3+|agent=4+|position=mid_early` | 4 | 13 |
| `visible=2-4|customer=3+|agent=4+|position=mid_late` | 9 | 29 |
| `visible=5-8|customer=3+|agent=4+|position=late` | 14 | 48 |
| `visible=5-8|customer=3+|agent=4+|position=mid_early` | 1 | 3 |
| `visible=5-8|customer=3+|agent=4+|position=mid_late` | 7 | 25 |
| `visible=9-14|customer=3+|agent=4+|position=late` | 52 | 180 |
| `visible=9-14|customer=3+|agent=4+|position=mid_late` | 9 | 30 |

## Notes

- One task per conversation unit is selected, so the effective sample size equals the number of independent conversations.
- Selection inside a stratum is ordered by sha256(seed:task_id), so the sample is reproducible from the seed alone.

Excluded 100 conversation unit(s) that belong to another sample, so this sample is independent of it.
