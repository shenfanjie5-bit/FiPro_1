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
