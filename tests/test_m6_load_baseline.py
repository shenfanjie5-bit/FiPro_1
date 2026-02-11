from __future__ import annotations

from app.eval.m6_load import render_m6_load_markdown, summarize_m6_load_results


def test_summarize_m6_load_results_core_metrics() -> None:
    rows = [
        {'ok': True, 'latency_ms': 1200, 'cost_usd': 0.4, 'data_quality_status': 'OK'},
        {'ok': True, 'latency_ms': 1500, 'cost_usd': 0.5, 'data_quality_status': 'PARTIAL'},
        {'ok': False, 'latency_ms': 1800, 'cost_usd': 0.0, 'data_quality_status': 'DEGRADED'},
    ]
    report = summarize_m6_load_results(rows, requested_count=3, concurrency=2, wall_time_seconds=3.0)
    summary = report['summary']
    assert summary['success_rate'] == 0.666667
    assert summary['failure_rate'] == 0.333333
    assert summary['degraded_report_rate'] == 0.666667
    assert summary['latency_p95_ms'] >= 1500
    assert summary['throughput_rps'] == 1.0


def test_render_m6_load_markdown_contains_sections() -> None:
    report = summarize_m6_load_results([], requested_count=0, concurrency=1, wall_time_seconds=0.1)
    markdown = render_m6_load_markdown(report)
    assert '# M6 Load/Soak Baseline' in markdown
    assert '## Summary' in markdown
    assert '## Capacity Recommendation' in markdown
