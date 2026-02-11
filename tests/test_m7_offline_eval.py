from __future__ import annotations

from app.eval.m4_baseline import ReportSample
from app.eval.m7_offline_eval import build_m7_offline_eval, render_m7_offline_eval_markdown


def _valid_report_json(*, report_id: str, tier: str, run_mode: str, action: str = 'WATCH') -> dict:
    return {
        'schema_version': '0.1',
        'report_id': report_id,
        'generated_at': '2026-02-11T00:00:00+00:00',
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': tier,
        'decision': {
            'action': action,
            'overall_score': 65,
            'confidence': 0.66,
            'time_horizon': 'SWING',
            'summary': 'ok',
        },
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
                'evidence_ids': ['ev_snap_1'],
                'graph_refs': [],
            }
        ],
        'thesis': {'base_case': 'b', 'bull_case': 'b', 'bear_case': 'b', 'next_steps': ['n']},
        'risk_flags': [{'risk_id': 'r1', 'severity': 'LOW', 'description': 'd', 'evidence_ids': ['ev_snap_1']}],
        'invalidations': [{'invalidation_id': 'i1', 'description': 'd', 'priority': 'LOW', 'evidence_ids': ['ev_snap_1']}],
        'evidence_refs': [
            {'evidence_id': 'ev_snap_1', 'type': 'SNAPSHOT_FIELD', 'title': 't', 'source': 's', 'captured_at': '2026-02-10T00:00:00+00:00'},
            {'evidence_id': 'ev_doc_1', 'type': 'NEWS_DOC', 'title': 't', 'source': 's', 'captured_at': '2026-02-10T00:00:00+00:00'},
            {'evidence_id': 'ev_doc_2', 'type': 'NEWS_DOC', 'title': 't', 'source': 's', 'captured_at': '2026-02-10T00:00:00+00:00'},
            {'evidence_id': 'ev_mem_1', 'type': 'MANUAL_NOTE', 'title': 't', 'source': 's', 'captured_at': '2026-02-10T00:00:00+00:00'},
        ],
        'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
        'provenance': {
            'model': {'primary': 'mock-primary-v1' if run_mode != 'SHADOW' else 'mock-challenger-v1', 'reviewer': 'NONE'},
            'router_policy': 'router_m7_v1',
            'snapshot_ids': ['snap_1'],
            'weights_hash': 'w1',
            'run_mode': run_mode,
            'tool_call_stats': {'tool_calls': 8, 'latency_ms': 1200, 'cost_usd_est': 0.4},
        },
        'memory_update': {'summary': 'm', 'tags': ['x'], 'importance': 50, 'followups': ['f']},
    }


def _sample(
    *,
    report_id: str,
    run_mode: str,
    created_at: str,
    cost_usd: float,
    latency_ms: int,
    action: str = 'WATCH',
    asof: str = '2026-02-10T09:30:00+08:00',
) -> ReportSample:
    report_json = _valid_report_json(report_id=report_id, tier='TIER1', run_mode=run_mode, action=action)
    report_json['asof'] = asof
    return ReportSample(
        report_id=report_id,
        tier='TIER1',
        created_at=created_at,
        report_json=report_json,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        tool_calls=8,
    )


def test_build_m7_offline_eval_pass() -> None:
    samples: list[ReportSample] = []
    for i in range(10):
        asof = f'2026-02-10T09:{30 + i:02d}:00+08:00'
        samples.append(
            _sample(
                report_id=f'live_{i}',
                run_mode='LIVE',
                created_at=f'2026-02-11T0{i}:00:00+00:00',
                cost_usd=0.45,
                latency_ms=1200,
                action='WATCH',
                asof=asof,
            )
        )
        samples.append(
            _sample(
                report_id=f'shadow_{i}',
                run_mode='SHADOW',
                created_at=f'2026-02-11T1{i}:00:00+00:00',
                cost_usd=0.40,
                latency_ms=1100,
                action='WATCH',
                asof=asof,
            )
        )

    report = build_m7_offline_eval(samples, lookback_days=30)
    assert report['overall_status'] == 'PASS'
    assert report['tracks']['PRIMARY']['sample_size'] == 10
    assert report['tracks']['CHALLENGER']['sample_size'] == 10
    assert report['pair_summary']['paired_sample_size'] == 10


def test_build_m7_offline_eval_fail_with_small_sample() -> None:
    samples = [
        _sample(report_id='live_a', run_mode='LIVE', created_at='2026-02-11T00:00:00+00:00', cost_usd=0.5, latency_ms=1300),
        _sample(report_id='live_b', run_mode='LIVE', created_at='2026-02-11T01:00:00+00:00', cost_usd=0.5, latency_ms=1300),
        _sample(report_id='live_c', run_mode='LIVE', created_at='2026-02-11T02:00:00+00:00', cost_usd=0.5, latency_ms=1300),
    ]
    report = build_m7_offline_eval(samples, lookback_days=30)
    assert report['overall_status'] == 'FAIL'


def test_render_m7_offline_eval_markdown_contains_sections() -> None:
    report = build_m7_offline_eval([], lookback_days=30)
    markdown = render_m7_offline_eval_markdown(report)
    assert '# M7 Offline Evaluation' in markdown
    assert '## Track Metrics' in markdown
    assert '## Pair Summary' in markdown
