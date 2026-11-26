# Sampling plan SAMPLE-43a7c204de7dbc6d

| Field | Value |
|---|---|
| Purpose | `train_r_annotation_pilot` |
| Split | `train_r` |
| Seed | 20261126 |
| Target size | 100 |
| Pool tasks | 2695 |
| Pool conversations | 1135 |
| Selected tasks | 100 |
| Selected conversations | 100 |
| One task per conversation | True |
| Guideline version | annotation-guidelines-v1 |
| Selection sha256 | `43a7c204de7dbc6d83b59646eb82d9b8aca23f7b452037725c11acaec253989f` |

## Strata (selected / pool)

| Stratum | Selected | Pool |
|---|---:|---:|
| `visible=15+|customer=3+|agent=2-3|position=late` | 1 | 5 |
| `visible=15+|customer=3+|agent=2-3|position=mid_late` | 1 | 9 |
| `visible=15+|customer=3+|agent=4+|position=late` | 60 | 680 |
| `visible=15+|customer=3+|agent=4+|position=mid_late` | 3 | 28 |
| `visible=2-4|customer=3+|agent=2-3|position=mid_late` | 2 | 17 |
| `visible=2-4|customer=3+|agent=4+|position=late` | 2 | 22 |
| `visible=2-4|customer=3+|agent=4+|position=mid_early` | 1 | 14 |
| `visible=2-4|customer=3+|agent=4+|position=mid_late` | 3 | 32 |
| `visible=5-8|customer=3+|agent=4+|position=late` | 5 | 53 |
| `visible=5-8|customer=3+|agent=4+|position=mid_late` | 2 | 27 |
| `visible=9-14|customer=3+|agent=4+|position=late` | 17 | 197 |
| `visible=9-14|customer=3+|agent=4+|position=mid_late` | 3 | 33 |

## Notes

- One task per conversation unit is selected, so the effective sample size equals the number of independent conversations.
- Selection inside a stratum is ordered by sha256(seed:task_id), so the sample is reproducible from the seed alone.
