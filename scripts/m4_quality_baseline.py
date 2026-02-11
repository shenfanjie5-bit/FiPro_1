#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.eval.m4_baseline import build_m4_quality_baseline, load_report_samples, render_m4_baseline_markdown


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate M4 quality baseline report artifact.')
    parser.add_argument('--lookback-days', type=int, default=14)
    parser.add_argument('--output-json', default='monitoring/dashboards/m4_quality_baseline.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m4_quality_baseline.md')
    parser.add_argument('--enforce-thresholds', action='store_true', help='Exit non-zero when baseline status is FAIL.')
    args = parser.parse_args()

    samples = load_report_samples(lookback_days=args.lookback_days)
    report = build_m4_quality_baseline(samples, lookback_days=args.lookback_days)
    markdown = render_m4_baseline_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)

    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    print(f"sample_size={report.get('overall', {}).get('sample_size', 0)}")
    if args.enforce_thresholds and report.get('overall_status') == 'FAIL':
        print('threshold_gate=FAILED')
        sys.exit(1)


if __name__ == '__main__':
    main()
