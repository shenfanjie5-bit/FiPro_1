from __future__ import annotations

from app.eval.m4_baseline import ReportSample, build_m4_quality_baseline, render_m4_baseline_markdown


def _sample(
    *,
    report_id: str,
    tier: str,
    evidence_types: list[str],
    cost_usd: float,
    latency_ms: int,
    schema_ok: bool = True,
    citation_ok: bool = True,
) -> ReportSample:
    evidence_refs = [
        {
            'evidence_id': f'ev_{report_id}_{idx}',
            'type': evidence_type,
            'title': f'title-{idx}',
            'source': f'source-{idx}',
            'captured_at': '2026-02-11T00:00:00+00:00',
            'uri': None,
            'snippet': 'snippet',
            'checksum': f'ck_{idx}',
        }
        for idx, evidence_type in enumerate(evidence_types)
    ]
    evidence_ids = [ref['evidence_id'] for ref in evidence_refs]
    if not citation_ok and evidence_ids:
        evidence_ids[0] = 'ev_missing'

    report_json = {
        'schema_version': '0.1',
        'report_id': report_id,
        'generated_at': '2026-02-11T00:00:01+00:00',
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': tier,
        'decision': {
            'action': 'WATCH',
            'overall_score': 62,
            'confidence': 0.56,
            'time_horizon': 'SWING',
            'summary': 'summary',
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
                'evidence_ids': evidence_ids[:1],
                'graph_refs': [],
            }
        ],
        'thesis': {'base_case': 'b', 'bull_case': 'b', 'bear_case': 'b', 'next_steps': ['n']},
        'risk_flags': [{'risk_id': 'r1', 'severity': 'LOW', 'description': 'd', 'evidence_ids': evidence_ids[:1]}],
        'invalidations': [{'invalidation_id': 'i1', 'description': 'd', 'priority': 'LOW', 'evidence_ids': evidence_ids[:1]}],
        'evidence_refs': evidence_refs,
        'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
        'provenance': {
            'model': {'primary': 'mock', 'reviewer': 'NONE'},
            'router_policy': 'router_m4_v1',
            'snapshot_ids': ['snap_1'],
            'weights_hash': 'w1',
            'run_mode': 'LIVE',
            'tool_call_stats': {'tool_calls': 7, 'latency_ms': latency_ms, 'cost_usd_est': cost_usd},
        },
        'memory_update': {'summary': 'm', 'tags': ['x'], 'importance': 50, 'followups': ['f']},
    }

    if not schema_ok:
        report_json.pop('evidence_refs', None)

    return ReportSample(
        report_id=report_id,
        tier=tier,
        created_at='2026-02-11T00:00:00+00:00',
        report_json=report_json,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        tool_calls=7,
    )


def test_build_m4_quality_baseline_aggregates_and_evaluates_thresholds() -> None:
    samples = [
        _sample(report_id='r1', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC'], cost_usd=0.3, latency_ms=1100),
        _sample(report_id='r2', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'NEWS_DOC', 'MANUAL_NOTE'], cost_usd=0.4, latency_ms=1300),
        _sample(report_id='r3', tier='TIER0', evidence_types=['SNAPSHOT_FIELD'], cost_usd=0.1, latency_ms=900),
        _sample(report_id='r4', tier='TIER2', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC', 'NEWS_DOC', 'NEWS_DOC'], cost_usd=0.7, latency_ms=2000),
        _sample(report_id='r5', tier='TIER2', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC', 'NEWS_DOC', 'NEWS_DOC'], cost_usd=0.9, latency_ms=3000),
        _sample(report_id='r6', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC'], cost_usd=0.2, latency_ms=1000),
        _sample(report_id='r7', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC'], cost_usd=0.6, latency_ms=1800),
        _sample(report_id='r8', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC'], cost_usd=0.6, latency_ms=1900),
        _sample(report_id='r9', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC'], cost_usd=0.6, latency_ms=1900),
        _sample(report_id='r10', tier='TIER1', evidence_types=['SNAPSHOT_FIELD', 'NEWS_DOC', 'MANUAL_NOTE', 'NEWS_DOC'], cost_usd=0.6, latency_ms=1900),
    ]

    report = build_m4_quality_baseline(samples, lookback_days=14, dedupe_latest=False)
    assert report['raw_sample_size'] == 10
    assert report['effective_sample_size'] == 10
    assert report['overall']['sample_size'] == 10
    assert report['overall']['schema_pass_rate'] == 1.0
    assert report['overall']['citation_consistency_rate'] == 1.0
    assert report['overall']['evidence_coverage_pass_rate'] == 1.0
    assert report['overall_status'] == 'PASS'


def test_build_m4_quality_baseline_detects_failures() -> None:
    samples = [
        _sample(report_id='r1', tier='TIER1', evidence_types=['SNAPSHOT_FIELD'], cost_usd=1.8, latency_ms=12000, citation_ok=False),
        _sample(report_id='r2', tier='TIER1', evidence_types=['SNAPSHOT_FIELD'], cost_usd=1.6, latency_ms=11000, schema_ok=False),
    ]

    report = build_m4_quality_baseline(samples, lookback_days=14)
    assert report['overall_status'] == 'FAIL'
    assert report['overall']['evidence_coverage_pass_rate'] < 1.0
    assert any(check['metric'] == 'sample_size' and check['pass'] is False for check in report['threshold_checks'])
    assert report['tier1_low_coverage_reports']
    assert report['raw_tier1_low_coverage_reports']
    assert report['tier1_low_coverage_reports'][0]['tier'] == 'TIER1'


def test_render_m4_baseline_markdown_contains_sections() -> None:
    report = build_m4_quality_baseline([], lookback_days=14)
    markdown = render_m4_baseline_markdown(report)
    assert '# M4 Quality Baseline' in markdown
    assert '## Core Metrics' in markdown
    assert '## Threshold Checks' in markdown
    assert '## Tier Breakdown' in markdown
    assert '## TIER1 Low Coverage Reports' in markdown
    assert 'Dedupe Latest Scenario' in markdown
