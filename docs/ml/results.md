| predictor | MAE s (bootstrap 95% interval) | RMSE s (bootstrap 95% interval) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 104.0 (98.6 to 109.8) | 148.1 (138.5 to 158.7) | -80.4 | 107.5 | 102.9 | 103.5 | 105.8 |
| persistence (BaselinePredictor) | 44.4 (43.1 to 45.8) | 67.0 (62.6 to 72.4) | -12.5 | 23.0 | 32.4 | 48.9 | 66.0 |
| historical median (line, stop, hour) | 112.4 (108.7 to 116.3) | 137.0 (130.7 to 144.0) | +57.6 | 105.9 | 110.1 | 112.6 | 118.6 |
| calibrated persistence | 49.5 (48.4 to 50.9) | 71.4 (67.5 to 76.2) | +21.8 | 22.9 | 34.2 | 53.5 | 79.7 |
| gradient boosted trees (median) | 40.6 (39.6 to 41.7) | 58.7 (56.6 to 61.0) | -4.5 | 18.2 | 29.2 | 44.9 | 61.5 |
| ridge regression (same features) | 46.0 (44.8 to 47.3) | 66.1 (62.6 to 70.2) | +0.5 | 31.3 | 33.8 | 48.0 | 69.6 |

80% interval on the test fold: model coverage 79.9% (79.2% to 80.6%), mean width 132 s; persistence plus empirical residual quantiles: coverage 87.8% (87.2% to 88.4%), mean width 198 s.

Trees against the ridge model: MAE 11.7% lower (bootstrap interval 10.6% to 12.9%).

Held-out test window:

| scheduled time to the stop | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| under 2 min | 11,859 | 1,497 | 78.8% (77.8% to 79.8%) | 10.2% | 11.0% | 53 |
| 2 to 5 min | 62,156 | 1,559 | 79.2% (78.5% to 79.8%) | 10.6% | 10.2% | 92 |
| 5 to 10 min | 55,946 | 1,438 | 80.4% (79.5% to 81.3%) | 10.1% | 9.5% | 147 |
| 10 min or more | 34,764 | 1,200 | 80.9% (79.4% to 82.2%) | 10.9% | 8.2% | 204 |

| hour of the day (Warsaw time) | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| 21:00 | 151,443 | 1,459 | 79.9% (79.1% to 80.6%) | 10.2% | 9.8% | 135 |
| 22:00 | 13,282 | 729 | 79.8% (78.2% to 81.3%) | 12.9% | 7.3% | 97 |

Calibration window (it set the interval width), by hour only:

| hour of the day (Warsaw time) | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| 20:00 | 44,019 | 863 | 80.3% (78.9% to 81.5%) | 10.5% | 9.3% | 136 |
| 21:00 | 28,588 | 836 | 79.6% (78.2% to 80.9%) | 8.5% | 11.9% | 109 |

Early-stopping window (it chose the number of trees), by hour only:

| hour of the day (Warsaw time) | predictions | trips | coverage (bootstrap 95% interval) | missed below | missed above | mean width s |
| --- | --- | --- | --- | --- | --- | --- |
| 20:00 | 80,076 | 1,177 | 80.2% (79.1% to 81.2%) | 11.8% | 8.1% | 128 |
