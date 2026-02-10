# DATA_SOURCES

## Source Registry (MVP)
| domain | source | auth | refresh | fallback |
|---|---|---|---|---|
| market | Tushare Pro (primary) | TUSHARE_TOKEN | 1m-5m | previous snapshot + mark stale |
| fundamentals | Tushare Pro (primary) | TUSHARE_TOKEN | daily | last available |
| sentiment/news | authorized news API (secondary) | API key | 5m-15m | reduce confidence |
| macro/commodity/logistics | Tushare Pro + public index APIs | TUSHARE_TOKEN/API key | 15m-1h | keep latest + stale flag |

## Mapping Rules
- Every ingestion row must contain: `source`, `source_id`, `ingested_at`, `asof`, `checksum`
- All timestamps normalized to UTC
- Missing critical fields go to quarantine table (future migration)
- For Tushare Pro, persist `ts_code`, endpoint name, and request params digest for traceability.

## Data Quality Gates
- Freshness SLA by source category
- Null ratio threshold by field
- Outlier check for numeric factors
- Schema drift detection for upstream payloads
