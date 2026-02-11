# M6 Load/Soak Baseline

- Generated At: `2026-02-11T14:35:49.914697+00:00`
- Requested Count: `30`
- Actual Count: `30`
- Concurrency: `6`
- Wall Time Seconds: `0.983637`

## Summary

- success_rate: `1.0`
- failure_rate: `0.0`
- degraded_report_rate: `1.0`
- throughput_rps: `30.499072`
- latency_avg_ms: `193.9`
- latency_p95_ms: `235.65`
- latency_p99_ms: `237.0`
- cost_avg_usd: `0.0`
- cost_p95_usd: `0.0`

## Capacity Recommendation

- recommended_concurrency: `6`
- notes: Increase concurrency only when failure_rate remains <=0.03 and latency_p95_ms <= 12000 under the same workload.
