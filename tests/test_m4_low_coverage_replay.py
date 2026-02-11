from __future__ import annotations

from app.eval.low_coverage_replay import _build_request_from_report
from app.eval.m4_baseline import ReportSample, build_m4_quality_baseline


def _sample(*, report_id: str, created_at: str, schema_ok: bool, tier: str = 'TIER1') -> ReportSample:
    report_json = {
        'schema_version': '0.1',
        'report_id': report_id,
        'generated_at': created_at,
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': tier,
        'decision': {
            'action': 'WATCH',
            'overall_score': 60,
            'confidence': 0.55,
            'time_horizon': 'SWING',
            'summary': 'summary',
        },
        'price_bands': [
            {
                'band_id': 'B1',
                'range': {'currency': 'CNY', 'min': 95, 'max': 100},
                'score': 60,
                'confidence': 0.55,
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
            {
                'evidence_id': 'ev_snap_1',
                'type': 'SNAPSHOT_FIELD',
                'title': 'snapshot',
                'source': 'facts',
                'captured_at': '2026-02-11T00:00:00+00:00',
            },
            {
                'evidence_id': 'ev_doc_1',
                'type': 'NEWS_DOC',
                'title': 'news',
                'source': 'event_docs.NEWS',
                'captured_at': '2026-02-11T00:00:00+00:00',
            },
            {
                'evidence_id': 'ev_mem_1',
                'type': 'MANUAL_NOTE',
                'title': 'memory',
                'source': 'memory',
                'captured_at': '2026-02-11T00:00:00+00:00',
            },
            {
                'evidence_id': 'ev_doc_2',
                'type': 'NEWS_DOC',
                'title': 'news2',
                'source': 'event_docs.NEWS',
                'captured_at': '2026-02-11T00:00:00+00:00',
            },
        ],
        'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
        'provenance': {
            'model': {'primary': 'mock', 'reviewer': 'NONE'},
            'router_policy': 'router_m4_v1',
            'snapshot_ids': ['snap_1'],
            'weights_hash': 'w1',
            'run_mode': 'LIVE',
            'tool_call_stats': {'tool_calls': 8, 'latency_ms': 1000, 'cost_usd_est': 0.3},
        },
        'memory_update': {'summary': 'm', 'tags': ['x'], 'importance': 50, 'followups': ['f']},
    }
    if not schema_ok:
        report_json.pop('evidence_refs', None)
    return ReportSample(
        report_id=report_id,
        tier=tier,
        created_at=created_at,
        report_json=report_json,
        cost_usd=0.3,
        latency_ms=1000,
        tool_calls=8,
    )


def test_build_request_from_report_with_run_mode_strategy_same() -> None:
    report_json = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'provenance': {'run_mode': 'LIVE'},
    }
    req = _build_request_from_report(report_json, run_mode_strategy='same')
    assert req is not None
    assert req['run_mode'] == 'LIVE'


def test_build_request_from_report_with_run_mode_override() -> None:
    report_json = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'provenance': {'run_mode': 'LIVE'},
    }
    req = _build_request_from_report(report_json, run_mode_strategy='backtest')
    assert req is not None
    assert req['run_mode'] == 'BACKTEST'


def test_baseline_dedupe_latest_enables_replay_supersede() -> None:
    bad_old = _sample(report_id='bad_old', created_at='2026-02-10T00:00:00+00:00', schema_ok=False)
    good_new = _sample(report_id='good_new', created_at='2026-02-11T00:00:00+00:00', schema_ok=True)

    report = build_m4_quality_baseline([bad_old, good_new], lookback_days=14, dedupe_latest=True)
    assert report['raw_sample_size'] == 2
    assert report['effective_sample_size'] == 1
    assert report['overall']['schema_pass_rate'] == 1.0


def test_baseline_includes_raw_low_coverage_list() -> None:
    bad_old = _sample(report_id='bad_old', created_at='2026-02-10T00:00:00+00:00', schema_ok=False)
    good_new = _sample(report_id='good_new', created_at='2026-02-11T00:00:00+00:00', schema_ok=True)

    report = build_m4_quality_baseline([bad_old, good_new], lookback_days=14, dedupe_latest=True)
    assert report['tier1_low_coverage_reports'] == []
    assert report['raw_tier1_low_coverage_reports']
