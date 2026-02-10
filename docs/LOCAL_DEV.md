# LOCAL_DEV

## Prerequisites
- Python 3.11+
- PostgreSQL 15+ (with pgvector)
- Redis 7+
- Neo4j (optional in MVP, can be mocked)

## Environment
1. Copy `.env.example` to `.env`
2. Fill required API keys and DB connection strings

## Boot Services (example)
```bash
# postgres / redis / neo4j can be started by docker compose (to be added)
```

## Apply DB Migration
```bash
psql "$DATABASE_URL" -f sql/001_init.sql
```

## Run App (planned FastAPI)
```bash
uvicorn app.main:app --reload --port 8000
```

## Smoke Test
```bash
curl http://localhost:8000/health
```
