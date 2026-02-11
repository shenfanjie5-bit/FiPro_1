# LOCAL_DEV

## Prerequisites
- Python 3.11+
- Docker + Docker Compose

## 1) Environment
```bash
cp .env.example .env
```
- `DATABASE_URL` 建议使用 `postgresql+psycopg://...`（运行时代码兼容 `postgresql://...` 自动转换）。

## 2) Start dependencies
```bash
docker compose up -d
```

## 3) Install app
```bash
python -m pip install -U pip
python -m pip install -e .
```

## 4) Run DB migrations (Alembic)
```bash
alembic upgrade head
```

## 5) Run API
```bash
uvicorn app.main:app --reload --port 8000
```

## 6) Smoke test
```bash
curl http://localhost:8000/health
```

## 7) Generate report (MVP)
```bash
curl -X POST http://localhost:8000/reports/generate \
  -H "Content-Type: application/json" \
  -d '{
    "ticker":"600519.SH",
    "market":"CN_A",
    "asof":"2026-02-10T09:30:00+08:00",
    "strategy_version_id":"stg_v1",
    "tier":"TIER0",
    "run_mode":"LIVE"
  }'
```

## 8) M6 stability checks
```bash
make eval-m6
make load-m6
make drill-m6
```
