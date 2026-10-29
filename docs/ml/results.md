| predictor | MAE s (bootstrap 95% interval) | RMSE s (bootstrap 95% interval) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 306.7 (297.0 to 316.9) | 460.4 (446.7 to 474.9) | -295.5 | 284.2 | 287.6 | 302.9 | 336.4 |
| persistence (BaselinePredictor) | 93.6 (91.1 to 96.1) | 165.4 (160.5 to 171.0) | -70.6 | 31.7 | 52.6 | 89.0 | 155.6 |
| historical median (line, stop, hour) | 202.9 (196.0 to 210.4) | 348.5 (331.0 to 365.7) | -14.2 | 198.2 | 196.1 | 201.4 | 213.1 |
| calibrated persistence | 83.8 (81.9 to 85.8) | 145.7 (141.3 to 150.6) | -25.7 | 29.3 | 49.3 | 81.6 | 134.4 |
| gradient boosted trees (median) | 74.4 (72.7 to 76.2) | 129.1 (125.0 to 133.4) | -35.0 | 23.9 | 44.4 | 74.0 | 117.3 |
| ridge regression (same features) | 79.7 (78.0 to 81.5) | 133.5 (129.5 to 137.8) | -23.0 | 37.1 | 50.4 | 76.6 | 123.6 |

80% interval on the test fold: model coverage 79.8% (79.4% to 80.1%), mean width 213 s; persistence plus empirical residual quantiles: coverage 76.2% (75.6% to 76.8%), mean width 206 s.

Trees against the ridge model: MAE 6.7% lower (bootstrap interval 6.3% to 7.0%).

Held-out test window:

| scheduled time to the stop | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| under 2 min | 27,865 | 8,834 | 82.9% (82.4% to 83.4%) | 6.8% | 10.3% | 75 |
| 2 to 5 min | 160,160 | 10,107 | 80.6% (80.3% to 80.9%) | 7.7% | 11.7% | 131 |
| 5 to 10 min | 172,896 | 9,810 | 79.5% (79.1% to 79.9%) | 7.5% | 13.1% | 212 |
| 10 min or more | 146,321 | 8,988 | 78.7% (78.1% to 79.2%) | 6.7% | 14.7% | 332 |

| hour of the day (Warsaw time) | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| 11:00 | 274 | 48 | 79.6% (71.1% to 87.2%) | 8.8% | 11.7% | 177 |
| 12:00 | 76,663 | 2,182 | 82.6% (82.0% to 83.2%) | 8.4% | 9.0% | 179 |
| 13:00 | 77,663 | 2,289 | 81.5% (80.9% to 82.2%) | 8.5% | 10.0% | 183 |
| 14:00 | 97,550 | 2,780 | 80.2% (79.5% to 80.8%) | 7.6% | 12.2% | 189 |
| 15:00 | 114,370 | 3,144 | 78.7% (78.1% to 79.4%) | 6.9% | 14.4% | 224 |
| 16:00 | 118,831 | 3,206 | 76.8% (76.1% to 77.5%) | 5.6% | 17.5% | 264 |
| 17:00 | 21,891 | 1,871 | 83.2% (82.1% to 84.3%) | 8.9% | 7.9% | 216 |

Calibration window (it set the interval width), by hour only:

| hour of the day (Warsaw time) | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| 06:00 (few predictions) | 144 | 48 | 84.7% (75.0% to 92.4%) | 6.2% | 9.0% | 136 |
| 07:00 | 99,229 | 2,900 | 76.8% (76.0% to 77.6%) | 6.8% | 16.4% | 175 |
| 08:00 | 116,833 | 3,177 | 79.3% (78.7% to 79.9%) | 9.9% | 10.8% | 215 |
| 09:00 | 93,498 | 2,708 | 81.3% (80.8% to 82.0%) | 10.3% | 8.4% | 199 |
| 10:00 | 77,111 | 2,249 | 82.1% (81.5% to 82.8%) | 9.9% | 7.9% | 177 |
| 11:00 | 64,639 | 2,107 | 81.7% (81.1% to 82.4%) | 9.1% | 9.2% | 173 |
