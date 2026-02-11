from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from app.workflows.graph import run_research_workflow


def resolve_tier_pattern(raw: str) -> list[str]:
    items = [item.strip().upper() for item in str(raw).split(',') if item.strip()]
    valid = [item for item in items if item in {'TIER0', 'TIER1', 'TIER2'}]
    return valid or ['TIER0', 'TIER1']


def seed_m4_baseline_samples(
    *,
    count: int = 12,
    ticker: str = '600519.SH',
    market: str = 'CN_A',
    strategy_version_id: str = 'stg_v1',
    run_mode: str = 'LIVE',
    asof: str | None = None,
    tier_pattern: str = 'TIER0,TIER1',
    vary_asof: bool = True,
) -> dict[str, object]:
    tiers = resolve_tier_pattern(tier_pattern)
    safe_count = max(1, int(count))

    if asof:
        base_asof = datetime.fromisoformat(str(asof).replace('Z', '+00:00'))
    else:
        base_asof = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if base_asof.tzinfo is None:
        base_asof = base_asof.replace(tzinfo=timezone.utc)

    seeded_report_ids: list[str] = []
    for idx in range(safe_count):
        tier = tiers[idx % len(tiers)]
        if vary_asof:
            asof_value = (base_asof + timedelta(minutes=idx)).isoformat()
        else:
            asof_value = base_asof.isoformat()
        payload = {
            'ticker': ticker,
            'market': market,
            'asof': asof_value,
            'strategy_version_id': strategy_version_id,
            'tier': tier,
            'run_mode': run_mode,
        }
        thread_id = f"seed_m4_{tier.lower()}_{uuid.uuid4().hex[:8]}"
        result = run_research_workflow(request_data=payload, thread_id=thread_id)
        seeded_report_ids.append(str(result['final_report']['report_id']))

    return {
        'seeded_at': datetime.now(timezone.utc).isoformat(),
        'seed_count': len(seeded_report_ids),
        'seed_tiers': tiers,
        'base_asof': base_asof.isoformat(),
        'seeded_report_ids': seeded_report_ids,
        'latest_report_id': seeded_report_ids[-1] if seeded_report_ids else '',
    }
