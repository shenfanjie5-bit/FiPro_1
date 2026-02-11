#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import uuid

from app.workflows.graph import run_research_workflow


def _resolve_tier_pattern(raw: str) -> list[str]:
    items = [item.strip().upper() for item in str(raw).split(',') if item.strip()]
    valid = [item for item in items if item in {'TIER0', 'TIER1', 'TIER2'}]
    return valid or ['TIER0', 'TIER1']


def main() -> None:
    parser = argparse.ArgumentParser(description='Seed workflow reports for M4 baseline CI gating.')
    parser.add_argument('--count', type=int, default=12)
    parser.add_argument('--ticker', default='600519.SH')
    parser.add_argument('--market', default='CN_A')
    parser.add_argument('--strategy-version-id', default='stg_v1')
    parser.add_argument('--run-mode', default='LIVE')
    parser.add_argument('--asof', default='2026-02-10T09:30:00+08:00')
    parser.add_argument('--tier-pattern', default='TIER0,TIER1')
    parser.add_argument('--vary-asof', action='store_true', help='Offset asof per sample to avoid dedupe collapsing.')
    args = parser.parse_args()

    tiers = _resolve_tier_pattern(args.tier_pattern)
    count = max(1, int(args.count))

    seeded_report_ids: list[str] = []
    base_asof = datetime.fromisoformat(str(args.asof).replace('Z', '+00:00'))
    if base_asof.tzinfo is None:
        base_asof = base_asof.replace(tzinfo=timezone.utc)
    for idx in range(count):
        tier = tiers[idx % len(tiers)]
        if args.vary_asof:
            asof = (base_asof + timedelta(minutes=idx)).isoformat()
        else:
            asof = str(args.asof)
        payload = {
            'ticker': args.ticker,
            'market': args.market,
            'asof': asof,
            'strategy_version_id': args.strategy_version_id,
            'tier': tier,
            'run_mode': args.run_mode,
        }
        thread_id = f"seed_m4_{tier.lower()}_{uuid.uuid4().hex[:8]}"
        result = run_research_workflow(request_data=payload, thread_id=thread_id)
        seeded_report_ids.append(result['final_report']['report_id'])

    now_iso = datetime.now(timezone.utc).isoformat()
    print(f'seeded_at={now_iso}')
    print(f'seed_count={len(seeded_report_ids)}')
    print(f'seed_tiers={"|".join(tiers)}')
    if seeded_report_ids:
        print(f'latest_report_id={seeded_report_ids[-1]}')


if __name__ == '__main__':
    main()
