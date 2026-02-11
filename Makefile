.PHONY: up down run test migrate db-init ci lint-openapi eval-m4 eval-m5 eval-m6 eval-m7 eval-m7-dataset eval-m7-offline eval-m7-shadow eval-m7-drift eval-m7-gate eval-m7-review eval-m7-offline-gate eval-m7-shadow-gate eval-m7-gate-strict seed-m4 replay-m4-lowcov load-m6 drill-m6

PYTHON ?= .venv/bin/python

up:
	docker compose up -d

down:
	docker compose down

run:
	uvicorn app.main:app --reload --port 8000

migrate:
	DATABASE_URL="$$DATABASE_URL" alembic upgrade head

# Kept for bootstrapping comparison with legacy SQL flow.
db-init:
	psql "$$DATABASE_URL" -f sql/001_init.sql

test:
	pytest

ci:
	pytest

lint-openapi:
	$(PYTHON) scripts/lint_openapi.py docs/OPENAPI.yaml

eval-m4:
	$(PYTHON) scripts/m4_quality_baseline.py --lookback-days 14 --auto-topup-samples --enforce-thresholds

eval-m5:
	$(PYTHON) scripts/m5_tier2_calibration.py --lookback-days 14 --auto-topup-samples --enforce-thresholds

eval-m6:
	$(PYTHON) scripts/m6_reliability_panel.py --lookback-days 7 --enforce-thresholds

seed-m4:
	$(PYTHON) scripts/seed_m4_baseline_samples.py --count 12 --tier-pattern "TIER0,TIER1" --vary-asof

replay-m4-lowcov:
	$(PYTHON) scripts/replay_tier1_low_coverage.py --lookback-days 14 --batch-size 20 --max-rounds 3 --run-mode-strategy same --update-baseline-artifacts

load-m6:
	$(PYTHON) scripts/m6_load_soak.py --requests 60 --concurrency 6 --tier TIER1

drill-m6:
	$(PYTHON) scripts/m6_rollout_drill.py --tier TIER1 --enforce-checks

eval-m7-dataset:
	$(PYTHON) scripts/m7_build_dataset.py --lookback-days 30

eval-m7-offline:
	$(PYTHON) scripts/m7_offline_eval.py --lookback-days 30

eval-m7-shadow:
	$(PYTHON) scripts/m7_shadow_compare.py --lookback-days 30

eval-m7-drift:
	$(PYTHON) scripts/m7_drift_monitor.py --baseline-lookback-days 30 --current-lookback-days 7

eval-m7-gate:
	$(PYTHON) scripts/m7_model_gate.py

eval-m7-review:
	$(PYTHON) scripts/m7_monthly_review.py

eval-m7-offline-gate:
	$(PYTHON) scripts/m7_offline_eval.py --lookback-days 30 --enforce-thresholds

eval-m7-shadow-gate:
	$(PYTHON) scripts/m7_shadow_compare.py --lookback-days 30 --enforce-thresholds

eval-m7-gate-strict:
	$(PYTHON) scripts/m7_model_gate.py --enforce-block

eval-m7: eval-m7-dataset eval-m7-offline eval-m7-shadow eval-m7-drift eval-m7-gate eval-m7-review
