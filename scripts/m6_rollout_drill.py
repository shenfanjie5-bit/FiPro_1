#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from app.eval.m6_rollout import build_m6_rollout_drill, render_m6_rollout_markdown
from app.workflows.graph import run_research_workflow


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _payload(tier: str, minute_offset: int = 0) -> dict:
    asof_base = datetime(2026, 2, 10, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    return {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': (asof_base + timedelta(minutes=minute_offset)).isoformat(),
        'strategy_version_id': 'stg_v1',
        'tier': tier,
        'run_mode': 'LIVE',
    }


def _run(payload: dict, prefix: str) -> dict:
    thread_id = f"{prefix}_{uuid.uuid4().hex[:8]}"
    return run_research_workflow(request_data=payload, thread_id=thread_id).get('final_report', {})


def main() -> None:
    parser = argparse.ArgumentParser(description='Run M6 rollout drill (fault + replay) and emit artifact.')
    parser.add_argument('--tier', default='TIER1', choices=['TIER0', 'TIER1', 'TIER2'])
    parser.add_argument('--output-json', default='monitoring/dashboards/m6_rollout_drill.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m6_rollout_drill.md')
    parser.add_argument('--enforce-checks', action='store_true', help='Exit non-zero when drill status is FAIL.')
    args = parser.parse_args()

    baseline_report = _run(_payload(str(args.tier), minute_offset=0), 'thread_m6_drill_baseline')
    replay_report_a = _run(_payload(str(args.tier), minute_offset=1), 'thread_m6_drill_replaya')
    replay_report_b = _run(_payload(str(args.tier), minute_offset=1), 'thread_m6_drill_replayb')

    previous_llm_force_failure = os.getenv('LLM_FORCE_FAILURE')
    try:
        os.environ['LLM_FORCE_FAILURE'] = 'timeout'
        fault_report = _run(_payload(str(args.tier), minute_offset=2), 'thread_m6_drill_fault')
    finally:
        if previous_llm_force_failure is None:
            os.environ.pop('LLM_FORCE_FAILURE', None)
        else:
            os.environ['LLM_FORCE_FAILURE'] = previous_llm_force_failure

    report = build_m6_rollout_drill(
        baseline_report=baseline_report,
        replay_report_a=replay_report_a,
        replay_report_b=replay_report_b,
        fault_report=fault_report,
    )
    markdown = render_m6_rollout_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"overall_status={report.get('overall_status', 'UNKNOWN')}")
    if args.enforce_checks and report.get('overall_status') == 'FAIL':
        print('drill_gate=FAILED')
        sys.exit(1)


if __name__ == '__main__':
    main()
