# LOCAL_DEV

## Prerequisites
- Python 3.11+
- Docker + Docker Compose

## 1) Environment
```bash
cp .env.example .env
```

## 2) Start dependencies
```bash
docker compose up -d
```

## 3) Install app
```bash
python -m pip install -U pip
python -m pip install -e .
```

## 4) Init DB
```bash
psql "$DATABASE_URL" -f sql/001_init.sql
```

## 5) Run API
```bash
uvicorn app.main:app --reload --port 8000
```

## 6) Smoke test
```bash
curl http://localhost:8000/health
```

## TODO
- 接入 Alembic 迁移流。
- 增加本地一键初始化脚本（seed + smoke test）。
