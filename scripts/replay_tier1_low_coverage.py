#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.eval.low_coverage_replay import replay_tier1_low_coverage_reports, write_replay_artifacts
from app.eval.m4_baseline import render_m4_baseline_markdown


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description='Replay and repair TIER1 low-coverage reports in batches.')
    parser.add_argument('--lookback-days', type=int, default=14)
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--max-rounds', type=int, default=3)
    parser.add_argument('--run-mode-strategy', choices=['same', 'live', 'backtest', 'shadow'], default='same')
    parser.add_argument('--baseline-json', default='')
    parser.add_argument('--output-json', default='monitoring/dashboards/m4_low_coverage_replay.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m4_low_coverage_replay.md')
    parser.add_argument('--update-baseline-artifacts', action='store_true')
    parser.add_argument('--enforce-pass', action='store_true', help='Exit non-zero if final baseline status is not PASS.')
    args = parser.parse_args()

    baseline_json = args.baseline_json.strip() or None
    result = replay_tier1_low_coverage_reports(
        lookback_days=args.lookback_days,
        batch_size=args.batch_size,
        max_rounds=args.max_rounds,
        run_mode_strategy=args.run_mode_strategy,
        baseline_json_path=baseline_json,
    )

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    write_replay_artifacts(result, output_json=output_json_path, output_md=output_md_path)

    if args.update_baseline_artifacts:
        baseline_after = result.get('baseline_after', {})
        _write_text(Path('monitoring/dashboards/m4_quality_baseline.json'), json.dumps(baseline_after, ensure_ascii=True, indent=2, sort_keys=True))
        _write_text(Path('monitoring/dashboards/m4_quality_baseline.md'), render_m4_baseline_markdown(baseline_after))

    print(f'output_json={output_json_path}')
    print(f'output_md={output_md_path}')
    print(f"initial_status={result.get('initial_status', 'UNKNOWN')}")
    print(f"final_status={result.get('final_status', 'UNKNOWN')}")
    print(f"replayed_total={result.get('replayed_total', 0)}")
    print(f"tier1_low_coverage_before={result.get('initial_tier1_low_coverage_count', 0)}")
    print(f"tier1_low_coverage_after={result.get('final_tier1_low_coverage_count', 0)}")

    if args.enforce_pass and result.get('final_status') != 'PASS':
        sys.exit(1)


if __name__ == '__main__':
    main()
