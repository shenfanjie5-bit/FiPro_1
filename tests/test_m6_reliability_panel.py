from __future__ import annotations

from app.eval.m6_reliability import (
    ReliabilitySample,
    build_m6_reliability_panel,
    render_m6_reliability_markdown,
)


def _sample(
    *,
    report_id: str,
    status: str,
    schema_ok: bool = True,
    latency_ms: int = 2000,
    cost_usd: float = 0.6,
) -> ReliabilitySample:
    report_json = {
        'schema_version': '0.1',
        'report_id': report_id,
        'generated_at': '2026-02-11T00:00:01+00:00',
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'decision': {'action': 'WATCH', 'overall_score': 60, 'confidence': 0.6, 'time_horizon': 'SWING', 'summary': 'ok'},
        'price_bands': [
            {
                'band_id': 'B1',
                'range': {'currency': 'CNY', 'min': 95, 'max': 100},
                'score': 60,
                'confidence': 0.5,
                'rationale': 'r',
                'entry_conditions': [{'type': 'TECHNICAL', 'description': 'd', 'priority': 'MEDIUM'}],
                'exit_conditions': [{'type': 'RISK', 'description': 'd', 'priority': 'HIGH'}],
            }
        ],
        'key_drivers_to_watch': [
            {
                'driver_id': 'drv_1',
                'type': 'SUPPLY_DEMAND',
                'what': 'w',
                'direction': 'UNCERTAIN',
                'urgency': 'MEDIUM',
                'impact_hypothesis': 'h',
                'monitor': {'signals': [{'name': 'n', 'source': 's'}], 'triggers': [{'description': 'd', 'severity': 'LOW'}]},
                'evidence_ids': ['ev_1'],
                'graph_refs': [],
            }
        ],
        'thesis': {'base_case': 'b', 'bull_case': 'b', 'bear_case': 'b', 'next_steps': ['n']},
        'risk_flags': [{'risk_id': 'r1', 'severity': 'LOW', 'description': 'd', 'evidence_ids': ['ev_1']}],
        'invalidations': [{'invalidation_id': 'i1', 'description': 'd', 'priority': 'LOW', 'evidence_ids': ['ev_1']}],
        'evidence_refs': [{'evidence_id': 'ev_1', 'type': 'SNAPSHOT_FIELD', 'title': 't', 'source': 's', 'captured_at': '2026-02-10T00:00:00+00:00'}],
        'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
        'provenance': {
            'model': {'primary': 'mock', 'reviewer': 'NONE'},
            'router_policy': 'router_m6_v1',
            'snapshot_ids': ['snap_1'],
            'weights_hash': 'w1',
            'run_mode': 'LIVE',
            'tool_call_stats': {'tool_calls': 8, 'latency_ms': latency_ms, 'cost_usd_est': cost_usd, 'retry_count': 1},
        },
        'memory_update': {'summary': 'm', 'tags': ['x'], 'importance': 50, 'followups': ['f']},
    }
    if not schema_ok:
        report_json.pop('decision')
    return ReliabilitySample(
        report_id=report_id,
        tier='TIER1',
        status=status,
        created_at=f'2026-02-11T0{int(report_id[-1]) % 8}:00:00+00:00',
        report_json=report_json,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def test_build_m6_reliability_panel_pass() -> None:
    samples = [_sample(report_id=f'r{i}', status='DONE', latency_ms=2200 + i * 100, cost_usd=0.5 + i * 0.02) for i in range(10)]
    report = build_m6_reliability_panel(samples, lookback_days=7, dedupe_latest=False)
    assert report['overall_status'] == 'PASS'
    assert report['summary']['sample_size'] == 10
    assert report['summary']['success_rate'] == 1.0
    assert report['summary']['failure_rate'] == 0.0


def test_build_m6_reliability_panel_fail() -> None:
    samples = [
        _sample(report_id='f0', status='FAILED', schema_ok=False, latency_ms=20000, cost_usd=4.0),
        _sample(report_id='f1', status='FAILED', schema_ok=False, latency_ms=19000, cost_usd=3.8),
        _sample(report_id='f2', status='DONE', schema_ok=True, latency_ms=15000, cost_usd=3.3),
    ]
    report = build_m6_reliability_panel(samples, lookback_days=7, dedupe_latest=False)
    assert report['overall_status'] == 'FAIL'
    assert any(item['metric'] == 'failure_rate' and item['pass'] is False for item in report['threshold_checks'])


def test_render_m6_reliability_markdown_contains_sections() -> None:
    report = build_m6_reliability_panel([], lookback_days=7)
    markdown = render_m6_reliability_markdown(report)
    assert '# M6 Reliability Panel' in markdown
    assert '## Core Metrics' in markdown
    assert '## Threshold Checks' in markdown
    assert '## Daily Trend' in markdown
