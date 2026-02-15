#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.backtest.champion_watchdog import render_champion_watchdog_markdown, run_champion_watchdog
from app.tools.facts import get_index_market_snapshot, get_market_snapshot
from app.workflows.graph import run_research_workflow


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'json root must be object: {path}')
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description='Run champion watchdog and generate alert/recommendation artifacts.')
    parser.add_argument('--run-health-check', action='store_true', help='Run one champion health check before watchdog evaluation.')
    parser.add_argument('--health-check-json', default='', help='JSON file for health check request payload.')
    parser.add_argument('--lookback-runs', type=int, default=20)
    parser.add_argument('--consecutive-fail-critical', type=int, default=2)
    parser.add_argument('--fail-rate-warn', type=float, default=0.25)
    parser.add_argument('--fail-rate-critical', type=float, default=0.50)
    parser.add_argument('--rollback-storm-critical', type=int, default=2)
    parser.add_argument('--output-json', default='monitoring/dashboards/champion_watchdog.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/champion_watchdog.md')
    parser.add_argument('--enforce-critical', action='store_true', help='Exit non-zero when overall status is CRITICAL.')
    parser.add_argument(
        '--enforce-rollback-recommendation',
        action='store_true',
        help='Exit non-zero when watchdog recommends rollback.',
    )
    args = parser.parse_args()

    health_check_request = {}
    if args.run_health_check:
        if not str(args.health_check_json).strip():
            raise ValueError('--health-check-json is required when --run-health-check is enabled')
        health_check_request = _read_json(Path(args.health_check_json))

    report = run_champion_watchdog(
        run_health_check=bool(args.run_health_check),
        health_check_request=health_check_request,
        runner=run_research_workflow,
        snapshot_loader=get_market_snapshot,
        benchmark_loader=get_index_market_snapshot,
        lookback_runs=max(1, int(args.lookback_runs)),
        consecutive_fail_critical=max(1, int(args.consecutive_fail_critical)),
        fail_rate_warn=max(0.0, min(1.0, float(args.fail_rate_warn))),
        fail_rate_critical=max(0.0, min(1.0, float(args.fail_rate_critical))),
        rollback_storm_critical=max(1, int(args.rollback_storm_critical)),
    )
    markdown = render_champion_watchdog_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"run_id={report.get('run_id', '')}")
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    print(f"alerts={report.get('alert_count', 0)}")

    recommendation = report.get('rollback_recommendation', {})
    should_rollback = bool(isinstance(recommendation, dict) and recommendation.get('should_rollback', False))
    print(f'recommend_rollback={should_rollback}')
    if should_rollback:
        print(f"rollback_target={recommendation.get('target_version', '')}")

    if args.enforce_critical and str(report.get('overall_status', '')).upper() == 'CRITICAL':
        print('watchdog_gate=CRITICAL')
        sys.exit(1)
    if args.enforce_rollback_recommendation and should_rollback:
        print('watchdog_gate=ROLLBACK_RECOMMENDED')
        sys.exit(1)


if __name__ == '__main__':
    main()
