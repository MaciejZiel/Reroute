| predictor | MAE s (95% CI) | RMSE s (95% CI) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 95.2 (88.4 to 102.6) | 155.2 (131.3 to 182.0) | -62.2 | 89.6 | 92.3 | 96.3 | 101.9 |
| persistence (BaselinePredictor) | 43.5 (42.1 to 44.8) | 68.4 (63.1 to 73.5) | -3.6 | 17.5 | 33.4 | 49.7 | 66.8 |
| historical median (line, stop, hour) | 122.4 (116.8 to 127.9) | 158.9 (140.7 to 181.7) | +70.8 | 112.0 | 118.3 | 124.9 | 132.1 |
| calibrated persistence | 51.4 (50.0 to 52.8) | 75.7 (71.3 to 80.2) | +26.1 | 17.8 | 36.0 | 58.1 | 87.7 |
| gradient boosted trees (median) | 39.8 (38.7 to 41.0) | 59.9 (56.4 to 63.7) | +1.0 | 16.5 | 30.2 | 45.1 | 62.3 |

80% interval on the test fold: model coverage 80.8% (80.0% to 81.6%), mean width 133 s; persistence plus empirical residual quantiles: coverage 85.8% (85.1% to 86.5%), mean width 189 s.
