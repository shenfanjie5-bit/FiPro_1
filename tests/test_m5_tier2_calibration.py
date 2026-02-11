from __future__ import annotations

from app.eval.m4_baseline import ReportSample
from app.eval.m5_tier2_calibration import build_m5_tier2_calibration


def _sample(
    *,
    report_id: str,
    tool_calls: int,
    cost_usd: float,
    latency_ms: int,
    created_at: str = '2026-02-11T00:00:00+00:00',
    asof: str = '2026-02-10T09:30:00+08:00',
) -> ReportSample:
    return ReportSample(
        report_id=report_id,
        tier='TIER2',
        created_at=created_at,
        report_json={
            'ticker': '600519.SH',
            'asof': asof,
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER2',
            'provenance': {'run_mode': 'LIVE'},
        },
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )


def test_m5_tier2_calibration_passes_within_budget() -> None:
    samples = [
        _sample(report_id='r1', tool_calls=35, cost_usd=0.8, latency_ms=2200, created_at='2026-02-11T00:00:00+00:00', asof='2026-02-10T09:30:00+08:00'),
        _sample(report_id='r2', tool_calls=42, cost_usd=1.2, latency_ms=3100, created_at='2026-02-11T01:00:00+00:00', asof='2026-02-10T09:31:00+08:00'),
        _sample(report_id='r3', tool_calls=38, cost_usd=1.0, latency_ms=2800, created_at='2026-02-11T02:00:00+00:00', asof='2026-02-10T09:32:00+08:00'),
        _sample(report_id='r4', tool_calls=40, cost_usd=0.9, latency_ms=2600, created_at='2026-02-11T03:00:00+00:00', asof='2026-02-10T09:33:00+08:00'),
        _sample(report_id='r5', tool_calls=44, cost_usd=1.1, latency_ms=3400, created_at='2026-02-11T04:00:00+00:00', asof='2026-02-10T09:34:00+08:00'),
        _sample(report_id='r6', tool_calls=39, cost_usd=1.0, latency_ms=2500, created_at='2026-02-11T05:00:00+00:00', asof='2026-02-10T09:35:00+08:00'),
        _sample(report_id='r7', tool_calls=36, cost_usd=0.95, latency_ms=2400, created_at='2026-02-11T06:00:00+00:00', asof='2026-02-10T09:36:00+08:00'),
        _sample(report_id='r8', tool_calls=41, cost_usd=1.05, latency_ms=3000, created_at='2026-02-11T07:00:00+00:00', asof='2026-02-10T09:37:00+08:00'),
    ]

    report = build_m5_tier2_calibration(samples, lookback_days=14, dedupe_latest=True)
    assert report['overall_status'] == 'PASS'
    assert report['summary']['sample_size'] == 8
    assert report['summary']['violation_rates']['any'] == 0


def test_m5_tier2_calibration_fails_when_budget_violations_are_high() -> None:
    samples = [
        _sample(report_id='f1', tool_calls=120, cost_usd=3.4, latency_ms=18000, created_at='2026-02-11T00:00:00+00:00', asof='2026-02-10T10:30:00+08:00'),
        _sample(report_id='f2', tool_calls=118, cost_usd=3.1, latency_ms=16500, created_at='2026-02-11T01:00:00+00:00', asof='2026-02-10T10:31:00+08:00'),
        _sample(report_id='f3', tool_calls=116, cost_usd=2.9, latency_ms=16000, created_at='2026-02-11T02:00:00+00:00', asof='2026-02-10T10:32:00+08:00'),
        _sample(report_id='f4', tool_calls=114, cost_usd=3.0, latency_ms=15000, created_at='2026-02-11T03:00:00+00:00', asof='2026-02-10T10:33:00+08:00'),
        _sample(report_id='f5', tool_calls=112, cost_usd=2.8, latency_ms=14800, created_at='2026-02-11T04:00:00+00:00', asof='2026-02-10T10:34:00+08:00'),
        _sample(report_id='f6', tool_calls=111, cost_usd=2.7, latency_ms=14600, created_at='2026-02-11T05:00:00+00:00', asof='2026-02-10T10:35:00+08:00'),
        _sample(report_id='f7', tool_calls=113, cost_usd=2.9, latency_ms=15200, created_at='2026-02-11T06:00:00+00:00', asof='2026-02-10T10:36:00+08:00'),
        _sample(report_id='f8', tool_calls=115, cost_usd=3.2, latency_ms=17000, created_at='2026-02-11T07:00:00+00:00', asof='2026-02-10T10:37:00+08:00'),
    ]

    report = build_m5_tier2_calibration(samples, lookback_days=14, dedupe_latest=True)
    assert report['overall_status'] == 'FAIL'
    assert report['summary']['violation_rates']['any'] > 0.05
    assert any(item['metric'] == 'budget_violation_rate' and item['pass'] is False for item in report['threshold_checks'])
