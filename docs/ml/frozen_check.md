Frozen model 20261013-77f128a2 on predictions from 2026-10-13 21:30 CEST to 2026-10-13 21:47 CEST: 31,155 samples, 962 trips.

| | MAE s | RMSE s |
| --- | --- | --- |
| scheduled (zero delay) | 105.4 | |
| persistence | 37.6 | 55.5 |
| model | 34.8 | 51.0 |

Model MAE is 7.4% lower than persistence (bootstrap interval 5.2% to 9.5%); 80% interval coverage 80.3%; the serving rule (at least 5% and interval above zero) is cleared.

| scheduled time to the stop | samples | model MAE | persistence MAE |
| --- | --- | --- | --- |
| under 2 min | 3,054 | 16.3 | 19.7 |
| 2 to 5 min | 14,315 | 27.4 | 29.9 |
| 5 to 10 min | 10,373 | 43.0 | 46.2 |
| 10 min or more | 3,413 | 57.0 | 59.1 |
