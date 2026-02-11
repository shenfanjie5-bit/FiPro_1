#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import uuid

from app.eval.m6_load import render_m6_load_markdown, summarize_m6_load_results
from app.workflows.graph import run_research_workflow


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _payload(index: int, tier: str) -> dict:
    tickers = ['600519.SH', '000858.SZ', '601318.SH', '300750.SZ', '600036.SH']
    asof_base = datetime(2026, 2, 10, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    asof = (asof_base + timedelta(minutes=index % 90)).isoformat()
    return {
        'ticker': tickers[index % len(tickers)],
        'market': 'CN_A',
        'asof': asof,
        'strategy_version_id': 'stg_v1',
        'tier': tier,
        'run_mode': 'LIVE',
    }


def _run_one(index: int, tier: str) -> dict:
    payload = _payload(index, tier)
    thread_id = f"thread_m6_load_{index}_{uuid.uuid4().hex[:8]}"
    start = time.perf_counter()
    try:
        result = run_research_workflow(request_data=payload, thread_id=thread_id)
        report = result.get('final_report', {})
        latency_ms = int((time.perf_counter() - start) * 1000)
        tool_stats = report.get('provenance', {}).get('tool_call_stats', {})
        return {
            'ok': True,
            'report_id': report.get('report_id', ''),
            'thread_id': thread_id,
            'latency_ms': latency_ms,
            'cost_usd': float(tool_stats.get('cost_usd_est', 0.0)),
            'tool_calls': int(tool_stats.get('tool_calls', 0)),
            'retry_count': int(tool_stats.get('retry_count', 0)),
            'data_quality_status': str(report.get('data_quality', {}).get('status', 'OK')),
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {
            'ok': False,
            'report_id': '',
            'thread_id': thread_id,
            'latency_ms': latency_ms,
            'cost_usd': 0.0,
            'tool_calls': 0,
            'retry_count': 0,
            'data_quality_status': 'DEGRADED',
            'error': str(exc),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description='Run M6 load/soak baseline and emit dashboard artifacts.')
    parser.add_argument('--requests', type=int, default=60)
    parser.add_argument('--concurrency', type=int, default=6)
    parser.add_argument('--tier', default='TIER1', choices=['TIER0', 'TIER1', 'TIER2'])
    parser.add_argument('--output-json', default='monitoring/dashboards/m6_load_baseline.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m6_load_baseline.md')
    args = parser.parse_args()

    request_count = max(1, int(args.requests))
    concurrency = max(1, int(args.concurrency))
    started = time.perf_counter()
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_run_one, index, str(args.tier)) for index in range(request_count)]
        for future in as_completed(futures):
            rows.append(future.result())

    wall_time_seconds = time.perf_counter() - started
    report = summarize_m6_load_results(
        rows,
        requested_count=request_count,
        concurrency=concurrency,
        wall_time_seconds=wall_time_seconds,
    )
    markdown = render_m6_load_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"success_rate={report.get('summary', {}).get('success_rate', 0.0)}")
    print(f"latency_p95_ms={report.get('summary', {}).get('latency_p95_ms', 0.0)}")


if __name__ == '__main__':
    main()
