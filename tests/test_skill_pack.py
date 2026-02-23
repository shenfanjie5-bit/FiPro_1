from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.skill_pack import clear_skill_pack_cache, load_skill_pack


def test_load_default_skill_pack() -> None:
    payload = load_skill_pack('cn_a_core', '0.1.0')
    summary = payload.get('summary', {})
    assert summary['skill_pack_id'] == 'cn_a_core'
    assert summary['version'] == '0.1.0'
    assert summary['market'] == 'CN_A'
    assert summary['factor_count'] >= 8
    assert summary['zero_weight_factor_count'] >= 1


def test_skill_pack_llm_mapping_targets_existing_factor_ids() -> None:
    payload = load_skill_pack('cn_a_core', '0.1.0')
    factor_ids = {
        str(item.get('factor_id'))
        for item in payload['factors'].get('factors', [])
        if isinstance(item, dict) and item.get('factor_id')
    }
    for item in payload['llm_mapping'].get('mappings', []):
        assert item['target_factor'] in factor_ids


def test_skill_pack_validation_rejects_mismatched_manifest(tmp_path: Path) -> None:
    pack_dir = tmp_path / 'cn_a_core' / '0.1.0'
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        'skill_pack_id': 'wrong_pack',
        'version': '0.1.0',
        'market': 'CN_A',
        'author': 'tester',
        'status': 'candidate',
        'inputs': {
            'manifest_file': 'manifest.json',
            'factors_file': 'factors.json',
            'formula_file': 'formula.json',
            'policy_file': 'policy.json',
            'risk_file': 'risk.json',
            'llm_mapping_file': 'llm_mapping.json',
            'gate_file': 'gate.json',
        },
    }
    factors = {'factors': [{'factor_id': 'event.policy_signal', 'enabled': True, 'weight': 0.1}]}
    formula = {'score_formula': {'clamp': [0, 100]}, 'confidence_formula': {'clamp': [0, 1]}}
    policy = {'actions': ['BUY', 'ADD', 'HOLD', 'REDUCE', 'SELL', 'AVOID'], 'rules': [{'rule_id': 'r1'}]}
    risk = {'constraints': {'max_single_position': 0.5, 'max_drawdown_pct': 10.0}}
    llm_mapping = {'mappings': [{'event_type': 'policy', 'target_factor': 'event.policy_signal'}]}
    gate = {'required_metrics': ['excess_return_pct'], 'promotion_rule': {'all_of': [{'metric': 'excess_return_pct'}]}}

    (pack_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (pack_dir / 'factors.json').write_text(json.dumps(factors), encoding='utf-8')
    (pack_dir / 'formula.json').write_text(json.dumps(formula), encoding='utf-8')
    (pack_dir / 'policy.json').write_text(json.dumps(policy), encoding='utf-8')
    (pack_dir / 'risk.json').write_text(json.dumps(risk), encoding='utf-8')
    (pack_dir / 'llm_mapping.json').write_text(json.dumps(llm_mapping), encoding='utf-8')
    (pack_dir / 'gate.json').write_text(json.dumps(gate), encoding='utf-8')

    clear_skill_pack_cache()
    with pytest.raises(ValueError, match='manifest.skill_pack_id'):
        load_skill_pack('cn_a_core', '0.1.0', root_dir=tmp_path)


def test_skill_pack_validation_rejects_component_version_mismatch(tmp_path: Path) -> None:
    pack_dir = tmp_path / 'cn_a_core' / '0.1.1'
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        'skill_pack_id': 'cn_a_core',
        'version': '0.1.1',
        'market': 'CN_A',
        'author': 'tester',
        'status': 'candidate',
        'inputs': {
            'manifest_file': 'manifest.json',
            'factors_file': 'factors.json',
            'formula_file': 'formula.json',
            'policy_file': 'policy.json',
            'risk_file': 'risk.json',
            'llm_mapping_file': 'llm_mapping.json',
            'gate_file': 'gate.json',
        },
    }
    factors = {'version': '0.1.0', 'factors': [{'factor_id': 'event.policy_signal', 'enabled': True, 'weight': 0.1}]}
    formula = {'version': '0.1.1', 'score_formula': {'clamp': [0, 100]}, 'confidence_formula': {'clamp': [0, 1]}}
    policy = {'version': '0.1.1', 'actions': ['BUY', 'ADD', 'HOLD', 'REDUCE', 'SELL', 'AVOID'], 'rules': [{'rule_id': 'r1'}]}
    risk = {'version': '0.1.1', 'constraints': {'max_single_position': 0.5, 'max_drawdown_pct': 10.0}}
    llm_mapping = {'version': '0.1.1', 'mappings': [{'event_type': 'policy', 'target_factor': 'event.policy_signal'}]}
    gate = {'version': '0.1.1', 'required_metrics': ['excess_return_pct'], 'promotion_rule': {'all_of': [{'metric': 'excess_return_pct'}]}}

    (pack_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (pack_dir / 'factors.json').write_text(json.dumps(factors), encoding='utf-8')
    (pack_dir / 'formula.json').write_text(json.dumps(formula), encoding='utf-8')
    (pack_dir / 'policy.json').write_text(json.dumps(policy), encoding='utf-8')
    (pack_dir / 'risk.json').write_text(json.dumps(risk), encoding='utf-8')
    (pack_dir / 'llm_mapping.json').write_text(json.dumps(llm_mapping), encoding='utf-8')
    (pack_dir / 'gate.json').write_text(json.dumps(gate), encoding='utf-8')

    clear_skill_pack_cache()
    with pytest.raises(ValueError, match='factors\\.json\\.version=0\\.1\\.0 does not match manifest\\.version=0\\.1\\.1'):
        load_skill_pack('cn_a_core', '0.1.1', root_dir=tmp_path)
