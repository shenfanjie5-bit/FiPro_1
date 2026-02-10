# FiPro_1

Production-oriented skeleton for the Hot-Leader Low-Risk Agentic Research Workbench.

## Quick Start

1. Start infra
```bash
docker compose up -d
```

2. Install dependencies
```bash
python -m pip install -U pip
python -m pip install -e .
```

3. Configure env
```bash
cp .env.example .env
```

4. Apply migrations
```bash
alembic upgrade head
```

5. Run API
```bash
uvicorn app.main:app --reload --port 8000
```

6. Smoke test
```bash
curl http://localhost:8000/health
```

## Generate Report (MVP TIER0)

```bash
curl -X POST http://localhost:8000/reports/generate \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "600519.SH",
    "market": "CN_A",
    "asof": "2026-02-10T09:30:00+08:00",
    "strategy_version_id": "stg_v1",
    "tier": "TIER0",
    "run_mode": "LIVE"
  }'
```

## Contract & Schema

- API machine-readable contract: `docs/OPENAPI.yaml`
- Report schema: `app/schemas/report.schema.json`
- Request schema: `app/schemas/request.schema.json`
- DB migration baseline: `app/db/migrations/versions/20260210_0001_m1_contract_baseline.py`

## Current Scope

- TIER0 end-to-end workflow with mock tools
- JSON schema validation + consistency checks
- SQLite checkpoint for replay seed (thread-level)
- M1 contract tests for OpenAPI + SQL baseline

## TODO

- Replace linear workflow with LangGraph StateGraph conditional routing
- Replace mock tool adapters with real data integrations
- Add reviewer path and TIER2 graph flow
