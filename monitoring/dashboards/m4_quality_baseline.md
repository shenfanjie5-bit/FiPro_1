# M4 Quality Baseline

- Generated At: `2026-02-11T08:24:03.872689+00:00`
- Lookback Days: `14`
- Dedupe Latest Scenario: `True`
- Overall Status: `FAIL`
- Effective Sample Size: `2`
- Raw Sample Size: `206`

## Core Metrics

| Metric | Value |
|---|---:|
| schema_pass_rate | 1.0000 |
| citation_consistency_rate | 1.0000 |
| evidence_coverage_pass_rate | 1.0000 |
| avg_evidence_refs | 9.50 |
| avg_latency_ms | 7.50 |
| latency_p95_ms | 10.65 |
| avg_cost_usd | 0.0000 |
| cost_p95_usd | 0.0000 |

## Threshold Checks

| Metric | Actual | Rule | Pass |
|---|---:|---|---|
| schema_pass_rate | 1.0 | `>= 1.0` | YES |
| citation_consistency_rate | 1.0 | `>= 0.98` | YES |
| evidence_coverage_pass_rate | 1.0 | `>= 0.9` | YES |
| latency_p95_ms | 10.65 | `<= 7000.0` | YES |
| avg_cost_usd | 0.0 | `<= 0.9` | YES |
| sample_size | 2.0 | `>= 10.0` | NO |

## Tier Breakdown

| Tier | Sample | Coverage Rate | Avg Cost | Budget Max | Budget OK |
|---|---:|---:|---:|---:|---|
| TIER0 | 1 | 1.0000 | 0.0000 | 0.2000 | YES |
| TIER1 | 1 | 1.0000 | 0.0000 | 0.8000 | YES |
| TIER2 | 0 | 0.0000 | 0.0000 | 2.5000 | YES |

## TIER1 Low Coverage Reports

- Effective Low Coverage Count: `0`
- Raw Low Coverage Count: `28`

- No low-coverage TIER1 reports in the selected window.
