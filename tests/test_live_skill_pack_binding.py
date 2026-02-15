from __future__ import annotations

import app.workflows.nodes as nodes_module


def _mock_skill_pack(skill_pack_id: str, version: str) -> dict:
    return {
        'summary': {
            'skill_pack_id': skill_pack_id,
            'version': version,
            'market': 'CN_A',
            'status': 'champion',
            'factor_count': 1,
            'enabled_factor_count': 1,
            'zero_weight_factor_count': 0,
            'llm_mapping_count': 1,
        },
        'factors': {
            'factors': [
                {
                    'factor_id': 'price.momentum_20d',
                    'enabled': True,
                    'weight': 0.18,
                }
            ]
        },
    }


def test_load_strategy_config_live_forces_champion(monkeypatch) -> None:
    monkeypatch.setattr(nodes_module, 'resolve_champion_version', lambda skill_pack_id: '0.9.0')  # noqa: ARG005
    monkeypatch.setattr(nodes_module, 'load_skill_pack', _mock_skill_pack)
    monkeypatch.setattr(
        nodes_module,
        'get_tushare_registry_for_context',
        lambda max_endpoints=260: {  # noqa: ARG005
            'source_id': 'TUSHARE',
            'source_name': 'TUSHARE',
            'total_endpoints': 0,
            'group_counts': {},
            'domain_counts': {},
            'endpoints': [],
            'entries': [],
        },
    )

    state = {
        'request': {
            'ticker': '600519.SH',
            'market': 'CN_A',
            'asof': '2026-02-13T09:30:00+08:00',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'run_mode': 'LIVE',
            'skill_pack_id': 'cn_a_core',
            'skill_pack_version': '0.1.0',
        }
    }
    updated = nodes_module.load_strategy_config(state)
    assert updated['request']['skill_pack_version'] == '0.9.0'
    assert updated['request']['skill_pack_version_source'] == 'forced_champion'
    assert 'LIVE mode ignores explicit skill_pack_version=0.1.0' in updated['config']['skill_pack_warning']


def test_load_strategy_config_backtest_keeps_explicit_version(monkeypatch) -> None:
    monkeypatch.setattr(nodes_module, 'resolve_champion_version', lambda skill_pack_id: '0.9.0')  # noqa: ARG005
    monkeypatch.setattr(nodes_module, 'load_skill_pack', _mock_skill_pack)
    monkeypatch.setattr(
        nodes_module,
        'get_tushare_registry_for_context',
        lambda max_endpoints=260: {  # noqa: ARG005
            'source_id': 'TUSHARE',
            'source_name': 'TUSHARE',
            'total_endpoints': 0,
            'group_counts': {},
            'domain_counts': {},
            'endpoints': [],
            'entries': [],
        },
    )

    state = {
        'request': {
            'ticker': '600519.SH',
            'market': 'CN_A',
            'asof': '2026-02-13T09:30:00+08:00',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'run_mode': 'BACKTEST',
            'skill_pack_id': 'cn_a_core',
            'skill_pack_version': '0.1.0',
        }
    }
    updated = nodes_module.load_strategy_config(state)
    assert updated['request']['skill_pack_version'] == '0.1.0'
    assert updated['request']['skill_pack_version_source'] == 'explicit'


def test_live_blend_without_champion_falls_back_and_skips_blend(monkeypatch) -> None:
    monkeypatch.setattr(nodes_module, 'resolve_champion_version', lambda skill_pack_id: None)  # noqa: ARG005
    monkeypatch.setattr(
        nodes_module,
        'get_tushare_registry_for_context',
        lambda max_endpoints=260: {  # noqa: ARG005
            'source_id': 'TUSHARE',
            'source_name': 'TUSHARE',
            'total_endpoints': 0,
            'group_counts': {},
            'domain_counts': {},
            'endpoints': [],
            'entries': [],
        },
    )

    state = {
        'request': {
            'ticker': '600519.SH',
            'market': 'CN_A',
            'asof': '2026-02-13T09:30:00+08:00',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER1',
            'run_mode': 'LIVE',
            'analysis_mode': 'TA_HYBRID',
            'ta_hybrid_mode': 'BLEND',
            'skill_pack_id': 'cn_a_core',
            'skill_pack_version': '0.1.5',
        },
        'snapshots': {
            'get_market_snapshot': {'close': 100.0},
            'get_fundamentals_snapshot': {'roe': 0.12},
            'get_flow_sentiment_snapshot': {'sentiment': {'polarity': 0.1}},
        },
        'features': {
            'event_policy_signal': 0.3,
            'event_governance_signal': -0.1,
            'event_feature_meta': {'used_event_count': 2},
        },
        'score': {'overall_score': 58, 'confidence': 0.55},
        'score_id': 'score_before_blend',
        'price_bands': [],
        'context': {
            'features': {'event_policy_signal': 0.3, 'event_governance_signal': -0.1, 'event_feature_meta': {'used_event_count': 2}},
            'score': {'overall_score': 58, 'confidence': 0.55},
            'evidence_coverage': {'ok': True, 'actual_total_refs': 6},
            'data_quality': {'status': 'OK'},
            'evidence_refs': [],
        },
        'data_quality': {'status': 'OK'},
        'tool_traces': [],
    }

    updated = nodes_module.load_strategy_config(state)
    assert updated['request']['skill_pack_version'] == '0.1.0'
    assert updated['request']['skill_pack_version_source'] == 'forced_default'

    output = nodes_module.ta_hybrid_node(updated)
    ta_state = output['ta_hybrid_state']
    assert ta_state['mode'] == 'BLEND'
    assert ta_state['status'] == 'ANALYZED_NO_BLEND'
    assert ta_state['applied'] is False
    assert ta_state.get('missing_ta_factor_ids')
