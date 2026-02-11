# M7 Offline Evaluation

- Generated At: `2026-02-11T15:12:49.360460+00:00`
- Lookback Days: `30`
- Overall Status: `FAIL`

## Track Metrics

| Track | Sample | Schema | Consistency | Citation | Coverage | Avg Cost | Avg Latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| PRIMARY | 405 | 0.9580 | 0.9580 | 1.0000 | 0.8938 | 0.0000 | 27.08 |
| CHALLENGER | 3 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 6.00 |

## Pair Summary

- paired_sample_size: `1`
- decision_change_rate: `0.0`
- challenger_win_rate: `1.0`
- avg_cost_delta_usd: `0.0`
- avg_latency_delta_ms: `-23.0`

## Threshold Checks

| Metric | Actual | Rule | Pass |
|---|---:|---|---|
| PRIMARY.sample_size | 405.0 | `>= 10` | YES |
| PRIMARY.schema_pass_rate | 0.958025 | `>= 0.99` | NO |
| PRIMARY.consistency_pass_rate | 0.958025 | `>= 0.97` | NO |
| PRIMARY.citation_consistency_rate | 1.0 | `>= 0.98` | YES |
| PRIMARY.evidence_coverage_pass_rate | 0.893827 | `>= 0.9` | NO |
| CHALLENGER.sample_size | 3.0 | `>= 10` | NO |
| CHALLENGER.schema_pass_rate | 1.0 | `>= 0.99` | YES |
| CHALLENGER.consistency_pass_rate | 1.0 | `>= 0.97` | YES |
| CHALLENGER.citation_consistency_rate | 1.0 | `>= 0.98` | YES |
| CHALLENGER.evidence_coverage_pass_rate | 1.0 | `>= 0.9` | YES |
| PAIR.paired_sample_size | 1.0 | `>= 8` | NO |
| PAIR.challenger_win_rate | 1.0 | `>= 0.45` | YES |

## Top Pair Differences

| Scenario | Primary | Challenger | Cost Delta | Latency Delta |
|---|---|---|---:|---:|
| 600519.SH|2026-02-10T09:30:00+08:00|stg_v1|TIER1 | WATCH | WATCH | 0.0 | -23 |
