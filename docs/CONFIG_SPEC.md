# CONFIG_SPEC

## Strategy Config Object
```json
{
  "strategy_version_id": "stg_v1",
  "mode": "hot_leader_low_risk",
  "weights": {
    "hotness": 0.25,
    "fundamental": 0.30,
    "volatility": 0.20,
    "liquidity": 0.15,
    "event_impact": 0.10
  },
  "risk_profile": "LOW",
  "constraints": {
    "max_drawdown": 0.08,
    "volatility_cap": 0.25,
    "position_cap": 0.20,
    "industry_concentration_cap": 0.35,
    "blacklist": []
  },
  "tier_budget": {
    "TIER0": {"max_tool_calls": 20, "max_cost_usd": 0.2},
    "TIER1": {"max_tool_calls": 45, "max_cost_usd": 0.8},
    "TIER2": {"max_tool_calls": 90, "max_cost_usd": 2.5}
  }
}
```

## Validation Rules
- Sum of `weights` = 1.0
- `max_drawdown` in [0, 0.5]
- `volatility_cap` in [0, 1]
- `position_cap` in (0, 1]
- `industry_concentration_cap` in (0, 1]

## Versioning Rules
- Published version immutable
- Breaking config field changes require `schema_version` bump
