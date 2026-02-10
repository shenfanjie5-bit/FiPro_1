# IMPLEMENTATION_PLAN

## Sprint 0 (Foundation)
- Initialize backend skeleton (FastAPI)
- Add DB migration runner and apply `sql/001_init.sql`
- Implement health check and auth middleware stub
- Define report JSON schema validation pipeline

## Sprint 1 (Core Loop)
- Implement `/strategies`, `/strategies/{id}/versions`
- Implement snapshot service and `/tickers/{ticker}/snapshot`
- Implement `/reports/generate` + async workflow stub
- Persist reports/decision logs/tool traces

## Sprint 2 (Intelligence)
- Implement tool adapters: market, events, memory search/write
- Implement deterministic `score_signal` + `risk_gate`
- Wire LLM draft -> schema validate -> persist

## Sprint 3 (Productization)
- Implement watchlist tier budgets
- Add graph query endpoints with mocked graph first
- Add replay endpoint/tooling for historical backtest run
- Add observability dashboards (latency, cost, failure)

## Exit Criteria (MVP)
- One ticker end-to-end report generation works
- Report output is 100% schema-valid
- Missing source data results in explicit degraded output, not hallucinated fields
