#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.eval.m7_drift import build_m7_drift_report, load_rows_for_lookback, render_m7_drift_markdown


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(payload, dict) and isinstance(payload.get('rows'), list):
        return list(payload.get('rows', []))
    if isinstance(payload, list):
        return payload
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate M7 drift monitor artifact (PSI).')
    parser.add_argument('--baseline-json', default='')
    parser.add_argument('--current-json', default='')
    parser.add_argument('--baseline-lookback-days', type=int, default=30)
    parser.add_argument('--current-lookback-days', type=int, default=7)
    parser.add_argument('--output-json', default='monitoring/dashboards/m7_drift_monitor.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m7_drift_monitor.md')
    parser.add_argument('--enforce-critical', action='store_true', help='Exit non-zero when status is FAIL.')
    args = parser.parse_args()

    baseline_rows: list[dict]
    current_rows: list[dict]

    if str(args.baseline_json).strip():
        baseline_rows = _read_rows(Path(args.baseline_json))
    else:
        baseline_rows = load_rows_for_lookback(max(1, int(args.baseline_lookback_days)))

    if str(args.current_json).strip():
        current_rows = _read_rows(Path(args.current_json))
    else:
        current_rows = load_rows_for_lookback(max(1, int(args.current_lookback_days)))

    report = build_m7_drift_report(
        baseline_rows,
        current_rows,
        baseline_label='baseline',
        current_label='current',
    )
    markdown = render_m7_drift_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    print(f"alerts={len(report.get('alerts', []))}")
    if args.enforce_critical and report.get('overall_status') == 'FAIL':
        print('drift_gate=FAILED')
        sys.exit(1)


if __name__ == '__main__':
    main()
