# Phase I: human-judgment evaluation

6 real student profiles x 11 real published events = 66 pairs, judged 0/1/2 by 2 rater(s). Relevant means consensus rating >= 1. This is a SMALL, SUBJECTIVE sample: intervals are wide and differences are indications, not proof. It is not an evaluation on real student behaviour.

Rater agreement (n=66 pairs): quadratic-weighted kappa 0.875, exact agreement 0.879, relevant-vs-not agreement 0.970.

## Mean over students (95% bootstrap interval)

| Method | Precision@3 | HitRate@3 | nDCG@5 | MAP@5 | CategoryDiversity@3 | Coverage@3 |
|---|---|---|---|---|---|---|
| Random | 0.770 [0.692, 0.851] | 0.990 [0.979, 0.998] | 0.608 [0.585, 0.631] | 0.680 [0.577, 0.787] | 0.849 [0.844, 0.856] | 0.848 |
| Most popular | 0.758 [0.628, 0.890] | 1.000 [1.000, 1.000] | 0.568 [0.476, 0.682] | 0.631 [0.470, 0.789] | 0.892 [0.885, 0.900] | 0.598 |
| Profile match | 0.738 [0.556, 0.902] | 1.000 [1.000, 1.000] | 0.600 [0.530, 0.675] | 0.638 [0.458, 0.822] | 0.816 [0.705, 0.928] | 0.548 |
| Keyword only | 0.848 [0.722, 0.959] | 1.000 [1.000, 1.000] | 0.716 [0.646, 0.789] | 0.800 [0.654, 0.938] | 0.914 [0.803, 1.000] | 0.679 |
| Semantic only | 0.833 [0.611, 1.000] | 1.000 [1.000, 1.000] | 0.638 [0.495, 0.777] | 0.782 [0.563, 0.960] | 0.722 [0.667, 0.833] | 0.636 |
| Full hybrid | 0.778 [0.667, 0.889] | 1.000 [1.000, 1.000] | 0.537 [0.444, 0.630] | 0.616 [0.433, 0.814] | 0.833 [0.722, 0.944] | 0.545 |

## Full hybrid minus each baseline (paired, 95% interval)

| Baseline | Metric | Difference | Interval | Verdict |
|---|---|---|---|---|
| Random | Precision@3 | 0.008 | [-0.117, 0.138] | no clear difference |
| Random | HitRate@3 | 0.010 | [0.003, 0.021] | hybrid clearly better |
| Random | nDCG@5 | -0.071 | [-0.179, 0.037] | no clear difference |
| Random | MAP@5 | -0.065 | [-0.237, 0.135] | no clear difference |
| Random | CategoryDiversity@3 | -0.016 | [-0.126, 0.096] | no clear difference |
| Most popular | Precision@3 | 0.020 | [-0.094, 0.100] | no clear difference |
| Most popular | HitRate@3 | 0.000 | [0.000, 0.000] | no clear difference |
| Most popular | nDCG@5 | -0.031 | [-0.122, 0.060] | no clear difference |
| Most popular | MAP@5 | -0.016 | [-0.098, 0.096] | no clear difference |
| Most popular | CategoryDiversity@3 | -0.059 | [-0.177, 0.057] | no clear difference |
| Profile match | Precision@3 | 0.039 | [-0.086, 0.168] | no clear difference |
| Profile match | HitRate@3 | 0.000 | [0.000, 0.000] | no clear difference |
| Profile match | nDCG@5 | -0.063 | [-0.164, 0.040] | no clear difference |
| Profile match | MAP@5 | -0.022 | [-0.127, 0.093] | no clear difference |
| Profile match | CategoryDiversity@3 | 0.017 | [-0.192, 0.226] | no clear difference |
| Keyword only | Precision@3 | -0.071 | [-0.182, 0.000] | no clear difference |
| Keyword only | HitRate@3 | 0.000 | [0.000, 0.000] | no clear difference |
| Keyword only | nDCG@5 | -0.179 | [-0.219, -0.140] | hybrid clearly worse |
| Keyword only | MAP@5 | -0.184 | [-0.271, -0.090] | hybrid clearly worse |
| Keyword only | CategoryDiversity@3 | -0.081 | [-0.222, 0.060] | no clear difference |
| Semantic only | Precision@3 | -0.056 | [-0.222, 0.111] | no clear difference |
| Semantic only | HitRate@3 | 0.000 | [0.000, 0.000] | no clear difference |
| Semantic only | nDCG@5 | -0.101 | [-0.204, -0.020] | hybrid clearly worse |
| Semantic only | MAP@5 | -0.166 | [-0.304, -0.028] | hybrid clearly worse |
| Semantic only | CategoryDiversity@3 | 0.111 | [-0.111, 0.278] | no clear difference |

Many comparisons are made on very few students and nothing is corrected for that, so a single 'clearly' verdict can be chance. Treat Precision and nDCG as the main results and read the rest as supporting detail.


Ties in a method's scores are broken at random and averaged over many draws. Students with no relevant event are left out of HitRate, nDCG and MAP (the table shows how many students each value uses in results_summary.csv). Coverage is the share of events shown to at least one student.
