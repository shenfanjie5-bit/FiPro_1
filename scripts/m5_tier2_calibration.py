#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.eval.m5_tier2_calibration import (
    load_and_build_m5_tier2_calibration,
    render_m5_tier2_calibration_markdown,
)
from app.eval.sample_seed import seed_m4_baseline_samples


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate M5 Tier2 budget calibration artifact.')
    parser.add_argument('--lookback-days', type=int, default=14)
    parser.add_argument('--output-json', default='monitoring/dashboards/m5_tier2_calibration.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m5_tier2_calibration.md')
    parser.add_argument('--no-dedupe-latest', action='store_true')
    parser.add_argument('--enforce-thresholds', action='store_true', help='Exit non-zero when calibration status is FAIL.')
    parser.add_argument('--auto-topup-samples', action='store_true')
    parser.add_argument('--min-sample-size', type=int, default=8)
    parser.add_argument('--topup-max-rounds', type=int, default=2)
    parser.add_argument('--seed-batch-size', type=int, default=8)
    args = parser.parse_args()

    report = load_and_build_m5_tier2_calibration(lookback_days=max(1, int(args.lookback_days)), dedupe_latest=not bool(args.no_dedupe_latest))
    topup_rounds: list[dict] = []
    if args.auto_topup_samples:
        target = max(1, int(args.min_sample_size))
        max_rounds = max(1, int(args.topup_max_rounds))
        for round_index in range(1, max_rounds + 1):
            current_size = int(report.get('effective_sample_size', 0))
            if current_size >= target:
                break
            missing = target - current_size
            seed_count = max(int(args.seed_batch_size), missing)
            seed_result = seed_m4_baseline_samples(
                count=seed_count,
                tier_pattern='TIER2',
                run_mode='LIVE',
                vary_asof=True,
            )
            topup_rounds.append(
                {
                    'round': round_index,
                    'effective_sample_before': current_size,
                    'seed_count': int(seed_result.get('seed_count', 0)),
                    'latest_report_id': seed_result.get('latest_report_id', ''),
                }
            )
            report = load_and_build_m5_tier2_calibration(
                lookback_days=max(1, int(args.lookback_days)),
                dedupe_latest=not bool(args.no_dedupe_latest),
            )
    if topup_rounds:
        report.setdefault('governance', {})
        report['governance']['sample_topup'] = {
            'enabled': True,
            'target_min_sample_size': int(args.min_sample_size),
            'rounds': topup_rounds,
            'effective_sample_after': int(report.get('effective_sample_size', 0)),
        }
    markdown = render_m5_tier2_calibration_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    print(f"effective_sample_size={report.get('effective_sample_size', 0)}")
    if topup_rounds:
        print(f'topup_rounds={len(topup_rounds)}')
    if args.enforce_thresholds and report.get('overall_status') == 'FAIL':
        print('threshold_gate=FAILED')
        sys.exit(1)


if __name__ == '__main__':
    main()
