# Delay model: pull data, retrain, test. Needs the `ml` extra: pip install -e 'backend[dev,ml]'.
# REROUTE_SSH="ssh -p 8022 user@host" makes `pull` read the collector's database through SSH.

DATA ?= ml-data

.PHONY: ml-pull ml-train ml-test ml-frozen

ml-pull:
	mkdir -p $(DATA)
	cd backend && ml/pull.sh ../$(DATA)/arrival_events.csv ../$(DATA)/observations.csv

# Writes backend/models/delay_model.json (read by the API on start) and docs/ml/*.
ml-train:
	cd backend && python -m ml train --events ../$(DATA)/arrival_events.csv \
		--observations ../$(DATA)/observations.csv

ml-test:
	cd backend && pytest -q tests/test_ml_ensemble.py tests/test_ml_features.py \
		tests/test_ml_pipeline.py tests/test_ml_live.py

# Score the committed model, unchanged, on arrivals after its training window (run make ml-pull first).
ml-frozen:
	cd backend && python -m ml evaluate-frozen --events ../$(DATA)/arrival_events.csv \
		--observations ../$(DATA)/observations.csv --out ../docs/ml/frozen_check.md
