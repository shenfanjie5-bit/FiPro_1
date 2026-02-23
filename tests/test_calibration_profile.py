from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.calibration import load_calibration_profile


def test_load_default_calibration_profile() -> None:
    payload = load_calibration_profile('cn_a_core', '0.1.0')
    summary = payload.get('_summary', {})
    assert summary['profile_id'] == 'cn_a_core_calibration_v0_1_0'
    assert summary['skill_pack_id'] == 'cn_a_core'
    assert summary['skill_pack_version'] == '0.1.0'
    assert summary['search_space_count'] >= 1
    assert summary['numeric_param_count'] >= 1


def test_load_calibration_profile_rejects_mismatch(tmp_path: Path) -> None:
    pack_dir = tmp_path / 'cn_a_core' / '0.1.0'
    pack_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        'profile_id': 'p1',
        'skill_pack_id': 'wrong_pack',
        'skill_pack_version': '0.1.0',
        'objective': {'primary': 'excess_return_delta_pct'},
        'search_space': [
            {
                'param_id': 'x',
                'path': 'policy.thresholds.buy_score_min',
                'type': 'float',
                'current': 72.0,
                'min': 68.0,
                'max': 80.0,
                'step': 1.0,
            }
        ],
        'execution': {'tier': 'TIER0'},
    }
    (pack_dir / 'calibration.json').write_text(json.dumps(payload), encoding='utf-8')

    with pytest.raises(ValueError, match='calibration.skill_pack_id'):
        load_calibration_profile('cn_a_core', '0.1.0', root_dir=tmp_path)
