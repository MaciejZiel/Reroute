# Frozen check of model 20261029-8e1d57de

The model trained on 26 to 29 October 2026, run unchanged through the server's evaluator on the eight service days that followed (Thursday evening 29 October to Friday 6 November, Saturday 31 October and Sunday 1 November, All Saints' Day, included; 4 November is missing from the source feed). One simulated request per four minutes of each trip, at most 25,000 scored per day, drawn at random. Command: `make ml-frozen` (`python -m ml evaluate-frozen --moment-every 240 --per-day 25000`).

Frozen model 20261029-8e1d57de on predictions from 2026-10-29 17:20 CET to 2026-11-06 11:57 CET: 200,000 samples, 106,920 trips.

| | MAE s | RMSE s |
| --- | --- | --- |
| scheduled (zero delay) | 200.5 | |
| persistence | 62.5 | 110.8 |
| model | 54.9 | 94.1 |

Model MAE is 12.2% lower than persistence (bootstrap interval 11.8% to 12.5%); 80% interval coverage 81.7%; the serving rule (at least 5% and interval above zero) is cleared.

| scheduled time to the stop | samples | model MAE | persistence MAE |
| --- | --- | --- | --- |
| under 2 min | 11,703 | 19.7 | 24.8 |
| 2 to 5 min | 64,315 | 35.1 | 40.0 |
| 5 to 10 min | 68,124 | 55.9 | 62.5 |
| 10 min or more | 55,858 | 83.9 | 96.3 |

| Warsaw day (trips starting on it) | samples | model MAE | persistence MAE | gain (95% interval) | 80% range held | model bias |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-29 Thu | 25,000 | 60.1 | 65.8 | 8.6% (7.2% to 9.9%) | 82.0% | -0.0 |
| 2026-10-30 Fri | 25,000 | 55.7 | 64.3 | 13.3% (12.6% to 14.2%) | 81.1% | -10.2 |
| 2026-10-31 Sat | 25,000 | 50.2 | 56.4 | 10.9% (10.1% to 11.7%) | 82.5% | -6.4 |
| 2026-11-01 Sun | 25,000 | 47.6 | 53.1 | 10.4% (9.5% to 11.2%) | 81.9% | -4.3 |
| 2026-11-02 Mon | 25,000 | 51.1 | 58.0 | 12.0% (11.2% to 12.8%) | 81.7% | -7.3 |
| 2026-11-03 Tue | 25,000 | 62.0 | 72.4 | 14.3% (13.5% to 15.1%) | 81.1% | -15.3 |
| 2026-11-05 Thu | 25,000 | 63.2 | 74.8 | 15.4% (14.6% to 16.2%) | 81.8% | -14.9 |
| 2026-11-06 Fri | 25,000 | 49.5 | 55.6 | 11.0% (10.1% to 11.8%) | 81.3% | -8.1 |
