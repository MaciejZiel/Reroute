# Delay model: model card

`/api/predictions`, the stop panel and the vehicle view ask a `Predictor` for the delay at each upcoming stop. Two exist: the baseline (the vehicle's current delay persists) and a gradient-boosted tree model that predicts how much the delay *changes* between the vehicle's latest recorded stop and a later stop of the same trip, together with an 80% interval and the factors behind the number. Example answer for a stop three stops ahead of a vehicle that is 3 min 15 s late:

```json
{"predicted_delay_s": 199, "lower_delay_s": 162, "upper_delay_s": 245,
 "why": {"summary": "Now 3 min 15 s late; expected 3 min 19 s late at this stop. Main factors: vehicles at this stop were 34 s late on average in the last 30 minutes (-16 s); the stop is 3 stops ahead, 3 min of running time (+11 s).",
         "factors": [{"feature": "stop_recent_delay_s", "effect_seconds": -16, "text": "Vehicles at this stop were 34 s late on average in the last 30 minutes"}, "..."]}}
```

The vehicle view shows the same reason as one quiet line under the next stops, naming the stop it explains, with the likely range to the second. Reasons are written in the UI language (`?lang=pl` or `en` on `/api/vehicles/...` and `/api/predictions`; Polish plurals, decimal comma, words for every factor). When the model is paused the same place shows a calm note instead ("Live prediction paused: waiting for a fresh vehicle report"), and `model_status` / `pause_reason` say why in the API (`model`, `held`, `paused`, `out_of_range`, and `passed` when the baseline answered because the vehicle has already reached or passed that stop). Reasons are exact TreeSHAP values of the median model, computed for the first prediction of a request only (the exact computation is the slow part on a small server). Predictions the baseline answers carry no reason.

## What it is

LightGBM quantile regression (median, 10% and 90% quantiles of the delay change; 400 trees each, 15 leaves), exported to a JSON file of flat tree arrays and evaluated by about 200 lines of pure Python (`backend/app/ml/ensemble.py`), so the server needs no extra dependency (no scikit-learn, no LightGBM, no NumPy). The interval is calibrated by split conformal prediction on a separate later window (this time no shift was needed: the raw quantile heads already held 80.6% there). Version `20261106-68efec08`, file `backend/models/delay_model.json` (about 1.7 MB).

The tree count is capped (`--max-rounds 400`): the server walks every tree for each prediction and for its reasons, and 800 trees per head doubled the time of an explained prediction on the server (675 ms against 307 ms) for 0.4 s of MAE.

## Where the model answers, and why it does not flicker

Only where it was trained and evaluated: the vehicle's newest report is at most 120 s old, its last recorded stop is at most 10 minutes old, and the stop asked about is at most 12 stops ahead (and within the scheduled running time seen in training). The two age limits were set from the recorded feed: vehicles report a new position every 22 s at the median, and at a random moment the newest report is at most 120 s old 97.5% of the time. Training simulates requests at random moments, so the report age the model is evaluated on is the one a live request sees, and the report's age is a feature. The last model answer for a stop is held for 60 s when a report is late; after that, without a live vehicle, or beyond the trained stops, the baseline answers with no interval and no reason. This is enforced in `app/ml/live.py` and `ModelPredictor` and covered by tests.

## Data

Eleven service dates, Monday 2026-10-26 03:16 to Friday 2026-11-06 11:58 Warsaw time (273 hours) of the live Warsaw feed: 7,142,909 recorded stop arrivals on 280,482 trips, 343 lines and 6,835 stops. For the first time this includes a weekend: Saturday 31 October and Sunday 1 November, All Saints' Day, when traffic around the cemeteries is unlike any other day of the year.

Gaps, all in the source feed rather than the collector: no vehicles on Wednesday 28 October from 04:00 to 15:00; none from Tuesday 3 November 22:00 to Thursday 5 November 08:00 (the feed answered every poll, but nothing was recorded; the collector now logs such stretches, see the README); Tuesday 27 October has far fewer arrivals from 14:00 to 16:00. Weather was not used. Arrival times are themselves interpolated between vehicle reports 20 to 30 s apart.

## Samples and split

Predictions are simulated at moments spread over each trip's life, one per eight minutes on average (`--moment-every 480`; at 240 the run did not fit in 7 GB on the maintainer's laptop, and neighbouring minutes of a trip are nearly the same sample). The origin is the newest arrival known at that moment and the target a stop 1, 2, 3, 4, 6, 8 or 12 stops further that has not been reached yet: 6,638,585 samples. The split is by time only, and a sample belongs to a window only if the prediction moment and the arrival it predicts both lie inside it (4,897 straddling samples dropped). The boundaries were set by hand before training (`--fractions 0.69207 0.77641 0.91576`) at whole service periods:

