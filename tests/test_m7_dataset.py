from __future__ import annotations

from app.eval.m4_baseline import ReportSample
from app.eval.m7_dataset import build_m7_offline_dataset, render_m7_dataset_markdown


def _sample(
    *,
    report_id: str,
    ticker: str,
    tier: str,
    run_mode: str,
    score: int,
    created_at: str,
    evidence_refs: int,
) -> ReportSample:
    refs = [
        {
            'evidence_id': f'ev_{report_id}_{idx}',
            'type': 'NEWS_DOC' if idx % 2 else 'SNAPSHOT_FIELD',
            'title': 't',
            'source': 's',
            'captured_at': '2026-02-11T00:00:00+00:00',
        }
        for idx in range(evidence_refs)
    ]
    return ReportSample(
        report_id=report_id,
        tier=tier,
        created_at=created_at,
        report_json={
            'ticker': ticker,
            'asof': '2026-02-10T09:30:00+08:00',
            'strategy_version_id': 'stg_v1',
            'tier': tier,
            'decision': {'action': 'WATCH', 'overall_score': score, 'confidence': 0.6},
            'evidence_refs': refs,
            'risk_flags': [],
            'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
            'provenance': {'run_mode': run_mode, 'model': {'primary': 'mock-primary-v1'}},
        },
        cost_usd=0.3,
        latency_ms=1200,
        tool_calls=6,
    )


def test_build_m7_offline_dataset_stratified_rows() -> None:
    samples = [
        _sample(
            report_id='r1',
            ticker='600519.SH',
            tier='TIER1',
            run_mode='LIVE',
            score=72,
            created_at='2026-02-11T01:00:00+00:00',
            evidence_refs=5,
        ),
        _sample(
            report_id='r2',
            ticker='000001.SZ',
            tier='TIER1',
            run_mode='SHADOW',
            score=35,
            created_at='2026-02-11T02:00:00+00:00',
            evidence_refs=2,
        ),
        _sample(
            report_id='r3',
            ticker='601318.SH',
            tier='TIER0',
            run_mode='LIVE',
            score=50,
            created_at='2026-02-11T03:00:00+00:00',
            evidence_refs=1,
        ),
    ]

    report = build_m7_offline_dataset(samples, lookback_days=30, dedupe_latest=False)
    assert report['effective_sample_size'] == 3
    assert report['dataset_version'].startswith('m7ds_')
    assert report['strata']['industry']
    assert report['strata']['market_regime']
    assert report['strata']['event_density']


def test_render_m7_dataset_markdown_contains_sections() -> None:
    report = build_m7_offline_dataset([], lookback_days=30)
    markdown = render_m7_dataset_markdown(report)
    assert '# M7 Offline Replay Dataset' in markdown
    assert '## Stratification Summary' in markdown
    assert '## Rows (Top 20)' in markdown
