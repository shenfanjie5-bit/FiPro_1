from __future__ import annotations

import json
from pathlib import Path
import shutil

from app.backtest.candidates import generate_skill_pack_candidates, generate_skill_pack_candidates_from_plans


def _seed_base_pack(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / 'skill_packs' / 'cn_a_core' / '0.1.0'
    target_root = tmp_path / 'skill_packs' / 'cn_a_core'
    shutil.copytree(source, target_root / '0.1.0')
    return tmp_path / 'skill_packs'


def test_generate_skill_pack_candidates_writes_new_versions(tmp_path: Path) -> None:
    root_dir = _seed_base_pack(tmp_path)
    result = generate_skill_pack_candidates(
        skill_pack_id='cn_a_core',
        base_version='0.1.0',
        calibration_version='0.1.0',
        max_candidates=2,
        author='unit_test',
        dry_run=False,
        root_dir=root_dir,
    )

    assert result['generated_count'] == 2
    assert result['items'][0]['version'] == '0.1.1'
    assert result['items'][1]['version'] == '0.1.2'

    version_dir = root_dir / 'cn_a_core' / '0.1.1'
    manifest = json.loads((version_dir / 'manifest.json').read_text(encoding='utf-8'))
    factors = json.loads((version_dir / 'factors.json').read_text(encoding='utf-8'))

    assert manifest['status'] == 'candidate'
    assert manifest['author'] == 'unit_test'
    assert manifest['derived_from_champion_version'] == '0.1.0'
    first_factor = factors['factors'][0]
    assert first_factor['factor_id'] == 'price.momentum_20d'
    assert first_factor['weight'] == 0.2


def test_generate_skill_pack_candidates_dry_run_does_not_write(tmp_path: Path) -> None:
    root_dir = _seed_base_pack(tmp_path)
    result = generate_skill_pack_candidates(
        skill_pack_id='cn_a_core',
        base_version='0.1.0',
        calibration_version='0.1.0',
        max_candidates=1,
        dry_run=True,
        root_dir=root_dir,
    )
    assert result['generated_count'] == 1
    assert result['items'][0]['version'] == '0.1.1'
    assert not (root_dir / 'cn_a_core' / '0.1.1').exists()


def test_generate_skill_pack_candidates_data_combo_only_zeroes_weights(tmp_path: Path) -> None:
    root_dir = _seed_base_pack(tmp_path)
    result = generate_skill_pack_candidates(
        skill_pack_id='cn_a_core',
        base_version='0.1.0',
        calibration_version='0.1.0',
        max_candidates=1,
        include_param_search=False,
        enable_data_combo_search=True,
        max_endpoint_toggles=1,
        endpoint_allowlist=['daily'],
        dry_run=False,
        root_dir=root_dir,
    )

    assert result['generated_count'] == 1
    assert result['items'][0]['plan_type'] == 'data_combo'
    assert result['items'][0]['mode'] == 'disable_endpoints'
    assert result['items'][0]['endpoints'] == ['daily']

    version_dir = root_dir / 'cn_a_core' / '0.1.1'
    manifest = json.loads((version_dir / 'manifest.json').read_text(encoding='utf-8'))
    factors = json.loads((version_dir / 'factors.json').read_text(encoding='utf-8'))
    by_id = {item['factor_id']: item for item in factors['factors']}

    assert by_id['price.momentum_20d']['weight'] == 0.0
    assert by_id['price.momentum_60d']['weight'] == 0.0
    assert by_id['price.volatility_20d']['weight'] == 0.0
    assert by_id['flow.moneyflow_5d']['weight'] == 0.14
    assert manifest['candidate_plan']['plan_type'] == 'data_combo'
    assert manifest['candidate_plan']['endpoints'] == ['daily']


def test_generate_skill_pack_candidates_from_plans_supports_append_op(tmp_path: Path) -> None:
    root_dir = _seed_base_pack(tmp_path)
    result = generate_skill_pack_candidates_from_plans(
        skill_pack_id='cn_a_core',
        base_version='0.1.0',
        plans=[
            {
                'plan_type': 'llm_proposal',
                'plan_id': 'prop_append_rule',
                'description': 'append one policy rule and tweak one factor weight',
                'changes': [
                    {
                        'op': 'set',
                        'path': 'factors.factors[price.momentum_20d].weight',
                        'to': 0.22,
                    },
                    {
                        'op': 'append',
                        'path': 'policy.rules',
                        'to': {
                            'rule_id': 'llm_rule_001',
                            'scope': 'has_position',
                            'when': 'final_score < 42',
                            'action': 'REDUCE',
                            'priority': 64,
                        },
                    },
                ],
            }
        ],
        author='llm_unit_test',
        dry_run=False,
        root_dir=root_dir,
        default_job_namespace='llm_proposal',
        default_job_profile_id='run_demo',
    )

    assert result['generated_count'] == 1
    assert result['items'][0]['version'] == '0.1.1'

    version_dir = root_dir / 'cn_a_core' / '0.1.1'
    policy = json.loads((version_dir / 'policy.json').read_text(encoding='utf-8'))
    factors = json.loads((version_dir / 'factors.json').read_text(encoding='utf-8'))
    manifest = json.loads((version_dir / 'manifest.json').read_text(encoding='utf-8'))

    assert factors['factors'][0]['weight'] == 0.22
    assert any(item.get('rule_id') == 'llm_rule_001' for item in policy['rules'])
    assert manifest['derived_from_job_id'] == 'llm_proposal:run_demo:prop_append_rule'
