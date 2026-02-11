# M7 Shadow Compare

- Generated At: `2026-02-11T15:12:49.541866+00:00`
- Lookback Days: `30`
- Overall Status: `FAIL`

## Summary

- paired_sample_size: `1`
- decision_change_rate: `0.0`
- challenger_quality_win_rate: `1.0`
- avg_cost_delta_usd: `0.0`
- avg_latency_delta_ms: `-23.0`

## Threshold Checks

| Metric | Actual | Rule | Pass |
|---|---:|---|---|
| paired_sample_size | 1.0 | `>= 10` | NO |
| decision_change_rate | 0.0 | `<= 0.4` | YES |
| abs_avg_cost_delta_usd | 0.0 | `<= 0.15` | YES |
| abs_avg_latency_delta_ms | 23.0 | `<= 1200.0` | YES |
| challenger_quality_win_rate | 1.0 | `>= 0.45` | YES |

## Model Breakdown

| Challenger Model | Pair Count | Win Rate | Decision Change | Avg Cost Delta | Avg Latency Delta |
|---|---:|---:|---:|---:|---:|
| mock-challenger-v1 | 1 | 1.0000 | 0.0000 | 0.0000 | -23.00 |

## Top Differences

| Scenario | Primary | Challenger | Cost Delta | Latency Delta |
|---|---|---|---:|---:|
| 600519.SH|2026-02-10T09:30:00+08:00|stg_v1|TIER1 | WATCH | WATCH | 0.0 | -23 |
