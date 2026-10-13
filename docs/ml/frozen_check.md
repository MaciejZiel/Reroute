Frozen model 20261013-f2a7a97f on predictions from 2026-10-13 22:08 CEST to 2026-10-13 22:15 CEST: 7,458 samples, 651 trips.

| | MAE s | RMSE s |
| --- | --- | --- |
| scheduled (zero delay) | 93.9 | |
| persistence | 29.7 | 41.1 |
| model | 28.0 | 38.5 |

Model MAE is 5.8% lower than persistence (bootstrap interval 3.1% to 8.6%); 80% interval coverage 80.8%; the serving rule (at least 5% and interval above zero) is cleared.

| scheduled time to the stop | samples | model MAE | persistence MAE |
| --- | --- | --- | --- |
| under 2 min | 910 | 14.8 | 18.3 |
| 2 to 5 min | 4,693 | 25.9 | 27.8 |
| 5 to 10 min | 1,854 | 39.8 | 40.1 |
| 10 min or more | 1 | 48.0 | 66.0 |
