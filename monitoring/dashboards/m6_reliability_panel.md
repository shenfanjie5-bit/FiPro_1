# M6 Reliability Panel

- Generated At: `2026-02-11T14:35:41.430476+00:00`
- Lookback Days: `7`
- Dedupe Latest Scenario: `True`
- Overall Status: `PASS`
- Raw Sample Size: `333`
- Effective Sample Size: `54`

## Core Metrics

- success_rate: `1.0`
- failure_rate: `0.0`
- schema_pass_rate: `1.0`
- latency_p95_ms: `160.0`
- cost_p95_usd: `0.0`
- degraded_report_rate: `1.0`
- retry_report_rate: `0.055556`

## Threshold Checks

- [PASS] `sample_size`: 54.0 >= 10.0
- [PASS] `success_rate`: 1.0 >= 0.97
- [PASS] `failure_rate`: 0.0 <= 0.03
- [PASS] `schema_pass_rate`: 1.0 >= 0.99
- [PASS] `latency_p95_ms`: 160.0 <= 12000.0
- [PASS] `cost_p95_usd`: 0.0 <= 2.5

## Daily Trend

- 2026-02-11: sample=54, success=1.0, failure=0.0, schema=1.0, latency_p95=160.0, cost_p95=0.0
