# Delay model: pull data, retrain, test. Training needs numpy, scipy and lightgbm installed by hand,
# outside the project's dependencies (see the README, "Retraining"); `make ml-train` says so if they
# are missing. The server never needs them.
# REROUTE_SSH="ssh -p 8022 user@host" makes `pull` read the collector's database through SSH.

DATA ?= ml-data

.PHONY: ml-pull ml-train ml-test ml-frozen ml-fixtures ml-credibility

ml-pull:
	mkdir -p $(DATA)
	cd backend && ml/pull.sh ../$(DATA)/arrival_events.csv ../$(DATA)/observations.csv

# Writes backend/models/delay_model.json (read by the API on start) and docs/ml/*.
ml-train:
	cd backend && python -m ml train --events ../$(DATA)/arrival_events.csv \
		--observations ../$(DATA)/observations.csv

ml-test:
	cd backend && pytest -q -rs tests/test_ml_*.py

# Score the committed model, unchanged, on arrivals after its training window (run make ml-pull first).
ml-frozen:
	cd backend && python -m ml evaluate-frozen --events ../$(DATA)/arrival_events.csv \
		--observations ../$(DATA)/observations.csv --out ../docs/ml/frozen_check.md

# Regenerate the committed golden fixtures of the evaluator tests (needs the training libraries).
ml-fixtures:
	cd backend && python scripts/make_ml_fixtures.py

# Add the ridge comparison and the interval calibration to the stored model's card, without
# retraining (run make ml-pull first). The folds must be the training run's exactly: models
# trained from now on record the pull time in their card and need nothing more; for an older card
# pass KNOWN_UNTIL, the UTC time (ISO 8601) the training data was pulled.
ml-credibility:
	cd backend && python -m ml credibility --events ../$(DATA)/arrival_events.csv \
		--observations ../$(DATA)/observations.csv $(if $(KNOWN_UNTIL),--known-until $(KNOWN_UNTIL))
