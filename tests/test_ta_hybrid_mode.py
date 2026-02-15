from __future__ import annotations

from app.backtest.skill_pack import load_skill_pack
from app.workflows.graph import _route_after_context
from app.workflows.nodes import ta_hybrid_node


def test_route_after_context_defaults_to_direct_draft() -> None:
    state = {
        'request': {},
        'budget': {'max_tool_calls': 20, 'used_tool_calls': 0, 'degraded': False},
    }
    assert _route_after_context(state) == 'direct_draft'


def test_route_after_context_ta_hybrid_enabled() -> None:
    state = {
        'request': {'analysis_mode': 'TA_HYBRID', 'ta_hybrid_mode': 'ANALYZE_ONLY'},
        'budget': {'max_tool_calls': 20, 'used_tool_calls': 5, 'degraded': False},
    }
    assert _route_after_context(state) == 'ta_hybrid_chain'


def test_ta_hybrid_node_populates_analyze_only_payload() -> None:
    state = {
        'request': {
            'ticker': '600519.SH',
            'asof': '2026-02-10T09:30:00+08:00',
            'tier': 'TIER1',
            'analysis_mode': 'TA_HYBRID',
            'ta_hybrid_mode': 'ANALYZE_ONLY',
            'ta_research_rounds': 1,
            'ta_risk_rounds': 1,
            'ta_llm_call_cap': 6,
        },
        'context': {
            'features': {'event_policy_signal': 0.2, 'event_governance_signal': -0.1, 'event_feature_meta': {'used_event_count': 2}},
            'score': {'confidence': 0.62},
            'evidence_coverage': {'ok': True, 'actual_total_refs': 6},
            'data_quality': {'status': 'OK'},
            'evidence_refs': [],
        },
    }

    output = ta_hybrid_node(state)

    ta_state = output['ta_hybrid_state']
    assert ta_state['mode'] == 'ANALYZE_ONLY'
    assert ta_state['status'] == 'ANALYZED'
    assert ta_state['applied'] is False
    assert output['context']['ta_hybrid']['state']['version'] == 'ta_hybrid_m2_v1'
    assert output['context']['ta_hybrid']['signal']['horizon_days_hint'] in {5, 7, 10}
    assert any(item.get('type') == 'AGENT_REASONING' for item in output['context']['ta_hybrid']['evidence_refs'])


def test_ta_hybrid_node_blend_applies_deterministic_rescore() -> None:
    state = {
        'request': {
            'ticker': '600519.SH',
            'asof': '2026-02-10T09:30:00+08:00',
            'tier': 'TIER1',
            'analysis_mode': 'TA_HYBRID',
            'ta_hybrid_mode': 'BLEND',
            'ta_research_rounds': 1,
            'ta_risk_rounds': 1,
            'ta_llm_call_cap': 6,
            'ta_require_evidence_refs': True,
        },
        'skill_pack': load_skill_pack(skill_pack_id='cn_a_core', version='0.1.5'),
        'snapshots': {
            'get_market_snapshot': {'close': 100.0},
            'get_fundamentals_snapshot': {'roe': 0.12},
            'get_flow_sentiment_snapshot': {'sentiment': {'polarity': 0.1}},
        },
        'features': {
            'event_policy_signal': 0.4,
            'event_governance_signal': -0.2,
            'evidence_coverage': 0.85,
            'staleness': 0.1,
            'event_feature_meta': {'used_event_count': 2},
        },
        'score': {'overall_score': 58, 'confidence': 0.55},
        'score_id': 'score_before_blend',
        'price_bands': [],
        'context': {
            'features': {'event_policy_signal': 0.4, 'event_governance_signal': -0.2, 'event_feature_meta': {'used_event_count': 2}},
            'score': {'overall_score': 58, 'confidence': 0.55},
            'evidence_coverage': {'ok': True, 'actual_total_refs': 6},
            'data_quality': {'status': 'OK'},
            'evidence_refs': [],
        },
        'data_quality': {'status': 'OK'},
        'tool_traces': [],
    }

    output = ta_hybrid_node(state)

    ta_state = output['ta_hybrid_state']
    assert ta_state['mode'] == 'BLEND'
    assert ta_state['status'] == 'BLENDED'
    assert ta_state['applied'] is True
    assert 'ta_research_bias' in output['features']
    assert 'ta_risk_bias' in output['features']
    assert 'ta_disagreement_penalty' in output['features']
    assert 'ta_conviction_support' in output['features']
    assert output['score_id'] != 'score_before_blend'
    assert output['price_bands']


def test_ta_hybrid_node_blend_requires_ta_factors_in_skill_pack() -> None:
    state = {
        'request': {
            'ticker': '600519.SH',
            'asof': '2026-02-10T09:30:00+08:00',
            'tier': 'TIER1',
            'analysis_mode': 'TA_HYBRID',
            'ta_hybrid_mode': 'BLEND',
            'ta_research_rounds': 1,
            'ta_risk_rounds': 1,
            'ta_llm_call_cap': 6,
            'ta_require_evidence_refs': True,
        },
        'skill_pack': load_skill_pack(skill_pack_id='cn_a_core', version='0.1.0'),
        'snapshots': {
            'get_market_snapshot': {'close': 100.0},
            'get_fundamentals_snapshot': {'roe': 0.12},
            'get_flow_sentiment_snapshot': {'sentiment': {'polarity': 0.1}},
        },
        'features': {
            'event_policy_signal': 0.4,
            'event_governance_signal': -0.2,
            'evidence_coverage': 0.85,
            'staleness': 0.1,
            'event_feature_meta': {'used_event_count': 2},
        },
        'score': {'overall_score': 58, 'confidence': 0.55},
        'score_id': 'score_before_blend',
        'price_bands': [],
        'context': {
            'features': {'event_policy_signal': 0.4, 'event_governance_signal': -0.2, 'event_feature_meta': {'used_event_count': 2}},
            'score': {'overall_score': 58, 'confidence': 0.55},
            'evidence_coverage': {'ok': True, 'actual_total_refs': 6},
            'data_quality': {'status': 'OK'},
            'evidence_refs': [],
        },
        'data_quality': {'status': 'OK'},
        'tool_traces': [],
    }

    output = ta_hybrid_node(state)

    ta_state = output['ta_hybrid_state']
    assert ta_state['mode'] == 'BLEND'
    assert ta_state['status'] == 'ANALYZED_NO_BLEND'
    assert ta_state['applied'] is False
    assert ta_state.get('missing_ta_factor_ids')
    assert output['score_id'] == 'score_before_blend'
