# DB_SCHEMA

## Scope
- Primary DB: PostgreSQL 15+
- Extensions: `pgvector`, `pgcrypto`
- Timezone: UTC storage, local timezone only in display layer

## Core Tables
- `strategies`
- `strategy_versions`
- `tickers`
- `daily_snapshots`
- `reports`
- `decision_logs`
- `watchlist`
- `memory_notes`
- `memory_embeddings`
- `tool_traces`
- `report_feedback`

## Index Baseline
- `daily_snapshots(ticker, asof desc)`
- `reports(ticker, asof desc)`
- `decision_logs(ticker, created_at desc)`
- `watchlist(tier, updated_at desc)`
- `memory_embeddings using ivfflat (embedding vector_cosine_ops)`
- `report_feedback(report_id, created_at desc)`

## Integrity Rules
- `strategy_versions` immutable after publish
- `reports.schema_version` required
- `reports.report_json` must pass schema validation before status = `DONE`
- `decision_logs.report_id` FK to `reports.id`

## Migration Policy
- SQL-first migrations under `sql/`
- One migration file per change set
- Backward compatible changes first, destructive changes by dedicated migration window
