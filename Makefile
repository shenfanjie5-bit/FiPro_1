.PHONY: up down run test migrate ci

up:
	docker compose up -d

down:
	docker compose down

run:
	uvicorn app.main:app --reload --port 8000

migrate:
	psql "$$DATABASE_URL" -f sql/001_init.sql

test:
	pytest

ci:
	pytest
