#!/usr/bin/env python3
from __future__ import annotations

import argparse

from app.eval.sample_seed import seed_m4_baseline_samples


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

    result = seed_m4_baseline_samples(
        count=int(args.count),
        ticker=str(args.ticker),
        market=str(args.market),
        strategy_version_id=str(args.strategy_version_id),
        run_mode=str(args.run_mode),
        asof=str(args.asof),
        tier_pattern=str(args.tier_pattern),
        vary_asof=bool(args.vary_asof),
    )

    print(f"seeded_at={result.get('seeded_at', '')}")
    print(f"seed_count={result.get('seed_count', 0)}")
    seed_tiers = '|'.join(result.get('seed_tiers', [])) if isinstance(result.get('seed_tiers'), list) else ''
    print(f'seed_tiers={seed_tiers}')
    latest_report_id = str(result.get('latest_report_id', '')).strip()
    if latest_report_id:
        print(f'latest_report_id={latest_report_id}')


if __name__ == '__main__':
    main()
