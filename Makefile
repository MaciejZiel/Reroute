# Delay model: pull data, retrain, test. Training needs numpy, scipy and lightgbm installed by hand,
# outside the project's dependencies (see the README, "Retraining"); `make ml-train` says so if they
# are missing. The server never needs them.
# REROUTE_SSH="ssh -p 8022 user@host" makes `pull` read the collector's database through SSH.

DATA ?= ml-data

.PHONY: ml-pull ml-train ml-test ml-frozen ml-fixtures

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
