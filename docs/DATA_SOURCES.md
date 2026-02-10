# DATA_SOURCES

## Source Registry (MVP)
| domain | source | auth | refresh | fallback |
|---|---|---|---|---|
| market | Tushare/YFinance (choose one first) | API key | 1m-5m | previous snapshot + mark stale |
| fundamentals | Tushare/SEC mirror | API key | daily | last available |
| sentiment/news | authorized news API | API key | 5m-15m | reduce confidence |
| macro/commodity/logistics | public index APIs | API key/public | 15m-1h | keep latest + stale flag |

## Mapping Rules
- Every ingestion row must contain: `source`, `source_id`, `ingested_at`, `asof`, `checksum`
- All timestamps normalized to UTC
- Missing critical fields go to quarantine table (future migration)

## Data Quality Gates
- Freshness SLA by source category
- Null ratio threshold by field
- Outlier check for numeric factors
- Schema drift detection for upstream payloads
