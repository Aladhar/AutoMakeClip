# Experiment A Report: Sustained Candidate Branch

## Metric Comparison

| Metric | Baseline | Experiment A | Change |
| --- | --- | --- | --- |
| Selected local clips | 57 | 57 | +0 |
| Extra local selections | 53 | 53 | +0 |
| Anchor-inside local detections | 4 | 4 | +0 |
| Unique Eklipse targets detected | 2 / 9 | 2 / 9 |  |
| Strict matches | 2 | 2 | +0 |
| Core kill-based targets detected | 0 | 0 | +0 |

## Core Kill-Based Target Results

| Eklipse Event | Window | Detected? |
| --- | --- | --- |
| Triple kill | 1075.0–1097.0 | ❌ No |
| Double kill | 3543.0–3565.0 | ❌ No |
| Multi kill - 3x | 4070.0–4118.0 | ❌ No |
| Multi kill - 3x | 4136.0–4165.0 | ❌ No |

## Top 15 Extras After Experiment A

These top 15 extras are the highest-scoring selected segments whose anchor falls outside all Eklipse windows.

| Rank | Start | End | Score | Candidate Type | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | 3740.40 | 3742.20 | 10.20 |  | Generic kill-heavy peak window. |
| 2 | 3448.78 | 3450.57 | 9.66 |  | Generic kill-heavy peak window. |
| 3 | 3645.90 | 3647.70 | 9.53 |  | Generic kill-heavy peak window. |
| 4 | 3564.90 | 3566.70 | 9.49 |  | Generic kill-heavy peak window. |
| 5 | 3317.65 | 3319.45 | 9.20 |  | Generic kill-heavy peak window. |
| 6 | 2491.65 | 2493.45 | 9.10 |  | Generic kill-heavy peak window. |
| 7 | 1823.28 | 1825.08 | 9.09 |  | Generic kill-heavy peak window. |
| 8 | 3390.90 | 3392.70 | 9.07 |  | Generic kill-heavy peak window. |
| 9 | 2613.78 | 2615.57 | 8.72 |  | Generic kill-heavy peak window. |
| 10 | 3997.65 | 3999.45 | 8.67 |  | Generic kill-heavy peak window. |
| 11 | 3622.15 | 3623.95 | 8.59 |  | Generic kill-heavy peak window. |
| 12 | 2282.78 | 2284.57 | 8.56 |  | Generic kill-heavy peak window. |
| 13 | 1351.53 | 1353.33 | 8.56 |  | Generic kill-heavy peak window. |
| 14 | 2145.20 | 2147.15 | 8.49 |  | Generic kill-heavy peak window. |
| 15 | 3879.40 | 3881.20 | 8.46 |  | Generic kill-heavy peak window. |

## Notes

- Experiment A adds sustained multi-kill/team-fight candidates only; the baseline generic candidate path is preserved.
- No Eklipse timing information is used in the detection logic.