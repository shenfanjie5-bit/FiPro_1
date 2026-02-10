.PHONY: up down run test migrate db-init ci

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
