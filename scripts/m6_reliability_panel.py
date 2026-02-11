#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.eval.m6_reliability import (
    load_and_build_m6_reliability_panel,
    render_m6_reliability_markdown,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate M6 reliability panel artifact.')
    parser.add_argument('--lookback-days', type=int, default=7)
    parser.add_argument('--output-json', default='monitoring/dashboards/m6_reliability_panel.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m6_reliability_panel.md')
    parser.add_argument('--no-dedupe-latest', action='store_true')
    parser.add_argument('--enforce-thresholds', action='store_true', help='Exit non-zero when status is FAIL.')
    args = parser.parse_args()

    report = load_and_build_m6_reliability_panel(
        lookback_days=max(1, int(args.lookback_days)),
        dedupe_latest=not bool(args.no_dedupe_latest),
    )
    markdown = render_m6_reliability_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    print(f"effective_sample_size={report.get('effective_sample_size', 0)}")
    if args.enforce_thresholds and report.get('overall_status') == 'FAIL':
        print('threshold_gate=FAILED')
        sys.exit(1)


if __name__ == '__main__':
    main()
