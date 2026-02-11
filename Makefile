.PHONY: up down run test migrate db-init ci lint-openapi eval-m4 seed-m4 replay-m4-lowcov

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

seed-m4:
	$(PYTHON) scripts/seed_m4_baseline_samples.py --count 12 --tier-pattern "TIER0,TIER1" --vary-asof

replay-m4-lowcov:
	$(PYTHON) scripts/replay_tier1_low_coverage.py --lookback-days 14 --batch-size 20 --max-rounds 3 --run-mode-strategy same --update-baseline-artifacts
