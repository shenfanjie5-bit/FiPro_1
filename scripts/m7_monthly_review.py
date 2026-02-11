#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eval.m7_monthly_review import build_m7_monthly_review, render_m7_monthly_review_markdown
from app.workflows.persistence import list_report_feedback


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _read_json(path: str) -> dict:
    target = Path(path)
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding='utf-8'))
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description='Build M7 monthly review summary from feedback/drift/shadow artifacts.')
    parser.add_argument('--month', default='')
    parser.add_argument('--offline-eval-json', default='monitoring/dashboards/m7_offline_eval.json')
    parser.add_argument('--shadow-compare-json', default='monitoring/dashboards/m7_shadow_compare.json')
    parser.add_argument('--drift-json', default='monitoring/dashboards/m7_drift_monitor.json')
    parser.add_argument('--feedback-limit', type=int, default=500)
    parser.add_argument('--output-json', default='monitoring/dashboards/m7_monthly_review.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m7_monthly_review.md')
    args = parser.parse_args()

    feedback_rows = list_report_feedback(report_id=None, limit=max(1, int(args.feedback_limit)))
    offline_eval = _read_json(str(args.offline_eval_json))
    shadow_compare = _read_json(str(args.shadow_compare_json))
    drift_report = _read_json(str(args.drift_json))

    report = build_m7_monthly_review(
        feedback_rows,
        offline_eval_report=offline_eval or None,
        shadow_compare_report=shadow_compare or None,
        drift_report=drift_report or None,
        month_label=str(args.month),
    )
    markdown = render_m7_monthly_review_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    print(f"backlog_items={len(report.get('backlog', []))}")


if __name__ == '__main__':
    main()
