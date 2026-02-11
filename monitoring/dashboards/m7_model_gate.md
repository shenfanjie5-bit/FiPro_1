# M7 Model Switch Gate

- Generated At: `2026-02-11T15:12:49.857317+00:00`
- Decision: `BLOCK`
- Candidate Model: `challenger`
- Current Model: `primary`

## Gate Checks

| Metric | Actual | Rule | Pass |
|---|---:|---|---|
| shadow_pairs | 1.0 | `>= 20` | NO |
| challenger_schema_pass_rate | 1.0 | `>= 0.99` | YES |
| challenger_coverage_pass_rate | 1.0 | `>= 0.9` | YES |
| challenger_quality_win_rate | 1.0 | `>= 0.5` | YES |
| abs_avg_cost_delta_usd | 0.0 | `<= 0.1` | YES |
| abs_avg_latency_delta_ms | 23.0 | `<= 800.0` | YES |
| critical_drift_dimensions | 0.0 | `<= 0.0` | YES |

## Failed Checks

- `shadow_pairs`

## Rollout / Rollback

- step_1: Start with 5% traffic shadow-only verification window for 24h.
- step_2: Promote to 10% live canary with automatic rollback.
- step_3: Rollback immediately when failure_rate or schema_pass_rate crosses M6 thresholds.
- rollback_model: `primary`