| window | Warsaw time | samples | used for |
| --- | --- | --- | --- |
| train | Mon 26 Oct 03:16 to Tue 3 Nov 00:00 | 5,013,843 | fitting the trees (includes the weekend and All Saints' Day) |
| early stop | Tue 3 Nov | 707,612 | choosing when to stop adding trees |
| calibration | Tue 3 Nov 23:00 to Thu 5 Nov 13:00 (only Thursday morning has data) | 170,772 | interval width and the choice of the best baseline |
| test | Thu 5 Nov 13:00 to Fri 6 Nov 11:58 | 741,461 | every number below |

Features use only arrivals known at the prediction moment (unit-tested by changing everything that happens later), and the live code computes the inputs with the same functions as training (tested for equality against the database at arbitrary moments).

## Features

Stops ahead and scheduled running time to the target, and how long ago the last stop was recorded; the vehicle's own latest reported delay, how old that report is, and the delay at its last recorded stop and how it changed; the delay trend over 10 min; the mean delay of other vehicles on the line (15 min) and at the target stop (30 min); the delay of the vehicle ahead on the same line at the target stop and its scheduled headway; tram or bus. Hour, weekday and stop number are implemented but switched off (`--calendar-features`): with one weekend they would mostly memorise single days.

## Results on the held-out window

MAE and RMSE in seconds; intervals come from a bootstrap that resamples clusters of one line and one 15-minute block; the "MAE under 2 min" columns bin by the scheduled time from the last stop to the target.

| predictor | MAE s (95% interval) | RMSE s | bias s | under 2 min | 2 to 5 min | 5 to 10 min | 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 230.9 (226.0 to 236.3) | 396.6 | -212.6 | 212.3 | 214.2 | 229.3 | 255.9 |
| persistence (what the server falls back to) | 69.6 (68.5 to 70.6) | 124.3 | -39.4 | 25.4 | 42.2 | 68.3 | 111.6 |
| historical median (line, stop, hour) | 151.3 (148.3 to 154.4) | 266.7 | -57.4 | 138.5 | 141.5 | 150.7 | 166.0 |
| calibrated persistence (best baseline) | 67.7 (66.9 to 68.5) | 114.9 | -2.9 | 24.4 | 41.6 | 67.4 | 106.8 |
| **gradient-boosted trees (median)** | **59.6 (58.9 to 60.3)** | **102.6** | -14.4 | **20.5** | **36.9** | **60.1** | **93.2** |
| ridge regression (same features) | 66.4 (65.7 to 67.3) | 108.3 | -2.0 | 32.7 | 44.3 | 65.3 | 100.1 |

- The model's MAE is **11.9% lower** than the best baseline, chosen on the calibration window (bootstrap interval 11.6% to 12.3%), and 10.3% lower than ridge on the same features. The rule for serving the model by default (at least 5% lower MAE than the best baseline, interval above zero) is in the code; it passed (`promoted: true`).
- The 80% interval held **79.6%** of the time on the test window (79.5% to 79.8%), at every distance to the stop (79.6% to 79.8%), with a mean width of 184 s. It is still too narrow in the rush hours: 76.6% at 07:00 and 77.5% to 77.7% at 15:00 and 16:00, where vehicles end up later than the range about 15% of the time. At night it is noisier still: 70.2% at 01:00, but from only 292 predictions on 61 trips (bootstrap interval 62% to 78%).
- The model still under-predicts delays (bias -14 s): vehicles keep getting later along a trip more than it expects.

The full tables, per horizon and per hour, are in [ml/results.md](ml/results.md), and the charts in [ml/](ml/).

## Against the previous model

The previous model (`20261029-8e1d57de`, three and a half weekdays of data) and this one were run, unchanged, through the server's evaluator on the same 60,000 predictions from the test window (one per eight minutes of each trip, 30,000 per day):

| | previous model | this model |
| --- | --- | --- |
| MAE | 57.9 s | 57.8 s |
| 80% interval held | 81.4% (too wide) | 80.1% |
| mean interval width | 213 s (its own test window) | 184 s |
| bias (Thursday / Friday) | -17.1 / -8.5 s | -17.7 / -9.3 s |

Three times the data did **not** make the median prediction better. What it did improve is the interval: narrower, so the "likely range" in the app says more, while holding as often as it should. And this model has seen a weekend and a public holiday. That is why it replaced the previous one; the persistent under-prediction needs a different input, not more of the same days.

## Does it give better advice? (passenger check)

Lower error is one question; the one a rider asks is "when do I have to be at the stop?". `python -m ml evaluate-decisions` simulates a passenger who is at the stop at the moment a strategy names and counts how often the vehicle has already gone and how long they wait otherwise. Each strategy gets a margin per horizon, fitted on the first half of the data, so that it misses the same share of vehicles; the second half is scored. This model, on 7 November (after its training window, 30,000 predictions, [ml/decisions.md](ml/decisions.md)):

| target | strategy | missed | mean wait |
| --- | --- | --- | --- |
| miss 1 in 10 | model (lower bound) | 9.2% | 86 s |
| | persistence + its own error quantile | 10.2% | 94 s |
| | timetable only | 13.3% | 224 s |
| miss 1 in 20 | model (lower bound) | 4.2% | 98 s |
| | persistence + its own error quantile | 4.9% | 109 s |
| | timetable only | 6.4% | 234 s |

With fewer missed vehicles, the model's passenger also waits 8.0% less than persistence's (95% interval 7.6% to 8.5%) at the 10% target and 10.6% less (10.2% to 11.0%) at the 5% target. The gain is real but modest: about 10 seconds per trip. Most of the value of live data comes from the vehicle's current delay itself (waiting about 60% less than with the timetable alone). One day of scoring; the truth is interpolated between vehicle reports.

## How the previous model held up on days it never saw

Before this model was trained, the previous one (`20261029-8e1d57de`, trained on 26 to 29 October) was scored unchanged on the eight service days that came after its training window, with `make ml-frozen`: 200,000 predictions, at most 25,000 per day ([ml/frozen_check.md](ml/frozen_check.md)).

| day | model MAE | persistence MAE | gain (95% interval) | 80% range held |
| --- | --- | --- | --- | --- |
| Thu 29 Oct (evening) | 60.1 | 65.8 | 8.6% (7.2% to 9.9%) | 82.0% |
| Fri 30 Oct | 55.7 | 64.3 | 13.3% (12.6% to 14.2%) | 81.1% |
| Sat 31 Oct | 50.2 | 56.4 | 10.9% (10.1% to 11.7%) | 82.5% |
| Sun 1 Nov, All Saints' Day | 47.6 | 53.1 | 10.4% (9.5% to 11.2%) | 81.9% |
| Mon 2 Nov | 51.1 | 58.0 | 12.0% (11.2% to 12.8%) | 81.7% |
| Tue 3 Nov | 62.0 | 72.4 | 14.3% (13.5% to 15.1%) | 81.1% |
| Thu 5 Nov | 63.2 | 74.8 | 15.4% (14.6% to 16.2%) | 81.8% |
| Fri 6 Nov (morning) | 49.5 | 55.6 | 11.0% (10.1% to 11.8%) | 81.3% |
| **all eight days** | **54.9** | **62.5** | **12.2% (11.8% to 12.5%)** | **81.7%** |

It beat persistence on every day, a weekend and a public holiday it had never seen included, by a margin close to the one measured at training time. Its range was a little too wide everywhere (81% to 83% instead of 80%), which is what the new model fixed.

## What the model knows and does not know

Knows, with figures from the held-out window:
- How a delay tends to evolve over the next 1 to 12 stops of a trip, given the vehicle's latest delay, how other vehicles on the line and at the stop are doing, and the vehicle ahead. It beats the best baseline by 11.9% of MAE, most for the nearest stops.
- How uncertain it is: the 80% interval held 79.6% overall and at every distance, but about 77% in the rush hours.

Does not know:
- Anything outside late October and early November: no winter, no school holidays, one weekend only.
- Weather, events, road works and traffic: none is an input.
- True arrival times: labels are interpolated between vehicle reports.
- More than 12 stops ahead, or a vehicle whose reports are older than 120 s: the baseline answers there.
- Why a delay happens. The reason line lists what moved the number, not causes.

## Explainability

Global importance (mean absolute SHAP value on the test window) and partial dependence of the four strongest features, produced by the training command:

<p align="center">
  <img src="ml/importance.svg" width="720" alt="Global feature importance">
</p>

<p align="center">
  <img src="ml/partial_dependence.svg" width="720" alt="Partial dependence of the four strongest features">
</p>

Read the partial dependence with care: the strongest features are correlated (stops ahead and scheduled running time say nearly the same), so their individual curves share one effect.

## Retraining

Everything below runs on the maintainer's machine; the server only reads the resulting file. Training needs numpy, scipy and lightgbm, installed by hand outside the project (`pip install numpy scipy lightgbm`; these wheels bundle LGPL/GPL runtime libraries, which is why they are deliberately not project dependencies, and `make ml-train` says so if they are missing).

```bash
REROUTE_SSH="ssh -p 8022 user@server" OBSERVATION_DAYS=30 make ml-pull   # CSV travels gzipped
make ml-frozen                          # score the committed model, unchanged, on the new days first
make ml-train                           # writes backend/models/delay_model.json and docs/ml/*
make ml-test                            # all ML tests; the ones that need the libraries skip without them
make ml-credibility                     # rebuild the ridge comparison and calibration on the exact folds
make ml-fixtures                        # regenerate the golden test fixtures (needs the libraries)
```

Memory: the loader keeps each trip's vehicle reports as NumPy arrays and shares repeated ids, so reading eleven days takes about 2 GB; the training run above peaked at 6.2 GB with `--moment-every 480` and took 19 minutes. `make ml-frozen` scores at most 25,000 predictions per day, so a week of new data takes minutes, not hours.

The server-side tests, including the proof that the pure-Python evaluator reproduces LightGBM's predictions and SHAP values, run in CI without LightGBM against golden values in `backend/tests/fixtures/ml_golden.json`. The model file is picked up by path when the API starts: `DELAY_MODEL_FILE` (default `backend/models/delay_model.json`). With nothing set the API serves the model only if its card says it was promoted; `PREDICTOR=model` forces it, `PREDICTOR=package.module:Class` selects another predictor. The version stamp is returned in every response as `model.version`.

## What would make it better

The persistent under-prediction says the inputs miss something that makes a trip get later as it goes: road traffic, dwell times at busy stops, or the delay a vehicle picks up on a given segment at a given hour. With several weeks of data a target encoding of how much each stop segment typically adds (per hour and day type) is the obvious next feature, with a per-hour calibration of the interval for the rush hours and a validation scheme with several consecutive test windows instead of one.
