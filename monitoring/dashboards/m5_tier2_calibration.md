# M5 TIER2 Budget Calibration

- Generated At: `2026-02-11T10:06:55.832771+00:00`
- Lookback Days: `14`
- Dedupe Latest Scenario: `True`
- Overall Status: `PASS`
- Effective Sample Size: `9`
- Raw Sample Size: `12`

## TIER2 Core Metrics

| Metric | Value |
|---|---:|
| avg_tool_calls | 15.0000 |
| tool_calls_p95 | 15.0000 |
| avg_cost_usd | 0.0000 |
| cost_p95_usd | 0.0000 |
| avg_latency_ms | 9.44 |
| latency_p95_ms | 15.60 |
| budget_violation_rate | 0.0000 |

## Threshold Checks

| Metric | Actual | Rule | Pass |
|---|---:|---|---|
| sample_size | 9.0 | `>= 8.0` | YES |
| tool_calls_p95 | 15.0 | `<= 90.0` | YES |
| cost_p95_usd | 0.0 | `<= 2.5` | YES |
| latency_p95_ms | 15.6 | `<= 12000.0` | YES |
| budget_violation_rate | 0.0 | `<= 0.05` | YES |

## Alert Lines

| Dimension | Warn P95 | Critical P95 |
|---|---:|---:|
| tool_calls | 76.5 | 90.0 |
| cost_usd | 2.125 | 2.5 |
| latency_ms | 10200.0 | 12000.0 |

## Violating Reports (Top 20)

- No budget violations detected in selected TIER2 samples.
