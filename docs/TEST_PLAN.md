# TEST_PLAN

## Unit Tests
- Factor scoring correctness (`score_signal`)
- Risk gating boundary conditions (`risk_gate`)
- JSON schema validation pass/fail cases

## Integration Tests
- Data source timeout -> degraded report behavior
- Missing field propagation to `data_quality` and confidence downgrade
- Report persistence with linked traces and decision logs

## Replay Tests
- Given fixed snapshot + strategy version, output action and key fields are reproducible
- Store replay artifacts for diffing by commit hash

## Quality Gates (CI)
- Lint/type checks pass
- Unit test coverage >= 70% on core modules
- No endpoint contract drift versus `docs/OPENAPI.yaml`
