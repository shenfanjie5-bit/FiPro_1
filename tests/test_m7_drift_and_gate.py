from __future__ import annotations

from app.eval.m7_drift import build_m7_drift_report, render_m7_drift_markdown
from app.eval.m7_model_gate import build_m7_model_gate, render_m7_model_gate_markdown


def test_build_m7_drift_report_warn_or_fail() -> None:
    baseline_rows = [
        {'industry': 'TECH', 'market_regime': 'UP', 'event_density': 'LOW', 'decision_action': 'WATCH', 'decision_confidence': 0.55}
        for _ in range(40)
    ]
    current_rows = [
        {'industry': 'ENERGY', 'market_regime': 'DOWN', 'event_density': 'HIGH', 'decision_action': 'BUY', 'decision_confidence': 0.9}
        for _ in range(40)
    ]
    report = build_m7_drift_report(baseline_rows, current_rows)
    assert report['overall_status'] in {'WARN', 'FAIL'}
    assert report['alerts']


def test_build_m7_model_gate_allow_and_block() -> None:
    offline_eval = {
        'tracks': {
            'CHALLENGER': {
                'schema_pass_rate': 1.0,
                'evidence_coverage_pass_rate': 0.95,
            }
        }
    }
    shadow_compare = {
        'summary': {
            'paired_sample_size': 30,
            'challenger_quality_win_rate': 0.6,
            'avg_cost_delta_usd': 0.03,
            'avg_latency_delta_ms': 120,
        }
    }
    drift = {'dimensions': [], 'overall_status': 'PASS'}
    allow_report = build_m7_model_gate(offline_eval, shadow_compare, drift_report=drift)
    assert allow_report['decision'] == 'ALLOW'

    bad_shadow_compare = {
        'summary': {
            'paired_sample_size': 5,
            'challenger_quality_win_rate': 0.2,
            'avg_cost_delta_usd': 0.3,
            'avg_latency_delta_ms': 2400,
        }
    }
    bad_drift = {'dimensions': [{'status': 'CRITICAL'}], 'overall_status': 'FAIL'}
    block_report = build_m7_model_gate(offline_eval, bad_shadow_compare, drift_report=bad_drift)
    assert block_report['decision'] == 'BLOCK'


def test_render_markdown_contains_sections() -> None:
    drift_report = build_m7_drift_report([], [])
    drift_md = render_m7_drift_markdown(drift_report)
    assert '# M7 Drift Monitor' in drift_md

    gate_md = render_m7_model_gate_markdown(
        {
            'generated_at': '2026-02-11T00:00:00+00:00',
            'decision': 'BLOCK',
            'candidate_model': 'c1',
            'current_model': 'p1',
            'checks': [],
            'failed_checks': [],
            'rollout_strategy': {},
        }
    )
    assert '# M7 Model Switch Gate' in gate_md
