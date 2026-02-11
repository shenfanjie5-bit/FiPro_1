from __future__ import annotations

from app.eval.m6_rollout import build_m6_rollout_drill, render_m6_rollout_markdown


def _report(*, report_id: str, action: str = 'WATCH', schema_ok: bool = True, dq_status: str = 'OK') -> dict:
    payload = {
        'schema_version': '0.1',
        'report_id': report_id,
        'generated_at': '2026-02-11T00:00:00+00:00',
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'decision': {'action': action, 'overall_score': 62, 'confidence': 0.55, 'time_horizon': 'SWING', 'summary': 's'},
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
        'data_quality': {'status': dq_status, 'missing_fields': [], 'notes': ''},
        'provenance': {'model': {'primary': 'mock', 'reviewer': 'NONE'}, 'router_policy': 'router_m6_v1', 'snapshot_ids': ['snap_1'], 'weights_hash': 'w', 'run_mode': 'LIVE'},
        'memory_update': {'summary': 'm', 'tags': ['x'], 'importance': 50, 'followups': ['f']},
    }
    if not schema_ok:
        payload.pop('decision')
    return payload


def test_build_m6_rollout_drill_pass() -> None:
    report = build_m6_rollout_drill(
        baseline_report=_report(report_id='b1'),
        replay_report_a=_report(report_id='r1'),
        replay_report_b=_report(report_id='r2'),
        fault_report=_report(report_id='f1', action='AVOID', dq_status='DEGRADED'),
    )
    assert report['overall_status'] == 'PASS'


def test_build_m6_rollout_drill_fail_on_unstable_replay() -> None:
    report = build_m6_rollout_drill(
        baseline_report=_report(report_id='b1'),
        replay_report_a=_report(report_id='r1', action='WATCH'),
        replay_report_b=_report(report_id='r2', action='BUY'),
        fault_report=_report(report_id='f1', action='BUY', dq_status='DEGRADED'),
    )
    assert report['overall_status'] == 'FAIL'
    assert any(item['name'] == 'replay_stability' and item['pass'] is False for item in report['checks'])


def test_render_m6_rollout_markdown_contains_sections() -> None:
    report = build_m6_rollout_drill(
        baseline_report=_report(report_id='b1'),
        replay_report_a=_report(report_id='r1'),
        replay_report_b=_report(report_id='r2'),
        fault_report=_report(report_id='f1', action='AVOID', dq_status='DEGRADED'),
    )
    markdown = render_m6_rollout_markdown(report)
    assert '# M6 Rollout Drill' in markdown
    assert '## Drill Checks' in markdown
    assert '## Artifact Refs' in markdown
