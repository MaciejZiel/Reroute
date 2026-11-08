# Will the passenger make it? (decision check)

Command: `python -m ml evaluate-decisions --events … --observations … --moment-every 240 --per-day 30000` on data pulled 2026-11-07, after the model's training window.

Model 20261106-68efec08, data after its training window: margins fitted on 2026-11-06 11:58 to 2026-11-07 00:20 Warsaw time (30,000 predictions), scored on 2026-11-07 00:20 to 2026-11-07 20:51 (30,000 predictions, 12,700 trips).

A passenger is at the stop at the moment the strategy names. Missed: the vehicle had already gone. Wait: seconds at the stop when caught.

Target: miss 10% of vehicles

| strategy | missed (95% interval) | mean wait s | median wait s |
| --- | --- | --- | --- |
| model | 9.2% (8.9% to 9.6%) | 86 | 61 |
| persistence | 10.2% (9.8% to 10.5%) | 94 | 69 |
| timetable | 13.3% (12.8% to 13.8%) | 224 | 143 |

Wait cut by the model vs persistence: 8.0% (95% interval 7.6% to 8.5%).

Wait cut by the model vs timetable: 61.4% (95% interval 60.4% to 62.3%).

| horizon | model missed / wait | persistence missed / wait |
| --- | --- | --- |
| under 2 min | 9.8% / 28 s | 11.3% / 32 s |
| 2 to 5 min | 9.9% / 54 s | 10.2% / 60 s |
| 5 to 10 min | 9.0% / 92 s | 10.1% / 99 s |
| 10 min or more | 8.5% / 136 s | 10.0% / 146 s |

Target: miss 5% of vehicles

| strategy | missed (95% interval) | mean wait s | median wait s |
| --- | --- | --- | --- |
| model | 4.2% (4.0% to 4.5%) | 98 | 73 |
| persistence | 4.9% (4.7% to 5.2%) | 109 | 84 |
| timetable | 6.4% (6.1% to 6.7%) | 234 | 157 |

Wait cut by the model vs persistence: 10.6% (95% interval 10.2% to 11.0%).

Wait cut by the model vs timetable: 58.3% (95% interval 57.3% to 59.2%).

| horizon | model missed / wait | persistence missed / wait |
| --- | --- | --- |
| under 2 min | 3.9% / 30 s | 6.3% / 35 s |
| 2 to 5 min | 4.8% / 59 s | 5.4% / 70 s |
| 5 to 10 min | 4.1% / 105 s | 4.5% / 116 s |
| 10 min or more | 3.6% / 156 s | 4.5% / 171 s |
