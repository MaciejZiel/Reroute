| predictor | MAE s (bootstrap 95% interval) | RMSE s (bootstrap 95% interval) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 104.1 (98.5 to 109.7) | 148.0 (138.3 to 158.8) | -80.4 | 107.7 | 102.9 | 103.5 | 105.9 |
| persistence (BaselinePredictor) | 44.5 (43.2 to 45.9) | 67.3 (62.6 to 73.5) | -12.5 | 23.2 | 32.5 | 48.9 | 66.1 |
| historical median (line, stop, hour) | 112.4 (108.6 to 116.5) | 136.9 (130.9 to 144.7) | +57.6 | 106.0 | 110.0 | 112.5 | 118.6 |
| calibrated persistence | 49.6 (48.4 to 50.9) | 71.7 (67.4 to 77.4) | +21.7 | 23.2 | 34.2 | 53.6 | 79.7 |
| gradient boosted trees (median) | 40.6 (39.6 to 41.7) | 58.7 (56.4 to 61.2) | -4.5 | 18.4 | 29.3 | 44.9 | 61.6 |

80% interval on the test fold: model coverage 79.9% (79.2% to 80.6%), mean width 132 s; persistence plus empirical residual quantiles: coverage 87.8% (87.2% to 88.3%), mean width 198 s.
