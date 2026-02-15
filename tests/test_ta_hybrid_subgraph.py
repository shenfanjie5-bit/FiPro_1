from __future__ import annotations

import app.llm.provider as provider_module
import app.workflows.subgraphs.ta_hybrid as ta_subgraph_module
from app.workflows.subgraphs.ta_hybrid import run_ta_hybrid_subgraph


def _request() -> dict:
    return {
        'ticker': '600519.SH',
        'asof': '2026-02-10T09:30:00+08:00',
        'tier': 'TIER1',
        'run_mode': 'LIVE',
        'analysis_mode': 'TA_HYBRID',
        'ta_hybrid_mode': 'ANALYZE_ONLY',
        'ta_require_evidence_refs': True,
    }


def _context() -> dict:
    return {
        'features': {
            'event_policy_signal': 0.2,
            'event_governance_signal': -0.1,
            'event_feature_meta': {'used_event_count': 2},
        },
        'score': {'confidence': 0.62},
        'evidence_coverage': {'ok': True, 'actual_total_refs': 6},
        'data_quality': {'status': 'OK'},
    }


def test_ta_hybrid_subgraph_contract_mock_mode() -> None:
    output = run_ta_hybrid_subgraph(
        request=_request(),
        context=_context(),
        ta_research_rounds=2,
        ta_risk_rounds=2,
        ta_llm_call_cap=6,
    )
    state = output['state']
    signal = output['signal']
    views = output['views']
    assert state['mode'] == 'ANALYZE_ONLY'
    assert state['status'] == 'ANALYZED'
    assert state['applied'] is False
    assert state['research_rounds_used'] == 2
    assert state['risk_rounds_used'] == 2
    assert state['llm_calls_used'] == 0
    assert -1.0 <= signal['directional_bias'] <= 1.0
    assert -1.0 <= signal['risk_bias'] <= 1.0
    assert 0.0 <= signal['conviction'] <= 1.0
    assert 0.0 <= signal['disagreement'] <= 1.0
    assert 1 <= signal['horizon_days_hint'] <= 120
    assert set(views) == {
        'bull',
        'bear',
        'research_judge',
        'risk_aggressive',
        'risk_conservative',
        'risk_neutral',
        'risk_judge',
    }
    assert len(output['evidence_refs']) >= 2


def test_ta_hybrid_subgraph_degrades_when_view_generation_fails(monkeypatch) -> None:
    original = provider_module.LLMProvider.generate_ta_hybrid_view

    def _boom(self, *, stage, role, ta_input, upstream=None, round_idx=1):  # noqa: ANN001, ARG001
        if stage == 'research' and role == 'bull':
            raise RuntimeError('forced failure')
        return original(self, stage=stage, role=role, ta_input=ta_input, upstream=upstream, round_idx=round_idx)

    monkeypatch.setattr(provider_module.LLMProvider, 'generate_ta_hybrid_view', _boom)
    output = run_ta_hybrid_subgraph(
        request=_request(),
        context=_context(),
        ta_research_rounds=1,
        ta_risk_rounds=1,
        ta_llm_call_cap=6,
    )
    reasons = output['state']['degraded_reasons']
    assert any('research.bull degraded' in reason for reason in reasons)
    assert output['state']['status'] == 'ANALYZED'
    assert output['signal']


def test_ta_hybrid_subgraph_provider_uses_runtime_primary_model(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_runtime_config() -> dict[str, str]:
        return {
            'llm_primary_model': 'runtime-primary-model',
            'llm_reviewer_model': 'runtime-reviewer-model',
        }

    class FakeProvider:
        provider = 'openai_compatible'

        def __init__(self, primary_model: str = 'mock-primary', reviewer_model: str = 'NONE') -> None:
            captured['primary_model'] = primary_model
            captured['reviewer_model'] = reviewer_model

        def generate_ta_hybrid_view(self, **kwargs):  # noqa: ANN001
            _ = kwargs
            return {
                'summary': 'ok',
                'stance': 'NEUTRAL',
                'directional_bias': 0.0,
                'risk_bias': 0.0,
                'conviction': 0.5,
                'disagreement': 0.1,
                'horizon_days_hint': 7,
                'rationale_points': ['ok'],
            }

    monkeypatch.setattr(ta_subgraph_module, 'get_runtime_config', fake_runtime_config)
    monkeypatch.setattr(ta_subgraph_module, 'LLMProvider', FakeProvider)
    output = run_ta_hybrid_subgraph(
        request=_request(),
        context=_context(),
        ta_research_rounds=1,
        ta_risk_rounds=1,
        ta_llm_call_cap=2,
    )
    assert output['state']['llm_calls_used'] == 2
    assert captured['primary_model'] == 'runtime-primary-model'
    assert captured['reviewer_model'] == 'runtime-reviewer-model'
