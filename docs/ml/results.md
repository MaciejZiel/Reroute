| predictor | MAE s (bootstrap 95% interval) | RMSE s (bootstrap 95% interval) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 102.6 (95.3 to 110.1) | 157.1 (139.6 to 176.2) | -76.7 | 103.0 | 99.3 | 102.2 | 108.8 |
| persistence (BaselinePredictor) | 44.8 (43.2 to 46.6) | 69.9 (63.7 to 76.4) | -14.1 | 19.1 | 31.3 | 48.7 | 70.0 |
| historical median (line, stop, hour) | 119.9 (114.9 to 125.7) | 151.4 (139.7 to 166.2) | +64.3 | 110.7 | 116.3 | 121.6 | 126.5 |
| calibrated persistence | 48.8 (47.3 to 50.5) | 72.1 (67.1 to 77.7) | +18.6 | 19.4 | 32.6 | 52.7 | 79.8 |
| gradient boosted trees (median) | 41.1 (39.6 to 42.7) | 63.3 (57.8 to 69.6) | -7.6 | 16.5 | 28.3 | 44.7 | 65.2 |

80% interval on the test fold: model coverage 78.6% (77.9% to 79.4%), mean width 126 s; persistence plus empirical residual quantiles: coverage 87.3% (86.7% to 87.9%), mean width 191 s.
