#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.eval.m7_model_gate import build_m7_model_gate, render_m7_model_gate_markdown


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description='Evaluate M7 model promotion gate based on eval/shadow/drift artifacts.')
    parser.add_argument('--offline-eval-json', default='monitoring/dashboards/m7_offline_eval.json')
    parser.add_argument('--shadow-compare-json', default='monitoring/dashboards/m7_shadow_compare.json')
    parser.add_argument('--drift-json', default='monitoring/dashboards/m7_drift_monitor.json')
    parser.add_argument('--candidate-model', default='challenger')
    parser.add_argument('--current-model', default='primary')
    parser.add_argument('--output-json', default='monitoring/dashboards/m7_model_gate.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m7_model_gate.md')
    parser.add_argument('--enforce-block', action='store_true', help='Exit non-zero when decision is BLOCK.')
    args = parser.parse_args()

    offline_eval = _read_json(Path(args.offline_eval_json))
    shadow_compare = _read_json(Path(args.shadow_compare_json))
    drift_report = _read_json(Path(args.drift_json)) if str(args.drift_json).strip() else {}

    report = build_m7_model_gate(
        offline_eval,
        shadow_compare,
        drift_report=drift_report or None,
        candidate_model=str(args.candidate_model),
        current_model=str(args.current_model),
    )
    markdown = render_m7_model_gate_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"decision={report.get('decision', 'UNKNOWN')}")
    if args.enforce_block and report.get('decision') == 'BLOCK':
        print('promotion_gate=BLOCKED')
        sys.exit(1)


if __name__ == '__main__':
    main()
